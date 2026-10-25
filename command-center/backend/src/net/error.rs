//! 网络错误分类（第 3.6 / 6.12 / 8.5 章）。
//!
//! 必须区分：网络不存在 / 数据源异常 / 接口限流 / IP 限制 / 请求超时 / 服务器异常，
//! 不同原因向用户展示不同状态，禁止统一显示“无法连接数据源”。

use serde::Serialize;
use std::fmt;

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "snake_case", tag = "kind", content = "detail")]
pub enum NetError {
    /// 网络不存在（DNS 解析失败 / 无网卡）
    NetworkUnavailable(String),
    /// 无法连接目标主机
    Connect(String),
    /// 请求超时
    Timeout(String),
    /// 被拒绝：403 / 封禁特征
    Forbidden(String),
    /// 接口限流：429
    RateLimited(String),
    /// 服务器异常：5xx
    ServerError(String),
    /// 返回内容为空
    EmptyBody(String),
    /// 返回内容无法解析（字段变化）
    Parse(String),
    /// 熔断中，暂停请求
    CircuitOpen(String),
    /// 数据源被配置禁用
    Disabled(String),
    /// 并发合并失败
    Coalesced(String),
}

impl NetError {
    /// 面向普通用户的中文提示（禁止暴露技术细节，第 6.12 章）
    pub fn user_message(&self) -> &'static str {
        match self {
            NetError::NetworkUnavailable(_) => "网络未连接，请检查网络后软件会自动重试",
            NetError::Connect(_) => "数据源连接异常，系统正在自动恢复",
            NetError::Timeout(_) => "数据源响应超时，正在使用最近数据",
            NetError::Forbidden(_) => "数据源访问受限，已自动降频保护",
            NetError::RateLimited(_) => "数据源请求过于频繁，已自动降频",
            NetError::ServerError(_) => "数据源服务器暂时异常，稍后自动恢复",
            NetError::EmptyBody(_) => "数据源返回为空，正在尝试其他通道",
            NetError::Parse(_) => "数据格式发生变化，正在尝试其他通道",
            NetError::CircuitOpen(_) => "该数据通道暂时熔断，稍后自动恢复",
            NetError::Disabled(_) => "该数据通道未启用",
            NetError::Coalesced(_) => "数据请求合并中",
        }
    }

    /// 是否属于 IP 风险信号（触发降频，第 8.5 章）
    pub fn is_ip_risk(&self) -> bool {
        matches!(
            self,
            NetError::Forbidden(_) | NetError::RateLimited(_) | NetError::EmptyBody(_)
        )
    }

    /// 是否为“主机不可达”级别错误（DNS 失败 / 连接被拒）
    ///
    /// 这类错误不应做指数退避，应快速失败并切换数据源（第 8.5 / 8.16 章）。
    pub fn is_host_unreachable(&self) -> bool {
        matches!(self, NetError::NetworkUnavailable(_) | NetError::Connect(_))
    }

    /// 是否值得重试（指数退避）
    pub fn is_retryable(&self) -> bool {
        matches!(
            self,
            NetError::Connect(_)
                | NetError::Timeout(_)
                | NetError::ServerError(_)
                | NetError::EmptyBody(_)
                | NetError::RateLimited(_)
        )
    }

    pub fn detail(&self) -> &str {
        match self {
            NetError::NetworkUnavailable(s)
            | NetError::Connect(s)
            | NetError::Timeout(s)
            | NetError::Forbidden(s)
            | NetError::RateLimited(s)
            | NetError::ServerError(s)
            | NetError::EmptyBody(s)
            | NetError::Parse(s)
            | NetError::CircuitOpen(s)
            | NetError::Disabled(s)
            | NetError::Coalesced(s) => s,
        }
    }

    pub fn kind(&self) -> &'static str {
        match self {
            NetError::NetworkUnavailable(_) => "NETWORK_UNAVAILABLE",
            NetError::Connect(_) => "CONNECT_FAILED",
            NetError::Timeout(_) => "TIMEOUT",
            NetError::Forbidden(_) => "IP_FORBIDDEN",
            NetError::RateLimited(_) => "RATE_LIMITED",
            NetError::ServerError(_) => "SERVER_ERROR",
            NetError::EmptyBody(_) => "EMPTY_BODY",
            NetError::Parse(_) => "PARSE_ERROR",
            NetError::CircuitOpen(_) => "CIRCUIT_OPEN",
            NetError::Disabled(_) => "SOURCE_DISABLED",
            NetError::Coalesced(_) => "COALESCED",
        }
    }
}

impl fmt::Display for NetError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[{}] {}", self.kind(), self.detail())
    }
}

impl std::error::Error for NetError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ip_risk_detection() {
        assert!(NetError::Forbidden("403".into()).is_ip_risk());
        assert!(NetError::RateLimited("429".into()).is_ip_risk());
        assert!(NetError::EmptyBody("empty".into()).is_ip_risk());
        assert!(!NetError::Timeout("t".into()).is_ip_risk());
    }

    #[test]
    fn unreachable_classification() {
        assert!(NetError::NetworkUnavailable("dns".into()).is_host_unreachable());
        assert!(NetError::Connect("refused".into()).is_host_unreachable());
        assert!(!NetError::Timeout("t".into()).is_host_unreachable());
        assert!(!NetError::Forbidden("403".into()).is_host_unreachable());
    }

    #[test]
    fn retryable_classification() {
        assert!(NetError::Timeout("t".into()).is_retryable());
        assert!(NetError::ServerError("500".into()).is_retryable());
        assert!(!NetError::Forbidden("403".into()).is_retryable());
        assert!(!NetError::Parse("p".into()).is_retryable());
    }

    #[test]
    fn user_messages_are_plain_language() {
        let err = NetError::Forbidden("403 from push2".into());
        let msg = err.user_message();
        assert!(!msg.contains("403"));
        assert!(msg.contains("降频"));
    }
}
