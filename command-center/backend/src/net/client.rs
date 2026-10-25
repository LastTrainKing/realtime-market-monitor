//! HTTP 客户端（第 4.5 / 4.6 / 8.6 / 12.8 章）。
//!
//! 全部外部请求的唯一出口，统一提供：
//!   * 全局限流（RateLimiter）
//!   * 并发去重（同一 URL 短时间只发一次请求）
//!   * 指数退避重试（1s → 2s → 4s → 8s）
//!   * 熔断（按主机）
//!   * 主机轮换（同一接口多镜像自动切换）
//!   * 统计与健康度

use dashmap::DashMap;
use parking_lot::RwLock;
use reqwest::header::{HeaderMap, HeaderValue, ACCEPT, ACCEPT_LANGUAGE, REFERER, USER_AGENT};
use serde::de::DeserializeOwned;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

use crate::config::NetConfig;
use crate::models::{HostHealth, NetStats};

use super::circuit_breaker::{BreakerState, CircuitBreaker};
use super::error::NetError;
use super::rate_limiter::{RateLimiter, RateLimiterConfig};

const UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \
(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

type Cell = Arc<Mutex<Option<Result<Arc<[u8]>, NetError>>>>;

#[derive(Default)]
struct Counters {
    total: AtomicU64,
    success: AtomicU64,
    failed: AtomicU64,
    deduped: AtomicU64,
    circuit_blocked: AtomicU64,
    latency_sum_ms: AtomicU64,
    latency_samples: AtomicU64,
}

pub struct HttpClient {
    client: reqwest::Client,
    limiter: Arc<RateLimiter>,
    breakers: DashMap<String, Arc<CircuitBreaker>>,
    inflight: DashMap<String, Cell>,
    counters: Counters,
    host_stats: DashMap<String, Arc<HostStat>>,
    ip_protection: AtomicBool,
    throttle_events: AtomicU64,
    cfg: RwLock<NetConfig>,
}

#[derive(Default)]
struct HostStat {
    success: AtomicU64,
    failure: AtomicU64,
    latency_sum_ms: AtomicU64,
    samples: AtomicU64,
    last_error: RwLock<String>,
}

impl HostStat {
    fn record_ok(&self, ms: u64) {
        self.success.fetch_add(1, Ordering::Relaxed);
        self.latency_sum_ms.fetch_add(ms, Ordering::Relaxed);
        self.samples.fetch_add(1, Ordering::Relaxed);
        self.last_error.write().clear();
    }
    fn record_err(&self, msg: &str) {
        self.failure.fetch_add(1, Ordering::Relaxed);
        *self.last_error.write() = msg.to_string();
    }
}

impl HttpClient {
    pub fn new(cfg: &NetConfig) -> anyhow::Result<Self> {
        let mut headers = HeaderMap::new();
        headers.insert(USER_AGENT, HeaderValue::from_static(UA));
        headers.insert(
            ACCEPT,
            HeaderValue::from_static("application/json, text/javascript, */*; q=0.01"),
        );
        headers.insert(ACCEPT_LANGUAGE, HeaderValue::from_static("zh-CN,zh;q=0.9"));
        headers.insert("Connection", HeaderValue::from_static("keep-alive"));

        let mut builder = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(Duration::from_secs(cfg.timeout_secs.max(3)))
            .connect_timeout(Duration::from_secs(cfg.connect_timeout_secs.max(2)))
            .pool_max_idle_per_host(8)
            .tcp_keepalive(Duration::from_secs(30))
            .no_proxy();

        if let Some(proxy) = cfg.proxy.as_ref().filter(|p| !p.trim().is_empty()) {
            builder = builder.proxy(reqwest::Proxy::all(proxy.trim())?);
        }

        let client = builder.build()?;
        Ok(Self {
            client,
            limiter: Arc::new(RateLimiter::new(RateLimiterConfig {
                per_second: cfg.requests_per_second.max(1),
                per_minute: cfg.requests_per_minute.max(1),
                min_interval: Duration::from_millis(cfg.min_interval_ms),
                max_concurrent: cfg.max_concurrent.max(1),
            })),
            breakers: DashMap::new(),
            inflight: DashMap::new(),
            counters: Counters::default(),
            host_stats: DashMap::new(),
            ip_protection: AtomicBool::new(false),
            throttle_events: AtomicU64::new(0),
            cfg: RwLock::new(cfg.clone()),
        })
    }

    pub fn limiter(&self) -> &Arc<RateLimiter> {
        &self.limiter
    }

    pub fn config(&self) -> NetConfig {
        self.cfg.read().clone()
    }

    pub fn update_config(&self, cfg: &NetConfig) {
        *self.cfg.write() = cfg.clone();
    }

    fn breaker_for(&self, host: &str) -> Arc<CircuitBreaker> {
        let cfg = self.cfg.read().clone();
        self.breakers
            .entry(host.to_string())
            .or_insert_with(|| {
                Arc::new(CircuitBreaker::new(
                    cfg.breaker_failure_threshold,
                    Duration::from_secs(cfg.breaker_cooldown_secs),
                ))
            })
            .clone()
    }

    fn host_stat(&self, host: &str) -> Arc<HostStat> {
        self.host_stats
            .entry(host.to_string())
            .or_insert_with(|| Arc::new(HostStat::default()))
            .clone()
    }

    pub fn is_ip_protected(&self) -> bool {
        self.ip_protection.load(Ordering::Relaxed)
    }

    /// 收到 IP 风险信号时进入保护模式（第 8.5 章）
    fn note_ip_risk(&self, err: &NetError) {
        if err.is_ip_risk() {
            self.limiter.throttle_up();
            self.ip_protection.store(true, Ordering::Relaxed);
            self.throttle_events.fetch_add(1, Ordering::Relaxed);
            tracing::warn!(
                target: "network",
                kind = err.kind(),
                throttle = self.limiter.throttle_factor(),
                "检测到数据源限制信号，已进入降频保护模式"
            );
        }
    }

    fn note_recovery(&self) {
        if self.ip_protection.load(Ordering::Relaxed) {
            let factor = self.limiter.throttle_factor();
            if factor > 1.0 {
                self.limiter.throttle_reset();
            }
            self.ip_protection.store(false, Ordering::Relaxed);
            tracing::info!(target: "network", "数据源恢复正常，已退出降频保护模式");
        }
    }

    /// 单一 URL 请求（带并发去重 + 重试 + 熔断），返回原始字节
    pub async fn get_bytes(
        &self,
        url: &str,
        referer: Option<&str>,
    ) -> Result<Arc<[u8]>, NetError> {
        self.get_bytes_inner(url, referer, true).await
    }

    async fn get_bytes_inner(
        &self,
        url: &str,
        referer: Option<&str>,
        allow_dedup: bool,
    ) -> Result<Arc<[u8]>, NetError> {
        let key = url.to_string();

        if !allow_dedup {
            return self.fetch_with_retry(url, referer).await;
        }

        let cell: Cell = self
            .inflight
            .entry(key.clone())
            .or_insert_with(|| Arc::new(Mutex::new(None)))
            .clone();

        match cell.try_lock() {
            Ok(mut guard) => {
                let result = self.fetch_with_retry(url, referer).await;
                if result.is_ok() {
                    *guard = Some(result.clone());
                }
                drop(guard);
                self.inflight.remove(&key);
                result
            }
            Err(_) => {
                // 并发合并：等待首个请求完成，直接复用结果
                let guard = cell.lock().await;
                self.counters.deduped.fetch_add(1, Ordering::Relaxed);
                match guard.as_ref() {
                    Some(Ok(body)) => Ok(body.clone()),
                    Some(Err(err)) => Err(err.clone()),
                    None => Err(NetError::Coalesced(url.to_string())),
                }
            }
        }
    }

    /// UTF-8 文本请求（JSON 接口）
    pub async fn get_text(&self, url: &str, referer: Option<&str>) -> Result<String, NetError> {
        let bytes = self.get_bytes(url, referer).await?;
        Ok(String::from_utf8_lossy(&bytes).into_owned())
    }

    /// GBK / GB18030 文本请求（腾讯、新浪行情接口使用 GBK 编码）
    pub async fn get_text_gbk(&self, url: &str, referer: Option<&str>) -> Result<String, NetError> {
        let bytes = self.get_bytes(url, referer).await?;
        let (decoded, _, _) = encoding_rs::GB18030.decode(&bytes);
        Ok(decoded.into_owned())
    }

    /// 自动编码识别：优先 UTF-8，失败回退 GB18030
    pub async fn get_text_auto(&self, url: &str, referer: Option<&str>) -> Result<String, NetError> {
        let bytes = self.get_bytes(url, referer).await?;
        Ok(decode_auto(&bytes))
    }

    /// 主机轮换：依次尝试多个镜像主机，任一成功即返回
    pub async fn get_text_rotating(
        &self,
        hosts: &[String],
        path_and_query: &str,
        referer: Option<&str>,
    ) -> Result<(String, String), NetError> {
        let mut last_err: Option<NetError> = None;
        let mut attempted = 0usize;
        for host in hosts {
            let breaker = self.breaker_for(host);
            if !breaker.allow() {
                self.counters.circuit_blocked.fetch_add(1, Ordering::Relaxed);
                last_err = Some(NetError::CircuitOpen(host.clone()));
                continue;
            }
            attempted += 1;
            let url = format!("https://{host}{path_and_query}");
            let started = Instant::now();
            match self.get_bytes_inner(&url, referer, false).await {
                Ok(body) => {
                    let ms = started.elapsed().as_millis() as u64;
                    breaker.on_success();
                    self.host_stat(host).record_ok(ms);
                    let (decoded, _, _) = encoding_rs::UTF_8.decode(&body);
                    return Ok((decoded.into_owned(), host.clone()));
                }
                Err(err) => {
                    breaker.on_failure();
                    self.host_stat(host).record_err(&err.to_string());
                    self.note_ip_risk(&err);
                    let unreachable = err.is_host_unreachable();
                    tracing::warn!(target: "network", host = %host, error = %err, "主机请求失败");
                    last_err = Some(err);
                    if unreachable && attempted > 1 {
                        // 多个主机同时不可达，判定为网络级故障，不再逐台重试
                        tracing::warn!(target: "network", "多个数据主机不可达，判定为网络异常，停止主机轮换");
                        break;
                    }
                }
            }
        }
        if attempted > 0 {
            self.counters.failed.fetch_add(1, Ordering::Relaxed);
        }
        Err(last_err.unwrap_or_else(|| NetError::Disabled("无可用主机".into())))
    }

    async fn fetch_with_retry(
        &self,
        url: &str,
        referer: Option<&str>,
    ) -> Result<Arc<[u8]>, NetError> {
        let cfg = self.cfg.read().clone();
        let max_attempts = cfg.retry_max.clamp(0, 6) + 1;
        // 服务端错误（5xx）在代理/网关场景下重试代价高，最多额外重试 1 次
        let server_error_attempts = 2u32;
        let mut attempt = 0u32;
        let mut last_err: Option<NetError> = None;

        while attempt < max_attempts {
            if attempt > 0 {
                // 指数退避：1s, 2s, 4s, 8s（叠加确定性抖动，避免雪崩）
                let base = 1u64 << (attempt - 1).min(3);
                let jitter = (crate::clock::now_cn().timestamp_millis() as u64 % 250) as u64;
                let wait = Duration::from_millis(base * 1000 + jitter);
                tracing::debug!(target: "network", attempt, wait_ms = wait.as_millis(), "指数退避重试");
                tokio::time::sleep(wait).await;
            }
            attempt += 1;

            let _permit = self.limiter.acquire().await;
            let started = Instant::now();
            self.counters.total.fetch_add(1, Ordering::Relaxed);

            let mut req = self.client.get(url);
            if let Some(r) = referer {
                req = req.header(REFERER, r);
            }
            let outcome = req.send().await;
            let latency = started.elapsed().as_millis() as u64;
            self.counters
                .latency_sum_ms
                .fetch_add(latency, Ordering::Relaxed);
            self.counters
                .latency_samples
                .fetch_add(1, Ordering::Relaxed);

            match outcome {
                Err(err) => {
                    let classified = classify_reqwest(&err, url);
                    self.note_ip_risk(&classified);
                    // 主机不可达（DNS 失败 / 连接被拒）不做指数退避重试，
                    // 直接交给上层主机轮换或降级源，避免用户长时间等待
                    let retry = classified.is_retryable() && !classified.is_host_unreachable();
                    tracing::warn!(target: "network", url = %url, error = %classified, latency_ms = latency, "请求失败");
                    last_err = Some(classified);
                    if !retry {
                        break;
                    }
                }
                Ok(resp) => {
                    let status = resp.status();
                    if status.is_success() {
                        match resp.bytes().await {
                            Ok(raw) => {
                                if raw.iter().all(|b| b.is_ascii_whitespace()) || raw.is_empty() {
                                    let e = NetError::EmptyBody(url.to_string());
                                    self.note_ip_risk(&e);
                                    last_err = Some(e);
                                    continue;
                                }
                                self.counters.success.fetch_add(1, Ordering::Relaxed);
                                self.note_recovery();
                                return Ok(Arc::from(&raw[..]));
                            }
                            Err(err) => {
                                let e = classify_reqwest(&err, url);
                                let retry = e.is_retryable();
                                last_err = Some(e);
                                if !retry {
                                    break;
                                }
                            }
                        }
                    } else {
                        let code = status.as_u16();
                        let e = if code == 403 || code == 401 {
                            NetError::Forbidden(format!("HTTP {code} {url}"))
                        } else if code == 429 {
                            NetError::RateLimited(format!("HTTP {code} {url}"))
                        } else {
                            NetError::ServerError(format!("HTTP {code} {url}"))
                        };
                        self.note_ip_risk(&e);
                        tracing::warn!(target: "network", url = %url, status = code, "HTTP 状态异常");
                        let retry = e.is_retryable() && attempt < server_error_attempts;
                        last_err = Some(e);
                        if !retry {
                            break;
                        }
                    }
                }
            }
        }

        self.counters.failed.fetch_add(1, Ordering::Relaxed);
        Err(last_err.unwrap_or_else(|| NetError::Connect(url.to_string())))
    }

    /// 请求并解析 JSON（自动剥离 JSONP 包装，自动编码识别）
    pub async fn get_json<T: DeserializeOwned>(
        &self,
        url: &str,
        referer: Option<&str>,
    ) -> Result<T, NetError> {
        let body = self.get_text_auto(url, referer).await?;
        parse_json_body(&body).map_err(|e| NetError::Parse(format!("{e} @ {url}")))
    }

    /// 多主机轮换 + JSON 解析
    pub async fn get_json_rotating<T: DeserializeOwned>(
        &self,
        hosts: &[String],
        path_and_query: &str,
        referer: Option<&str>,
    ) -> Result<T, NetError> {
        let (body, _host) = self.get_text_rotating(hosts, path_and_query, referer).await?;
        parse_json_body(&body).map_err(|e| NetError::Parse(format!("{e} @ {path_and_query}")))
    }

    pub fn stats(&self) -> NetStats {
        let total = self.counters.total.load(Ordering::Relaxed);
        let samples = self.counters.latency_samples.load(Ordering::Relaxed);
        let sum = self.counters.latency_sum_ms.load(Ordering::Relaxed);
        let hosts = self
            .host_stats
            .iter()
            .map(|e| {
                let stat = e.value();
                let s = stat.samples.load(Ordering::Relaxed);
                let state = self
                    .breakers
                    .get(e.key())
                    .map(|b| b.state().as_str().to_string())
                    .unwrap_or_else(|| BreakerState::Closed.as_str().to_string());
                HostHealth {
                    host: e.key().clone(),
                    state,
                    success: stat.success.load(Ordering::Relaxed),
                    failure: stat.failure.load(Ordering::Relaxed),
                    avg_latency_ms: if s > 0 {
                        stat.latency_sum_ms.load(Ordering::Relaxed) / s
                    } else {
                        0
                    },
                }
            })
            .collect();

        NetStats {
            total_requests: total,
            success: self.counters.success.load(Ordering::Relaxed),
            failed: self.counters.failed.load(Ordering::Relaxed),
            deduped: self.counters.deduped.load(Ordering::Relaxed),
            rate_limited: self.limiter.limited_count(),
            circuit_open: self.breakers.iter().any(|b| b.value().is_open()),
            circuit_opens: self.breakers.iter().map(|b| b.value().stats().2).sum(),
            avg_latency_ms: if samples > 0 { sum / samples } else { 0 },
            hosts,
        }
    }
}

/// 自动编码识别：合法 UTF-8 直接用，否则按 GB18030 解码（腾讯/新浪行情为 GBK）
pub fn decode_auto(bytes: &[u8]) -> String {
    match std::str::from_utf8(bytes) {
        Ok(s) => s.to_string(),
        Err(_) => {
            let (decoded, _, _) = encoding_rs::GB18030.decode(bytes);
            decoded.into_owned()
        }
    }
}

/// 从 reqwest 错误中提取语义（第 3.6 章）
fn classify_reqwest(err: &reqwest::Error, url: &str) -> NetError {
    if err.is_timeout() {
        NetError::Timeout(format!("{url} :: {err}"))
    } else if err.is_connect() {
        let text = err.to_string().to_lowercase();
        if text.contains("dns") || text.contains("resolve") || text.contains("name or service") {
            NetError::NetworkUnavailable(format!("{url} :: {err}"))
        } else {
            NetError::Connect(format!("{url} :: {err}"))
        }
    } else if err.is_decode() {
        NetError::Parse(format!("{url} :: {err}"))
    } else if err.is_request() {
        NetError::Connect(format!("{url} :: {err}"))
    } else {
        NetError::Connect(format!("{url} :: {err}"))
    }
}

/// 解析 JSON，兼容 `cb(...)` / `var x = {...};` 等包装形式
pub fn parse_json_body<T: DeserializeOwned>(body: &str) -> Result<T, String> {    let trimmed = body.trim().trim_start_matches('\u{feff}').trim();
    if trimmed.is_empty() {
        return Err("响应为空".into());
    }
    if trimmed.starts_with('{') || trimmed.starts_with('[') {
        return serde_json::from_str(trimmed).map_err(|e| e.to_string());
    }
    // JSONP / 变量赋值
    if let (Some(start), Some(end)) = (trimmed.find('{'), trimmed.rfind('}')) {
        if end > start {
            let slice = &trimmed[start..=end];
            return serde_json::from_str(slice).map_err(|e| e.to_string());
        }
    }
    Err(format!("无法识别的响应格式: {}", &trimmed[..trimmed.len().min(80)]))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_plain_json() {
        let v: serde_json::Value = parse_json_body(r#"{"a":1}"#).unwrap();
        assert_eq!(v["a"], 1);
    }

    #[test]
    fn parses_jsonp_wrapper() {
        let v: serde_json::Value = parse_json_body("cb({\"rc\":0,\"data\":[1,2]});").unwrap();
        assert_eq!(v["rc"], 0);
    }

    #[test]
    fn parses_variable_assignment() {
        let v: serde_json::Value = parse_json_body("var x = {\"ok\":true};").unwrap();
        assert_eq!(v["ok"], true);
    }

    #[test]
    fn rejects_empty_and_garbage() {
        assert!(parse_json_body::<serde_json::Value>("").is_err());
        assert!(parse_json_body::<serde_json::Value>("   ").is_err());
        assert!(parse_json_body::<serde_json::Value>("not json at all").is_err());
    }

    #[test]
    fn decode_auto_handles_utf8_and_gbk() {
        assert_eq!(decode_auto("贵州茅台".as_bytes()), "贵州茅台");
        // "贵州茅台" 的 GBK 编码
        let gbk = [0xb9u8, 0xf3, 0xd6, 0xdd, 0xc3, 0xa9, 0xcc, 0xa8];
        assert_eq!(decode_auto(&gbk), "贵州茅台");
    }

    #[test]
    fn parses_gbk_wrapped_quote() {
        let mut raw = b"v_sh600519=\"1~".to_vec();
        raw.extend_from_slice(&[0xb9u8, 0xf3, 0xd6, 0xdd, 0xc3, 0xa9, 0xcc, 0xa8]);
        raw.extend_from_slice(b"~600519~1258.62~\";");
        let text = decode_auto(&raw);
        assert!(text.contains("贵州茅台"));
        assert!(text.contains("1258.62"));
    }

    #[test]
    fn client_builds_with_defaults() {
        let client = HttpClient::new(&NetConfig::default()).unwrap();
        assert!(!client.is_ip_protected());
        let stats = client.stats();
        assert_eq!(stats.total_requests, 0);
    }

    #[tokio::test]
    async fn coalesces_concurrent_requests_to_unreachable_host() {
        let mut cfg = NetConfig::default();
        cfg.retry_max = 0;
        cfg.timeout_secs = 2;
        cfg.connect_timeout_secs = 1;
        let client = Arc::new(HttpClient::new(&cfg).unwrap());
        let url = "https://127.0.0.1:9/never";
        let mut handles = Vec::new();
        for _ in 0..5 {
            let c = client.clone();
            handles.push(tokio::spawn(async move { c.get_text(url, None).await }));
        }
        let mut errs = 0;
        for h in handles {
            if h.await.unwrap().is_err() {
                errs += 1;
            }
        }
        assert_eq!(errs, 5);
        // 去重生效：实际发出的网络请求少于调用次数
        assert!(client.stats().deduped >= 1, "并发请求应被合并");
    }
}
