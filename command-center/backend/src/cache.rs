//! 一级缓存：内存缓存（第 4.7 / 8.7 章）。
//!
//! 所有盘中实时数据先落内存，多模块共享读取，避免重复请求。
//! 带 TTL 与容量上限，超限按 LRU 淘汰，防止长时间运行内存增长（第 7.9 章）。

use chrono::{DateTime, FixedOffset};
use dashmap::DashMap;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::models::{
    BoardResult, CacheStats, DataSource, DataStatus, IndexTick, IntradaySnapshot, MarketBreadth,
    SectorTick, StockAggregate, StockTick,
};

#[derive(Debug, Clone)]
pub struct Entry<T> {
    pub value: T,
    pub source: DataSource,
    pub status: DataStatus,
    pub updated_at: DateTime<FixedOffset>,
    pub expires_at: Instant,
    pub message: String,
}

impl<T> Entry<T> {
    pub fn is_expired(&self, now: Instant) -> bool {
        now >= self.expires_at
    }

    pub fn age_secs(&self) -> i64 {
        (crate::clock::now_cn() - self.updated_at).num_seconds().max(0)
    }
}

pub struct TtlCache<T> {
    map: DashMap<String, Arc<Entry<T>>>,
    capacity: usize,
    default_ttl: Duration,
    hits: AtomicU64,
    misses: AtomicU64,
}

impl<T: Clone + Send + Sync + 'static> TtlCache<T> {
    pub fn new(capacity: usize, default_ttl: Duration) -> Self {
        Self {
            map: DashMap::new(),
            capacity: capacity.max(8),
            default_ttl,
            hits: AtomicU64::new(0),
            misses: AtomicU64::new(0),
        }
    }

    pub fn put(&self, key: impl Into<String>, entry: Entry<T>) {
        let key: String = key.into();
        if self.map.len() >= self.capacity && !self.map.contains_key(&key) {
            self.evict();
        }
        self.map.insert(key, Arc::new(entry));
    }

    pub fn put_value(
        &self,
        key: impl Into<String>,
        value: T,
        source: DataSource,
        status: DataStatus,
        ttl: Duration,
    ) {
        let now = crate::clock::now_cn();
        self.put(
            key,
            Entry {
                value,
                source,
                status,
                updated_at: now,
                expires_at: Instant::now() + ttl,
                message: String::new(),
            },
        );
    }

    pub fn get(&self, key: &str) -> Option<Arc<Entry<T>>> {
        let entry = self.map.get(key).map(|e| e.value().clone());
        match entry {
            Some(e) => {
                if e.is_expired(Instant::now()) {
                    self.misses.fetch_add(1, Ordering::Relaxed);
                    None
                } else {
                    self.hits.fetch_add(1, Ordering::Relaxed);
                    Some(e)
                }
            }
            None => {
                self.misses.fetch_add(1, Ordering::Relaxed);
                None
            }
        }
    }

    /// 读取（允许过期），用于“降级显示最近有效数据”（第 4.6 章）
    pub fn get_stale(&self, key: &str) -> Option<Arc<Entry<T>>> {
        self.map.get(key).map(|e| e.value().clone())
    }

    pub fn remove(&self, key: &str) {
        self.map.remove(key);
    }

    pub fn clear(&self) {
        self.map.clear();
    }

    pub fn len(&self) -> usize {
        self.map.len()
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    pub fn default_ttl(&self) -> Duration {
        self.default_ttl
    }

    fn evict(&self) {
        // 先清理过期项，仍超限则删除任意最旧项
        let now = Instant::now();
        let expired: Vec<String> = self
            .map
            .iter()
            .filter(|e| e.value().is_expired(now))
            .map(|e| e.key().clone())
            .collect();
        for k in expired {
            self.map.remove(&k);
        }
        if self.map.len() >= self.capacity {
            if let Some(k) = self
                .map
                .iter()
                .min_by_key(|e| e.value().updated_at)
                .map(|e| e.key().clone())
            {
                self.map.remove(&k);
            }
        }
    }

    pub fn stats(&self) -> (u64, u64) {
        (
            self.hits.load(Ordering::Relaxed),
            self.misses.load(Ordering::Relaxed),
        )
    }
}

/// 应用级缓存中枢
pub struct CacheHub {
    pub stocks: TtlCache<Vec<StockTick>>,
    pub sectors: TtlCache<Vec<SectorTick>>,
    pub indices: TtlCache<Vec<IndexTick>>,
    pub breadth: TtlCache<MarketBreadth>,
    pub boards: TtlCache<Vec<BoardResult>>,
    pub snapshots: TtlCache<HashMap<String, IntradaySnapshot>>,
    pub aggregates: TtlCache<Vec<StockAggregate>>,
    /// 个股分时资金流
    pub flow_series: TtlCache<Vec<crate::models::FlowPoint>>,
    /// 市场资金曲线（带口径信息）
    pub market_flow: TtlCache<crate::models::MarketFlowSeries>,
    pub sector_series: TtlCache<Vec<crate::models::FlowPoint>>,
    pub minute_series: TtlCache<Vec<crate::models::MinutePoint>>,
    pub stock_detail: TtlCache<StockTick>,
}

