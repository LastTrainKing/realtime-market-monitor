//! 熔断器（第 4.6 / 8.5 章）。
//!
//! 连续失败达到阈值后进入 Open 状态，暂停对该主机的请求；
//! 冷却期结束后进入 HalfOpen，**只放行一个探测请求**决定是否恢复。
//!
//! 关键约束：HalfOpen 必须是"单探针"语义。
//! 若在 HalfOpen 状态下无条件放行，数据源刚恢复时会有大量并发请求同时涌入，
//! 立刻再次触发 403/429，与熔断器"保护数据源"的初衷完全相反。

use parking_lot::Mutex;
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BreakerState {
    Closed,
    Open,
    HalfOpen,
}

impl BreakerState {
    pub fn as_str(&self) -> &'static str {
        match self {
            BreakerState::Closed => "closed",
            BreakerState::Open => "open",
            BreakerState::HalfOpen => "half_open",
        }
    }
}

struct Inner {
    state: BreakerState,
    consecutive_failures: u32,
    opened_at: Option<Instant>,
    /// 探测请求是否在途（HalfOpen 单探针）
    probe_in_flight: bool,
    probe_started_at: Option<Instant>,
    opens: u64,
    success: u64,
    failure: u64,
    rejected_in_half_open: u64,
}

pub struct CircuitBreaker {
    inner: Mutex<Inner>,
    threshold: u32,
    cooldown: Duration,
}

impl CircuitBreaker {
    pub fn new(threshold: u32, cooldown: Duration) -> Self {
        Self {
            inner: Mutex::new(Inner {
                state: BreakerState::Closed,
                consecutive_failures: 0,
                opened_at: None,
                probe_in_flight: false,
                probe_started_at: None,
                opens: 0,
                success: 0,
                failure: 0,
                rejected_in_half_open: 0,
            }),
            threshold: threshold.max(1),
            cooldown,
        }
    }

    /// 是否允许发起请求。
    ///
    /// HalfOpen 状态下只有第一个调用者会拿到 `true` 并占用探针名额，
    /// 其余并发请求一律返回 `false`（防止恢复瞬间的请求风暴）。
    pub fn allow(&self) -> bool {
        let mut inner = self.inner.lock();
        let now = Instant::now();
        match inner.state {
            BreakerState::Closed => true,
            BreakerState::Open => {
                let elapsed = inner
                    .opened_at
                    .map(|t| now.duration_since(t))
                    .unwrap_or(Duration::ZERO);
                if elapsed >= self.cooldown {
                    // 冷却结束 → 进入 HalfOpen，并立即占用唯一探针名额
                    inner.state = BreakerState::HalfOpen;
                    inner.probe_in_flight = true;
                    inner.probe_started_at = Some(now);
                    true
                } else {
                    false
                }
            }
            BreakerState::HalfOpen => {
                // 探针超时保护：请求被取消 / 卡死时不应永久堵死熔断器
                if inner.probe_in_flight {
                    let stuck = inner
                        .probe_started_at
                        .map(|t| now.duration_since(t) >= self.cooldown)
                        .unwrap_or(true);
                    if !stuck {
                        inner.rejected_in_half_open += 1;
                        return false;
                    }
                    // 探针超时，允许换一个新的探针
                    inner.probe_started_at = Some(now);
                    return true;
                }
                inner.probe_in_flight = true;
                inner.probe_started_at = Some(now);
                true
            }
        }
    }

    pub fn on_success(&self) {
        let mut inner = self.inner.lock();
        inner.success += 1;
        inner.consecutive_failures = 0;
        inner.probe_in_flight = false;
        inner.probe_started_at = None;
        if inner.state != BreakerState::Closed {
            inner.state = BreakerState::Closed;
            inner.opened_at = None;
        }
    }

    pub fn on_failure(&self) {
        let mut inner = self.inner.lock();
        inner.failure += 1;
        inner.consecutive_failures += 1;
        inner.probe_in_flight = false;
        inner.probe_started_at = None;
        match inner.state {
            BreakerState::HalfOpen => {
                inner.state = BreakerState::Open;
                inner.opened_at = Some(Instant::now());
                inner.opens += 1;
            }
            _ => {
                if inner.consecutive_failures >= self.threshold {
                    if inner.state != BreakerState::Open {
                        inner.opens += 1;
                    }
                    inner.state = BreakerState::Open;
                    inner.opened_at = Some(Instant::now());
                }
            }
        }
    }

    pub fn state(&self) -> BreakerState {
        self.inner.lock().state
    }

