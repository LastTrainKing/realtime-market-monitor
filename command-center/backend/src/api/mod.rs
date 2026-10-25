//! Axum REST API（第 2.1 / 9.6 章）。
//!
//! 路由层只做参数解析与状态读取，全部业务逻辑位于 service / engine / collector。

pub mod dto;
pub mod handlers;
pub mod routes;

pub use routes::build_router;
