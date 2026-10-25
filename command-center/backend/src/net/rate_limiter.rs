//! 全局限流器（第 4.6 / 8.4 章）。
//!
//! 支持：每秒上限、每分钟上限、最小请求间隔、并发上限。
//! 所有外部请求必须经过 `acquire()`，禁止任何模块绕过。

use parking_lot::Mutex;
use std::collections::VecDeque;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{OwnedSemaphorePermit, Semaphore};
use tokio::time::Instant;

#[derive(Debug, Clone, Copy)]
pub struct RateLimiterConfig {
    pub per_second: u32,
    pub per_minute: u32,
    pub min_interval: Duration,
    pub max_concurrent: usize,
}

impl Default for RateLimiterConfig {
    fn default() -> Self {
        Self {
            per_second: 4,
            per_minute: 180,
            min_interval: Duration::from_millis(60),
            max_concurrent: 4,
        }
    }
}

pub struct RateLimiter {
    cfg: RateLimiterConfig,
    recent: Mutex<VecDeque<Instant>>,
    last_dispatch: Mutex<Option<Instant>>,
    sem: Arc<Semaphore>,
    /// 保护模式降频倍率（1 = 正常，>1 = 降频）
    throttle: Mutex<f64>,
    limited_count: std::sync::atomic::AtomicU64,
}

pub struct RatePermit {
    _permit: OwnedSemaphorePermit,
}

impl RateLimiter {
    pub fn new(cfg: RateLimiterConfig) -> Self {
        Self {
            cfg,
            recent: Mutex::new(VecDeque::with_capacity(256)),
            last_dispatch: Mutex::new(None),
            sem: Arc::new(Semaphore::new(cfg.max_concurrent.max(1))),
            throttle: Mutex::new(1.0),
            limited_count: std::sync::atomic::AtomicU64::new(0),
        }
    }

    pub fn config(&self) -> RateLimiterConfig {
        self.cfg
    }

    /// 当前降频倍率
    pub fn throttle_factor(&self) -> f64 {
        *self.throttle.lock()
    }

    /// IP 保护：进入降频模式（第 8.5 章）
    pub fn throttle_up(&self) {
        let mut t = self.throttle.lock();
        *t = (*t * 2.0).min(16.0);
    }

    /// 恢复正常速率
    pub fn throttle_reset(&self) {
        *self.throttle.lock() = 1.0;
    }

    pub fn limited_count(&self) -> u64 {
        self.limited_count.load(std::sync::atomic::Ordering::Relaxed)
    }

    /// 计算需要等待的时间
    fn compute_wait(&self, now: Instant) -> Duration {
        let factor = self.throttle_factor();
        let mut recent = self.recent.lock();
        while let Some(front) = recent.front() {
            if now.duration_since(*front) > Duration::from_secs(60) {
                recent.pop_front();
            } else {
                break;
            }
        }
        let mut wait = Duration::ZERO;

        // 每分钟
        let minute_cap = ((self.cfg.per_minute as f64) / factor).floor().max(1.0) as usize;
        if recent.len() >= minute_cap {
            if let Some(oldest) = recent.front() {
                wait = wait.max(Duration::from_secs(60) - now.duration_since(*oldest));
            }
        }

        // 每秒
        let sec_cap = ((self.cfg.per_second as f64) / factor).floor().max(1.0) as usize;
        let in_second = recent
            .iter()
            .rev()
            .take_while(|t| now.duration_since(**t) <= Duration::from_secs(1))
            .count();
        if in_second >= sec_cap {
            let boundary = recent
                .iter()
                .rev()
                .take(in_second)
                .last()
                .copied()
                .unwrap_or(now);
            wait = wait.max(Duration::from_secs(1) - now.duration_since(boundary));
        }

        // 最小间隔
        let min_gap = Duration::from_secs_f64(self.cfg.min_interval.as_secs_f64() * factor);
        if let Some(last) = *self.last_dispatch.lock() {
            let elapsed = now.duration_since(last);
            if elapsed < min_gap {
                wait = wait.max(min_gap - elapsed);
            }
        }
        wait
    }

    fn record(&self, now: Instant) {
        let mut recent = self.recent.lock();
        recent.push_back(now);
        *self.last_dispatch.lock() = Some(now);
    }

    /// 获取一次请求许可（阻塞直到允许）
    pub async fn acquire(&self) -> RatePermit {
        let permit = self
            .sem
            .clone()
            .acquire_owned()
            .await
            .expect("rate limiter semaphore closed");
        loop {
            let now = Instant::now();
            let wait = self.compute_wait(now);
            if wait.is_zero() {
                self.record(now);
                return RatePermit { _permit: permit };
            }
            self.limited_count
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            let capped = wait.min(Duration::from_secs(5));
            tokio::time::sleep(capped + Duration::from_millis(5)).await;
        }
    }

    /// 剩余并发额度（用于监控展示）
    pub fn available_permits(&self) -> usize {
        self.sem.available_permits()
    }

    pub fn requests_in_last_minute(&self) -> usize {
        let now = Instant::now();
        self.recent
            .lock()
            .iter()
            .filter(|t| now.duration_since(**t) <= Duration::from_secs(60))
            .count()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn respects_per_second_limit() {
        let limiter = RateLimiter::new(RateLimiterConfig {
            per_second: 3,
            per_minute: 1000,
            min_interval: Duration::from_millis(0),
            max_concurrent: 4,
        });
        let start = Instant::now();
        for _ in 0..4 {
            let _p = limiter.acquire().await;
        }
        // 第 4 次必须等到下一个 1 秒窗口
        assert!(
            start.elapsed() >= Duration::from_millis(900),
            "elapsed = {:?}",
            start.elapsed()
        );
        assert!(limiter.limited_count() >= 1);
    }

    #[tokio::test]
    async fn throttle_up_slows_requests() {
        let limiter = RateLimiter::new(RateLimiterConfig {
            per_second: 4,
            per_minute: 1000,
            min_interval: Duration::from_millis(50),
            max_concurrent: 4,
        });
        limiter.throttle_up();
        limiter.throttle_up();
        assert!((limiter.throttle_factor() - 4.0).abs() < f64::EPSILON);
        let start = Instant::now();
        for _ in 0..2 {
            let _p = limiter.acquire().await;
        }
        assert!(start.elapsed() >= Duration::from_millis(180));
        limiter.throttle_reset();
        assert!((limiter.throttle_factor() - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn throttle_factor_is_capped() {
        let limiter = RateLimiter::new(RateLimiterConfig::default());
        for _ in 0..20 {
            limiter.throttle_up();
        }
        assert!(limiter.throttle_factor() <= 16.0);
    }
}