    pub fn is_open(&self) -> bool {
        self.state() == BreakerState::Open
    }

    pub fn stats(&self) -> (u64, u64, u64) {
        let inner = self.inner.lock();
        (inner.success, inner.failure, inner.opens)
    }

    pub fn consecutive_failures(&self) -> u32 {
        self.inner.lock().consecutive_failures
    }

    /// HalfOpen 期间被拒绝的并发请求数（用于监控"恢复瞬间请求风暴"）
    pub fn rejected_in_half_open(&self) -> u64 {
        self.inner.lock().rejected_in_half_open
    }

    pub fn probe_in_flight(&self) -> bool {
        self.inner.lock().probe_in_flight
    }

    /// 冷却剩余秒数（Open 状态）
    pub fn remaining_cooldown_secs(&self) -> u64 {
        let inner = self.inner.lock();
        if inner.state != BreakerState::Open {
            return 0;
        }
        inner
            .opened_at
            .map(|t| self.cooldown.saturating_sub(t.elapsed()).as_secs())
            .unwrap_or(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn opens_after_threshold_and_recovers() {
        let breaker = CircuitBreaker::new(3, Duration::from_millis(80));
        assert!(breaker.allow());
        breaker.on_failure();
        breaker.on_failure();
        assert_eq!(breaker.state(), BreakerState::Closed);
        breaker.on_failure();
        assert_eq!(breaker.state(), BreakerState::Open);
        assert!(!breaker.allow());

        std::thread::sleep(Duration::from_millis(100));
        assert!(breaker.allow(), "冷却期结束后应放行探测请求");
        assert_eq!(breaker.state(), BreakerState::HalfOpen);
        breaker.on_failure();
        assert_eq!(breaker.state(), BreakerState::Open, "探测失败应重新熔断");

        std::thread::sleep(Duration::from_millis(100));
        assert!(breaker.allow());
        breaker.on_success();
        assert_eq!(breaker.state(), BreakerState::Closed);
        assert_eq!(breaker.consecutive_failures(), 0);
    }

    #[test]
    fn success_resets_counter() {
        let breaker = CircuitBreaker::new(3, Duration::from_secs(1));
        breaker.on_failure();
        breaker.on_failure();
        breaker.on_success();
        breaker.on_failure();
        breaker.on_failure();
        assert_eq!(breaker.state(), BreakerState::Closed);
    }

    /// 核心回归：HalfOpen 必须是单探针，不允许并发穿透
    #[test]
    fn half_open_allows_only_one_probe() {
        let breaker = CircuitBreaker::new(2, Duration::from_millis(50));
        breaker.on_failure();
        breaker.on_failure();
        assert_eq!(breaker.state(), BreakerState::Open);

        std::thread::sleep(Duration::from_millis(70));

        // 第一个请求拿到探针名额
        assert!(breaker.allow(), "冷却结束后第一个请求应被放行");
        assert_eq!(breaker.state(), BreakerState::HalfOpen);
        assert!(breaker.probe_in_flight());

        // 其余 19 个并发请求必须全部被拒绝
        let mut allowed = 0;
        for _ in 0..19 {
            if breaker.allow() {
                allowed += 1;
            }
        }
        assert_eq!(allowed, 0, "HalfOpen 期间不允许并发请求穿透");
        assert_eq!(breaker.rejected_in_half_open(), 19);
    }

    /// 探针成功后恢复正常放行
    #[test]
    fn half_open_recovers_after_probe_success() {
        let breaker = CircuitBreaker::new(2, Duration::from_millis(40));
        breaker.on_failure();
        breaker.on_failure();
        std::thread::sleep(Duration::from_millis(60));
        assert!(breaker.allow());
        breaker.on_success();
        assert_eq!(breaker.state(), BreakerState::Closed);
        for _ in 0..20 {
            assert!(breaker.allow(), "恢复后应恢复全量放行");
        }
    }

    /// 探针卡死时不应永久堵死熔断器
    #[test]
    fn stuck_probe_is_replaced_after_timeout() {
        let breaker = CircuitBreaker::new(2, Duration::from_millis(40));
        breaker.on_failure();
        breaker.on_failure();
        std::thread::sleep(Duration::from_millis(60));
        assert!(breaker.allow(), "第一个探针");
        assert!(!breaker.allow(), "同一时刻第二个请求被拒绝");

        // 探针一直不返回，超过冷却时间后允许换新探针
        std::thread::sleep(Duration::from_millis(60));
        assert!(breaker.allow(), "探针超时后应放行新的探针");
    }
}
