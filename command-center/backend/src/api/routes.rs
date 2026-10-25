//! 路由装配。

use axum::Router;
use axum::extract::{Request, State};
use axum::http::{HeaderName, Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::Response;
use axum::routing::{delete, get, post};
use std::sync::Arc;
use tower_http::cors::{AllowOrigin, CorsLayer};
use tower_http::trace::TraceLayer;

use crate::security;
use crate::state::AppState;

use super::handlers;

/// 本地 API 鉴权中间件。
///
/// 只监听 127.0.0.1 并不足以防止浏览器中的任意网页发起请求，
/// 因此除健康检查外，所有接口都必须携带启动时生成的 `X-CF-Token`。
async fn require_token(
    State(state): State<Arc<AppState>>,
    req: Request,
    next: Next,
) -> Result<Response, (StatusCode, axum::Json<super::dto::MessageResponse>)> {
    let path = req.uri().path().to_string();
    if security::is_public_path(&path) {
        return Ok(next.run(req).await);
    }

    let provided = req
        .headers()
        .get("x-cf-token")
        .and_then(|v| v.to_str().ok())
        .unwrap_or_default();

    if provided == state.api_token {
        return Ok(next.run(req).await);
    }

    tracing::warn!(target: "app", path = %path, "本地接口调用被拒绝：令牌缺失或错误");
    Err((
        StatusCode::UNAUTHORIZED,
        axum::Json(super::dto::MessageResponse {
            ok: false,
            message: "本地访问令牌校验失败".into(),
        }),
    ))
}

pub fn build_router(state: Arc<AppState>) -> Router {
    // CORS 白名单：只允许本程序自身的前端来源，禁止 Any
    let origins: Vec<axum::http::HeaderValue> = security::allowed_origins(5183)
        .into_iter()
        .filter_map(|o| o.parse().ok())
        .collect();
    let cors = CorsLayer::new()
        .allow_origin(AllowOrigin::list(origins))
        .allow_methods([
            Method::GET,
            Method::POST,
            Method::PUT,
            Method::DELETE,
            Method::OPTIONS,
        ])
        .allow_headers([
            header::CONTENT_TYPE,
            header::ACCEPT,
            HeaderName::from_static("x-cf-token"),
        ]);

    Router::new()
        .route("/api/health", get(handlers::health))
        .route("/api/client-log", post(handlers::client_log))
        .route("/api/system/status", get(handlers::system_status))
        .route("/api/config", get(handlers::get_config).put(handlers::put_config))
        .route("/api/shutdown", post(handlers::shutdown))
        .route("/api/dashboard", get(handlers::dashboard))
        .route("/api/snapshot", get(handlers::snapshot))
        .route("/api/timeline/point", get(handlers::timeline_point))
        .route("/api/sector/compare", get(handlers::sector_compare))
        .route("/api/history/status", get(handlers::history_status))
        .route("/api/history/sync", post(handlers::history_sync))
        .route("/api/market/indices", get(handlers::indices))
        .route("/api/market/breadth", get(handlers::breadth))
        .route("/api/market/sectors", get(handlers::sectors))
        .route("/api/boards", get(handlers::boards))
        .route("/api/boards/{id}", get(handlers::board_by_id))
        .route("/api/stocks", get(handlers::universe))
        .route("/api/stocks/{code}", get(handlers::stock_detail))
        .route("/api/capital/curve", get(handlers::capital_curve))
        .route("/api/nodes", get(handlers::node_states))
        .route("/api/nodes/{node}", get(handlers::node_detail))
        .route("/api/nodes/{node}/rebuild", post(handlers::rebuild_node))
        .route("/api/opening", get(handlers::opening))
        .route("/api/anomaly", get(handlers::anomaly))
        .route("/api/selection", get(handlers::selection))
        .route("/api/review/days", get(handlers::review_days))
        .route("/api/review/{date}", get(handlers::review_day))
        .route("/api/watchlist", get(handlers::watchlist).post(handlers::add_watch))
        .route("/api/watchlist/{code}", delete(handlers::remove_watch))
        .layer(middleware::from_fn_with_state(state.clone(), require_token))
        .with_state(state)
        .layer(TraceLayer::new_for_http())
        .layer(cors)
}

/// 健康检查路由（供启动器快速探测，不依赖业务状态）
pub fn health_only() -> Router {
    Router::new().route(
        "/api/health",
        get(|| async { axum::Json(serde_json::json!({"status":"ok"})) }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::db::Db;
    use crate::net::HttpClient;
    use crate::paths::AppPaths;

    fn state(dir: &std::path::Path) -> Arc<AppState> {
        let paths = AppPaths {
            root: dir.to_path_buf(),
            data_dir: dir.join("data"),
            db_path: dir.join("data/db.sqlite"),
            logs_dir: dir.join("logs"),
            config_dir: dir.join("config"),
            cache_dir: dir.join("cache"),
            install_dir: dir.to_path_buf(),
            resources_dir: dir.join("res"),
        };
        std::fs::create_dir_all(&paths.data_dir).unwrap();
        let db = Db::open(&paths.db_path).unwrap();
        let cfg = Config::default();
        let http = Arc::new(HttpClient::new(&cfg.net).unwrap());
        AppState::new(paths, cfg, vec![], http, db)
    }

    #[test]
    fn router_builds_with_all_routes() {
        let tmp = tempfile::tempdir().unwrap();
        let s = state(tmp.path());
        let _router = build_router(s);
        // 构建成功即表示全部路由签名合法
        assert!(true);
    }

    #[tokio::test]
    async fn health_endpoint_is_public() {
        use axum::body::Body;
        use axum::http::Request;
        use tower::ServiceExt;

        let tmp = tempfile::tempdir().unwrap();
        let s = state(tmp.path());
        let app = build_router(s);
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/api/health")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), 200, "健康检查必须免鉴权（用于启动探测）");
    }

    /// 安全边界：没有令牌的接口调用必须被拒绝
    #[tokio::test]
    async fn api_requires_token() {
        use axum::body::Body;
        use axum::http::Request;
        use tower::ServiceExt;

        let tmp = tempfile::tempdir().unwrap();
        let s = state(tmp.path());
        let app = build_router(s.clone());
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/api/dashboard")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), 401, "无令牌访问必须返回 401");

        // 错误令牌同样拒绝
        let app2 = build_router(s.clone());
        let resp2 = app2
            .oneshot(
                Request::builder()
                    .uri("/api/dashboard")
                    .header("x-cf-token", "wrong-token")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp2.status(), 401);
    }

    /// 写接口在无令牌时必须拒绝（防止本机任意网页操作软件）
    #[tokio::test]
    async fn mutating_endpoints_require_token() {
        use axum::body::Body;
        use axum::http::Request;
        use tower::ServiceExt;

        let tmp = tempfile::tempdir().unwrap();
        let s = state(tmp.path());
        for (method, uri) in [
            ("POST", "/api/shutdown"),
            ("PUT", "/api/config"),
            ("POST", "/api/watchlist"),
        ] {
            let app = build_router(s.clone());
            let resp = app
                .oneshot(
                    Request::builder()
                        .method(method)
                        .uri(uri)
                        .header("content-type", "application/json")
                        .body(Body::from("{}"))
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(resp.status(), 401, "{method} {uri} 无令牌必须被拒绝");
        }
    }

    #[tokio::test]
    async fn dashboard_returns_initializing_state_without_panic() {
        use axum::body::Body;
        use axum::http::Request;
        use tower::ServiceExt;

        let tmp = tempfile::tempdir().unwrap();
        let s = state(tmp.path());
        let token = s.api_token.clone();
        let app = build_router(s);
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/api/dashboard")
                    .header("x-cf-token", token)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), 200);
    }

    #[tokio::test]
    async fn nodes_endpoint_lists_configured_nodes() {
        use axum::body::Body;
        use axum::http::Request;
        use tower::ServiceExt;

        let tmp = tempfile::tempdir().unwrap();
        let s = state(tmp.path());
        let token = s.api_token.clone();
        let app = build_router(s);
        let resp = app
            .oneshot(
                Request::builder()
                    .uri("/api/nodes")
                    .header("x-cf-token", token)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), 200);
    }
}
