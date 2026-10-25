//! 网络层：限流 / 熔断 / 退避 / 去重 / 主机轮换。

pub mod circuit_breaker;
pub mod client;
pub mod error;
pub mod rate_limiter;

pub use circuit_breaker::{BreakerState, CircuitBreaker};
pub use client::{parse_json_body, HttpClient};
pub use error::NetError;
pub use rate_limiter::{RateLimiter, RateLimiterConfig};
