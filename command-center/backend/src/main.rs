//! 资金流向指挥中心 AI Edition — 后端进程入口。
//!
//! 启动顺序（第 3.3 章）：
//!   路径解析 → 日志 → 配置 → 单实例 → SQLite → 缓存 → 任务调度 → 网络 → 采集 → 服务
//!
//! 关闭顺序（第 3.5 章）：
//!   收到退出请求 → 保存状态 → 停止任务 → 释放数据库 → 退出

#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

use std::sync::Arc;

use clap::Parser;

use capital_backend::api;
use capital_backend::config::{Config, APP_VERSION};
use capital_backend::db::Db;
use capital_backend::instance::{self, AcquireOutcome};
use capital_backend::logging;
use capital_backend::net::HttpClient;
use capital_backend::paths::AppPaths;
use capital_backend::scheduler::{self, Scheduler};
use capital_backend::state::AppState;

#[derive(Parser, Debug)]
#[command(
    name = "capital_backend",
    version = APP_VERSION,
    about = "资金流向指挥中心 AI Edition 后端服务"
)]
struct Args {
    /// 监听端口
    #[arg(long)]
    port: Option<u16>,
    /// 输出日志到控制台（开发调试用）
    #[arg(long, default_value_t = false)]
    console: bool,
    /// 日志级别
    #[arg(long, default_value = "info")]
    log_level: String,
    /// 覆盖数据目录
    #[arg(long)]
    data_dir: Option<String>,
    /// 自检模式：验证数据通道与核心链路后退出
    #[arg(long, default_value_t = false)]
    self_check: bool,
}

fn main() {
    let args = Args::parse();

    if let Some(dir) = args.data_dir.as_ref() {
        unsafe {
            std::env::set_var("CAPITAL_FLOW_HOME", dir);
        }
    }

    let paths = AppPaths::resolve();
    let _guards = logging::init(&paths.logs_dir, args.console, &args.log_level);

    tracing::info!(
        target: "app",
        version = APP_VERSION,
        root = %paths.root.display(),
        db = %paths.db_path.display(),
        "资金流向指挥中心后端启动"
    );

    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)
        .enable_all()
        .thread_name("capital-backend")
        .build()
    {
        Ok(rt) => rt,
        Err(err) => {
            tracing::error!(target: "app", "运行时创建失败: {err}");
            eprintln!("后端运行时创建失败：{err}");
            std::process::exit(1);
        }
    };

    let code = runtime.block_on(async_main(args, paths));
    std::process::exit(code);
}

async fn async_main(args: Args, paths: AppPaths) -> i32 {
    // ---------- 配置 ----------
    let (mut config, notes) = Config::load_or_init(&paths);
    if let Some(port) = args.port {
        config.port = port;
    }
    for note in &notes {
        tracing::info!(target: "app", "{note}");
    }

    // ---------- 单实例 ----------
    let (listener, port) = match instance::acquire(&paths, config.port) {
        AcquireOutcome::Bound { listener, port } => (listener, port),
        AcquireOutcome::AlreadyRunning { port } => {
            tracing::info!(target: "app", port, "已有实例在运行，本进程退出");
            if args.console {
                println!("已有实例在运行（端口 {port}），无需重复启动。");
            }
            return 0;
        }
    };
    config.port = port;
    if let Err(err) = config.save(&paths) {
        tracing::warn!(target: "app", "配置保存失败: {err}");
    }

    // ---------- 数据库 ----------
    let db = match Db::open(&paths.db_path) {
        Ok(db) => db,
        Err(err) => {
            tracing::error!(target: "app", "数据库初始化失败: {err}");
            return 2;
        }
    };

    // ---------- 网络 ----------
    let http = match HttpClient::new(&config.net) {
        Ok(c) => Arc::new(c),
        Err(err) => {
            tracing::error!(target: "app", "网络层初始化失败: {err}");
            return 3;
        }
    };

    // ---------- 状态 ----------
    let state = AppState::new(paths.clone(), config, notes, http, db);

    if args.self_check {
        run_self_check(&state).await;
        return 0;
    }

    // ---------- 调度器 ----------
    let scheduler = Scheduler::spawn(state.clone());

    // ---------- 启动初始化放到后台，HTTP 服务立即就绪 ----------
    // 用户双击后马上能看到 Splash 与进度，而不是等待几十秒（第 6.14 章）
    {
        let init_state = state.clone();
        tokio::spawn(async move {
            scheduler::bootstrap(init_state).await;
        });
    }

    // ---------- HTTP 服务 ----------
    let router = api::build_router(state.clone());
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    tracing::info!(target: "app", %addr, "HTTP 服务已就绪");

    // 单实例检查使用 std 监听器，此处转换为 tokio 监听器
    if let Err(err) = listener.set_nonblocking(true) {
        tracing::error!(target: "app", "监听器设置失败: {err}");
        return 5;
    }
    let listener = match tokio::net::TcpListener::from_std(listener) {
        Ok(l) => l,
        Err(err) => {
            tracing::error!(target: "app", "监听器初始化失败: {err}");
            return 5;
        }
    };

    let shutdown_state = state.clone();
    let server = axum::serve(
        listener,
        router.into_make_service_with_connect_info::<std::net::SocketAddr>(),
    )
    .with_graceful_shutdown(async move {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                tracing::info!(target: "app", "收到中断信号");
            }
            _ = shutdown_state.shutdown.notified() => {
                tracing::info!(target: "app", "收到退出请求");
            }
        }
    });

    let result = server.await;

    // ---------- 关闭流程 ----------
    tracing::info!(target: "app", "开始安全退出");
    state.begin_shutdown();
    state.persist_state();
    scheduler.shutdown().await;
    // 收尾写入：设置较短的忙等待，避免因其它连接占用而拖长退出时间
    let _ = state.db.call_sync(|c| {
        c.busy_timeout(std::time::Duration::from_millis(800)).ok();
        c.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);").ok();
        Ok(())
    });
    instance::cleanup(&paths);
    tracing::info!(target: "app", "已安全退出");

    match result {
        Ok(()) => 0,
        Err(err) => {
            tracing::error!(target: "app", "服务异常终止: {err}");
            4
        }
    }
}