impl CacheHub {
    pub fn new(default_ttl_secs: u64) -> Self {
        let ttl = Duration::from_secs(default_ttl_secs.max(5));
        Self {
            stocks: TtlCache::new(16, Duration::from_secs(120)),
            sectors: TtlCache::new(16, Duration::from_secs(120)),
            indices: TtlCache::new(16, Duration::from_secs(60)),
            breadth: TtlCache::new(16, Duration::from_secs(120)),
            boards: TtlCache::new(32, Duration::from_secs(300)),
            snapshots: TtlCache::new(16, Duration::from_secs(3600)),
            aggregates: TtlCache::new(16, Duration::from_secs(600)),
            flow_series: TtlCache::new(64, Duration::from_secs(120)),
            market_flow: TtlCache::new(8, Duration::from_secs(900)),
            sector_series: TtlCache::new(64, Duration::from_secs(120)),
            minute_series: TtlCache::new(64, Duration::from_secs(600)),
            stock_detail: TtlCache::new(512, ttl),
        }
    }

    pub fn stats(&self) -> CacheStats {
        let mut hits = 0u64;
        let mut misses = 0u64;
        let mut entries = 0usize;
        macro_rules! acc {
            ($c:expr) => {{
                let (h, m) = $c.stats();
                hits += h;
                misses += m;
                entries += $c.len();
            }};
        }
        acc!(self.stocks);
        acc!(self.sectors);
        acc!(self.indices);
        acc!(self.breadth);
        acc!(self.boards);
        acc!(self.snapshots);
        acc!(self.aggregates);
        acc!(self.flow_series);
        acc!(self.market_flow);
        acc!(self.sector_series);
        acc!(self.minute_series);
        acc!(self.stock_detail);
        let total = hits + misses;
        CacheStats {
            entries,
            hits,
            misses,
            hit_rate: if total > 0 {
                hits as f64 / total as f64
            } else {
                0.0
            },
            memory_kb: (entries as u64) * 4,
        }
    }

    pub fn clear_all(&self) {
        self.stocks.clear();
        self.sectors.clear();
        self.indices.clear();
        self.breadth.clear();
        self.boards.clear();
        self.snapshots.clear();
        self.aggregates.clear();
        self.flow_series.clear();
        self.market_flow.clear();
        self.sector_series.clear();
        self.minute_series.clear();
        self.stock_detail.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(v: i32, ttl_ms: u64) -> Entry<i32> {
        Entry {
            value: v,
            source: DataSource::EastmoneyRealtime,
            status: DataStatus::Realtime,
            updated_at: crate::clock::now_cn(),
            expires_at: Instant::now() + Duration::from_millis(ttl_ms),
            message: String::new(),
        }
    }

    #[test]
    fn respects_ttl() {
        let cache: TtlCache<i32> = TtlCache::new(8, Duration::from_secs(1));
        cache.put("a", entry(1, 40));
        assert_eq!(cache.get("a").unwrap().value, 1);
        std::thread::sleep(Duration::from_millis(70));
        assert!(cache.get("a").is_none(), "过期后不应命中");
        assert!(cache.get_stale("a").is_some(), "降级读取应仍可取到旧值");
    }

    #[test]
    fn evicts_when_over_capacity() {
        let cache: TtlCache<i32> = TtlCache::new(8, Duration::from_secs(60));
        for i in 0..40 {
            cache.put(format!("k{i}"), entry(i, 60_000));
        }
        assert!(cache.len() <= 40);
    }

    #[test]
    fn counts_hits_and_misses() {
        let cache: TtlCache<i32> = TtlCache::new(8, Duration::from_secs(60));
        cache.put("x", entry(7, 60_000));
        assert!(cache.get("x").is_some());
        assert!(cache.get("missing").is_none());
        let (hits, misses) = cache.stats();
        assert_eq!(hits, 1);
        assert_eq!(misses, 1);
    }

    #[test]
    fn hub_reports_stats() {
        let hub = CacheHub::new(30);
        hub.stocks
            .put_value("all", vec![], DataSource::SinaRealtime, DataStatus::Cache, Duration::from_secs(30));
        let stats = hub.stats();
        assert_eq!(stats.entries, 1);
        assert!(stats.hit_rate >= 0.0);
    }
}
