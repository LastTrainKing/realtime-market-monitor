//! 资金流向指挥中心 AI Edition — Rust 后端核心库。
//!
//! 进程职责（第 3.2 章）：
//!   本 crate 是全部业务逻辑的唯一归属：数据采集、处理、计算、缓存、
//!   数据库、任务调度、网络请求、异常恢复。
//!   Tauri 只负责窗口与生命周期；React 只负责渲染。

pub mod api;
pub mod cache;
pub mod clock;
pub mod collector;
pub mod config;
pub mod db;
pub mod engine;
pub mod history;
pub mod instance;
pub mod logging;
pub mod market_time;
pub mod models;
pub mod net;
pub mod paths;
pub mod scheduler;
pub mod security;
pub mod source;
pub mod state;
pub mod testutil;
pub mod universe;

pub use config::APP_VERSION;
pub use state::AppState;
