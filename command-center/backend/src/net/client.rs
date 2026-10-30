//! HTTP 客户端【Shell 版：不具备联网能力】
//!
//! # 为什么这个文件被改写成"空客户端"
//!
//! 原实现是本程序**唯一的外部请求出口**：全局限流、并发去重、指数退避重试、
//! 按主机熔断、多镜像轮换，全部集中在这里。
//!
//! 上游行情接口已对爬虫来源做风控标记，任何联网取数都会导致运行机器的
//! IP 被封禁。因此本版本**在唯一出口处切断**：所有请求方法立即返回
//! `NetError::Disabled`，不做任何 socket 操作。
//!
//! # 为什么保留 API 而不删掉这一层
//!
//! `source/{eastmoney,sina,tencent}.rs`、`collector/*`、`history/*` 全部按这些
//! 签名调用。保留签名、改变行为，上层可以一行不改地走既有的错误降级路径
//! （回落到缓存或空数据），程序仍能启动、渲染与计算。
//!
//! 这与 Python 版的处理一致：**换实现，不砍层**。
//!
//! # 能力保证
//!
//! * 本文件不再依赖任何 HTTP 客户端 crate
//! * `Cargo.toml` 已移除 `reqwest`
//! * 依赖树中不存在 TLS / HTTP 栈 → 运行期无法发起任何出站请求
//!
//! 静态验证：`python scripts/check_no_network.py .`

use parking_lot::RwLock;
use serde::de::DeserializeOwned;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use crate::config::NetConfig;
use crate::models::NetStats;

use super::error::NetError;
use super::rate_limiter::{RateLimiter, RateLimiterConfig};

/// 统一的"已禁用"错误文案
const DISABLED_MSG: &str =
    "本版本不含数据读取能力：已移除全部联网取数实现（Shell 版）";

pub struct HttpClient {
    limiter: Arc<RateLimiter>,
    cfg: RwLock<NetConfig>,
    ip_protection: AtomicBool,
    /// 被拦截的请求计数（每次调用 +1，用于界面提示"已拦截 N 次取数尝试"）
    blocked: AtomicU64,
}

impl HttpClient {
    pub fn new(cfg: &NetConfig) -> anyhow::Result<Self> {
        Ok(Self {
            limiter: Arc::new(RateLimiter::new(RateLimiterConfig {
                per_second: cfg.requests_per_second.max(1),
                per_minute: cfg.requests_per_minute.max(1),
                min_interval: Duration::from_millis(cfg.min_interval_ms),
                max_concurrent: cfg.max_concurrent.max(1),
            })),
            cfg: RwLock::new(cfg.clone()),
            ip_protection: AtomicBool::new(false),
            blocked: AtomicU64::new(0),
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

    pub fn is_ip_protected(&self) -> bool {
        self.ip_protection.load(Ordering::Relaxed)
    }

    /// 已拦截的取数尝试次数
    pub fn blocked_count(&self) -> u64 {
        self.blocked.load(Ordering::Relaxed)
    }

    fn reject(&self) -> NetError {
        self.blocked.fetch_add(1, Ordering::Relaxed);
        NetError::Disabled(DISABLED_MSG.to_string())
    }

    // ---------------------------------------------------------------- 请求
    // 全部立即失败。不做 DNS、不建连接、不发字节。

    pub async fn get_bytes(
        &self,
        _url: &str,
        _referer: Option<&str>,
    ) -> Result<Arc<[u8]>, NetError> {
        Err(self.reject())
    }

    pub async fn get_text(&self, _url: &str, _referer: Option<&str>) -> Result<String, NetError> {
        Err(self.reject())
    }

    pub async fn get_text_gbk(
        &self,
        _url: &str,
        _referer: Option<&str>,
    ) -> Result<String, NetError> {
        Err(self.reject())
    }

    pub async fn get_text_auto(
        &self,
        _url: &str,
        _referer: Option<&str>,
    ) -> Result<String, NetError> {
        Err(self.reject())
    }

    pub async fn get_text_rotating(
        &self,
        _hosts: &[String],
        _path_and_query: &str,
        _referer: Option<&str>,
    ) -> Result<(String, String), NetError> {
        Err(self.reject())
    }

    pub async fn get_json<T: DeserializeOwned>(
        &self,
        _url: &str,
        _referer: Option<&str>,
    ) -> Result<T, NetError> {
        Err(self.reject())
    }

    pub async fn get_json_rotating<T: DeserializeOwned>(
        &self,
        _hosts: &[String],
        _path_and_query: &str,
        _referer: Option<&str>,
    ) -> Result<T, NetError> {
        Err(self.reject())
    }

    /// 统计信息。Shell 版只报"拦截了多少次"，其余恒为 0。
    pub fn stats(&self) -> NetStats {
        NetStats {
            total_requests: 0,
            success: 0,
            failed: self.blocked.load(Ordering::Relaxed),
            deduped: 0,
            rate_limited: 0,
            circuit_open: false,
            circuit_opens: 0,
            avg_latency_ms: 0,
            hosts: Vec::new(),
        }
    }
}

/// 自动编码识别：合法 UTF-8 直接用，否则按 GB18030 解码。
///
/// 纯函数，与网络无关，保留给本地文件解析复用。
pub fn decode_auto(bytes: &[u8]) -> String {
    match std::str::from_utf8(bytes) {
        Ok(s) => s.to_string(),
        Err(_) => {
            let (decoded, _, _) = encoding_rs::GB18030.decode(bytes);
            decoded.into_owned()
        }
    }
}

/// 解析 JSON，兼容 `cb(...)` / `var x = {...};` 等包装形式。
///
/// 纯函数，保留给本地文件解析复用。
pub fn parse_json_body<T: DeserializeOwned>(body: &str) -> Result<T, String> {
    let trimmed = body.trim().trim_start_matches('\u{feff}').trim();
    if trimmed.is_empty() {
        return Err("响应为空".into());
    }
    if trimmed.starts_with('{') || trimmed.starts_with('[') {
        return serde_json::from_str(trimmed).map_err(|e| e.to_string());
    }
    let start = trimmed
        .find(|c| c == '{' || c == '[')
        .ok_or_else(|| "未找到 JSON 起始符".to_string())?;
    let end = trimmed
        .rfind(|c| c == '}' || c == ']')
        .ok_or_else(|| "未找到 JSON 结束符".to_string())?;
    if end <= start {
        return Err("JSON 边界异常".into());
    }
    serde_json::from_str(&trimmed[start..=end]).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_auto_handles_utf8() {
        assert_eq!(decode_auto("半导体".as_bytes()), "半导体");
    }

    #[test]
    fn parse_json_strips_callback_wrapper() {
        let v: serde_json::Value = parse_json_body("cb({\"a\":1});").unwrap();
        assert_eq!(v["a"], 1);
    }

    #[test]
    fn parse_json_rejects_empty() {
        assert!(parse_json_body::<serde_json::Value>("   ").is_err());
    }

    #[tokio::test]
    async fn every_request_is_refused() {
        let cfg = NetConfig::default();
        let c = HttpClient::new(&cfg).unwrap();
        assert!(c.get_text("http://example.invalid/x", None).await.is_err());
        assert!(c.get_bytes("http://example.invalid/x", None).await.is_err());
        assert!(c
            .get_text_rotating(&["h1".into()], "/p", None)
            .await
            .is_err());
        assert!(c
            .get_json::<serde_json::Value>("http://example.invalid/x", None)
            .await
            .is_err());
        assert_eq!(c.blocked_count(), 4);
    }
}