/// 自检：不启动长期服务，仅验证数据通道与核心链路
async fn run_self_check(state: &Arc<AppState>) {
    let started = std::time::Instant::now();
    println!("=== 资金流向指挥中心 后端自检 ===");
    println!("版本: {}", APP_VERSION);
    println!("数据目录: {}", state.paths.root.display());

    let clock = state.clock.read().clone();
    let status = clock.status();
    println!("市场状态: {} ({:?})", status.label(), status);
    println!("当前交易日: {}", state.current_trade_date());
    println!("内置休市日: {} 天", clock.holiday_count());

    let idx = capital_backend::collector::index::collect_indices(state).await;
    println!(
        "指数通道: {:?} / 数据 {} 条 / {}",
        idx.source,
        idx.indices.len(),
        if idx.message.is_empty() {
            "正常".to_string()
        } else {
            idx.message.clone()
        }
    );

    // ---- 全市场股票宇宙（本次修复的核心）----
    let t0 = std::time::Instant::now();
    let symbols = capital_backend::universe::ensure_symbol_list(state).await;
    println!(
        "证券列表: {} 只（耗时 {}ms）",
        symbols.len(),
        t0.elapsed().as_millis()
    );
    let boards = symbols.iter().fold(std::collections::HashMap::new(), |mut m, s| {
        *m.entry(s.board.clone()).or_insert(0usize) += 1;
        m
    });
    let mut bl: Vec<_> = boards.into_iter().collect();
    bl.sort_by(|a, b| b.1.cmp(&a.1));
    println!("  板块分布: {:?}", bl);

    let t1 = std::time::Instant::now();
    let uni_snap = capital_backend::universe::collect_full_universe(state).await;
    let us = &uni_snap.stats;
    println!(
        "全市场行情: total={} loaded={} missing={} batches={} source={} 耗时 {}ms",
        us.total_symbols,
        us.loaded_symbols,
        us.missing_symbols,
        us.batches,
        us.source,
        t1.elapsed().as_millis()
    );
    println!("  完整性: complete={} 覆盖率={:.1}%", us.complete, us.coverage_pct());
    println!("  说明: {}", us.note);

    // ---- 时间上下文 ----
    let mtc = capital_backend::market_time::resolve(state);
    println!(
        "时间上下文: 系统={} 数据日={} 行情时间={} 模式={} 定稿={}",
        mtc.system_time, mtc.trade_date, mtc.data_timestamp, mtc.data_mode_label, mtc.is_final
    );
    println!("  {}", mtc.note);

    // ---- 历史库 ----
    let cov = capital_backend::history::preload::preload(state, &state.history).await;
    println!(
        "历史库: 期望 {} 个交易日，就绪 {}，缺失 {}，最新交易日 {} 分钟点数 {}",
        cov.days_requested,
        cov.dates_minute_ready.len(),
        cov.dates_minute_missing.len(),
        cov.latest_trading_day,
        cov.latest_minute_points
    );
    println!("  {}", cov.source_note);
    println!("  磁盘占用: {} 字节", cov.size_bytes);

    let uni = capital_backend::collector::market::run_full_market_cycle(state).await;
    println!(
        "个股池通道: {:?} / {} 只 / 请求 {} 次 / {}",
        uni.source,
        uni.stocks.len(),
        uni.requests,
        uni.message
    );

    let sec = capital_backend::collector::sector::collect_sectors(state).await;
    println!(
        "板块通道: {:?} / {} 个 / {}",
        sec.source,
        sec.sectors.len(),
        sec.message
    );

    if !uni.stocks.is_empty() {
        let engine = state.engine();
        let as_of = capital_backend::clock::now_cn().format("%H:%M").to_string();
        let sectors = sec.sectors.clone();
        let boards = engine.compute(&uni.stocks, &sectors, &as_of, &state.tracker);
        println!("九榜计算: {} 个榜单", boards.len());
        for b in boards.iter() {
            let head = b
                .entries
                .first()
                .map(|e| format!("{} {}", e.code, e.name))
                .unwrap_or_else(|| "—".to_string());
            println!("  {:<12} 榜首: {:<18} 条目 {}", b.name, head, b.entries.len());
        }
        let agg = state.tracker.aggregates(engine.boards(), 10);
        println!("九榜统计: {} 只个股上榜", agg.len());
        for a in agg.iter().take(5) {
            println!(
                "  {} {} 上榜 {} 次 / 命中 {} 榜 {:?}",
                a.code, a.name, a.appear_count, a.board_count, a.board_labels
            );
        }
    }

    let net = state.http.stats();
    println!(
        "网络: 请求 {} 次，成功 {}，失败 {}，去重 {}，平均延迟 {}ms",
        net.total_requests, net.success, net.failed, net.deduped, net.avg_latency_ms
    );
    let db = state.db.stats();
    println!(
        "数据库: {} 字节，快照 {}，榜单事件 {}，分钟行 {}",
        db.size_bytes, db.snapshots, db.ranking_events, db.minute_rows
    );
    println!("自检耗时: {}ms", started.elapsed().as_millis());
    println!("=== 自检完成 ===");
}
