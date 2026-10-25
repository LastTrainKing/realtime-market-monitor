/**
 * 与后端 REST 接口一一对应的类型定义。
 * 前端不包含任何业务计算，只做展示。
 */

export type DataStatus =
  | 'REALTIME'
  | 'CACHE'
  | 'REBUILD'
  | 'DELAY'
  | 'ERROR'
  | 'UNAVAILABLE'
  | 'WAITING'
  | 'PARTIAL'
  | 'DATA_SOURCE_LIMITATION'

export type DataSource =
  | 'eastmoney_realtime'
  | 'eastmoney_history'
  | 'sina_realtime'
  | 'tencent_realtime'
  | 'memory_cache'
  | 'database_replay'
  | 'none'

export interface Envelope<T> {
  data: T
  status: DataStatus
  source: DataSource
  source_label: string
  status_label: string
  updated_at: string
  age_secs: number
  message: string
}

export interface StockTick {
  code: string
  name: string
  market: number
  price: number
  change_pct: number
  change_amount: number
  open: number
  high: number
  low: number
  prev_close: number
  volume: number
  amount: number
  amplitude: number
  turnover_rate: number
  volume_ratio: number
  main_net_inflow: number
  main_net_ratio: number
  super_large_net: number
  large_net: number
  medium_net: number
  small_net: number
  is_st: boolean
}

export interface SectorTick {
  code: string
  name: string
  kind: string
  change_pct: number
  main_net_inflow: number
  main_net_ratio: number
  amount: number
  volume: number
  turnover_rate: number
  up_count: number
  down_count: number
  leader_code: string
  leader_name: string
  leader_change_pct: number
}

export interface IndexTick {
  code: string
  name: string
  price: number
  change_pct: number
  change_amount: number
  volume: number
  amount: number
  high: number
  low: number
  open: number
  prev_close: number
  main_net_inflow: number | null
}

/** 市场资金曲线（必须携带口径信息，避免把样本口径误读为全市场） */
export interface MarketFlowSeries {
  points: FlowPoint[]
  /** full = 全市场扫描；sample = 活跃股样本 */
  scope: string
  sample_size: number
  note: string
}

export interface MarketBreadth {
  up_count: number
  down_count: number
  flat_count: number
  limit_up_count: number
  limit_down_count: number
  total_amount: number
  total_main_net: number
  up_ratio: number
  /** 本次统计实际覆盖的股票数 */
  sample_size: number
  /** full = 全市场；partial = 未取全；source = 数据源现成汇总 */
  scope: string
  source: string
  sentiment: number
  /** 全市场应有股票数（用于展示"已覆盖 N / 共 M"） */
  total_symbols?: number
  /** 缺失股票数 */
  missing_symbols?: number
  /** 是否达到"全市场完整"标准 */
  complete?: boolean
}

export interface RankEntry {
  rank: number
  code: string
  name: string
  value: number
  value_label: string
  change_pct: number
  price: number
  volume_ratio: number
  turnover_rate: number
  main_net_inflow: number
  amount: number
  volume: number
  rank_delta: number
  first_seen: string
  appear_count: number
  boards: string[]
  history: string[]
  /** 板块类型（industry / concept / region）；仅板块类条目有值 */
  kind?: string
}

export interface BoardResult {
  id: string
  name: string
  kind: string
  scope: string
  size: number
  entries: RankEntry[]
  as_of: string
  /** 该榜单当前是否有有效数据 */
  available: boolean
  /** 不可用原因（面向用户的中文说明） */
  note: string
}

export interface FlowPoint {
  time: string
  main_net: number
  super_large: number
  large: number
  medium: number
  small: number
}

export interface MinutePoint {
  code: string
  trade_date: string
  minute: string
  price: number
  volume: number
  amount: number
  main_net_inflow: number
}

export interface StockAggregate {
  code: string
  name: string
  appear_count: number
  board_count: number
  boards: string[]
  board_labels: string[]
  history: string[]
  best_rank: number
  last_rank: number
  last_seen: string
  main_net_inflow: number
  change_pct: number
  price: number
  score: number
  active: boolean
}

export interface NodeState {
  node: string
  status: DataStatus
  status_label: string
  source: DataSource
  source_label: string
  note: string
  available: boolean
}

export interface DashboardResponse {
  indices: Envelope<IndexTick[]>
  breadth: Envelope<MarketBreadth>
  sectors: Envelope<SectorTick[]>
  boards: Envelope<BoardResult[]>
  top_stocks: Envelope<StockTick[]>
  capital_curve: Envelope<MarketFlowSeries>
  node_summary: NodeState[]
  market_status: string
  market_status_label: string
  trade_date: string
  server_time: string
  degraded: boolean
  cycle: number
}

export interface OpeningNode {
  node: string
  status: DataStatus
  status_label: string
  source: DataSource
  source_label: string
  created_at: string
  origin: string
  note: string
  volume_top: RankEntry[]
  netflow_top: RankEntry[]
}

export interface OpeningResponse {
  trade_date: string
  nodes: OpeningNode[]
  note: string
}

export interface AnomalyNode {
  node: string
  status: DataStatus
  status_label: string
  origin: string
  ratio_top: RankEntry[]
  volume_top: RankEntry[]
  netflow_top: RankEntry[]
}

export interface AnomalyResponse {
  trade_date: string
  nodes: AnomalyNode[]
  note: string
}

export interface SelectionResponse {
  trade_date: string
  as_of: string
  stocks: StockAggregate[]
  total_events: number
  note: string
}

export interface StockDetailResponse {
  tick: Envelope<StockTick>
  flow: Envelope<FlowPoint[]>
  aggregates: StockAggregate[]
  minute: Envelope<MinutePoint[]>
}

export interface ReviewDay {
  trade_date: string
  snapshots: number
  events: number
  minute_rows: number
  first_node: string
  last_node: string
  complete: boolean
}

export interface ReviewDaysResponse {
  days: ReviewDay[]
}

export interface ReviewDayResponse {
  trade_date: string
  snapshots: OpeningSnapshotRaw[]
  boards: BoardResult[]
  aggregates: StockAggregate[]
}

export interface OpeningSnapshotRaw {
  trade_date: string
  node: string
  origin: string
  status: DataStatus
  source: DataSource
  created_at: string
  volume_top: RankEntry[]
  netflow_top: RankEntry[]
  ratio_top: RankEntry[]
  anomaly_volume_top: RankEntry[]
  anomaly_netflow_top: RankEntry[]
  sector_netflow_top: RankEntry[]
  breadth: MarketBreadth
  note: string
}

export interface CollectorStatus {
  name: string
  label: string
  last_run: string
  last_success: string
  last_error: string
  ok: boolean
  latency_ms: number
  runs: number
  failures: number
  data_points: number
  status: DataStatus
  source: DataSource
}

export interface HostHealth {
  host: string
  state: string
  success: number
  failure: number
  avg_latency_ms: number
}

export interface GatedSource {
  name: string
  label: string
  remaining_secs: number
  reason: string
}

export interface SystemStatus {
  version: string
  status: string
  market_status: string
  market_status_label: string
  trade_date: string
  server_time: string
  uptime_secs: number
  collectors: CollectorStatus[]
  net: {
    total_requests: number
    success: number
    failed: number
    deduped: number
    rate_limited: number
    circuit_open: boolean
    circuit_opens: number
    avg_latency_ms: number
    hosts: HostHealth[]
  }
  db: {
    path: string
    size_bytes: number
    snapshots: number
    ranking_events: number
    minute_rows: number
    trading_days: number
    last_write: string
  }
  cache: {
    entries: number
    hits: number
    misses: number
    hit_rate: number
    memory_kb: number
  }
  notes: string[]
  gated_sources?: GatedSource[]
}

export interface HealthResponse {
  status: string
  version: string
  service: string
  uptime_secs: number
  trade_date: string
  market_status: string
}

export interface BoardConfig {
  id: string
  name: string
  kind: string
  scope: string
  size: number
  enabled: boolean
  descending: boolean
}

export interface AppConfig {
  version: string
  port: number
  auto_refresh: boolean
  fullscreen: boolean
  confirm_on_exit: boolean
  refresh: {
    index_interval_secs: number
    sector_interval_secs: number
    market_interval_secs: number
    full_scan_interval_secs: number
    snapshot_interval_secs: number
    minute_persist_secs: number
  }
  net: {
    requests_per_second: number
    requests_per_minute: number
    max_concurrent: number
    timeout_secs: number
    connect_timeout_secs: number
    retry_max: number
    breaker_failure_threshold: number
    breaker_cooldown_secs: number
    min_interval_ms: number
    proxy: string | null
  }
  boards: BoardConfig[]
  nodes: string[]
  top_n: number
  top_open: number
  holidays: string[]
  stock_batch_size: number
  cache_ttl_secs: number
  retention_days: number
  collect_after_close: boolean
  enable_eastmoney: boolean
  enable_sina: boolean
  enable_tencent: boolean
  update_url: string
}

export interface MessageResponse {
  ok: boolean
  message: string
}

/* ------------------------------------------------------------------ *
 * 历史静态分钟库（首页四个核心模块统一数据源）
 * ------------------------------------------------------------------ */

/** 历史分钟 bar。字段与后端 history::MinuteBar 一一对应。 */
export interface MinuteBar {
  code: string
  trade_date: string
  /** HH:MM */
  minute: string
  /** 北京时间毫秒时间戳 */
  dt_ms: number
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number
  main_net_inflow: number
}

/** 数据模式（后端 DataMode，SCREAMING_SNAKE_CASE） */
export type DataMode =
  | 'REALTIME'
  | 'DELAYED'
  | 'HISTORICAL_CLOSE'
  | 'STATIC'
  | 'UNAVAILABLE'

/** 数据源连接状态（后端 SourceLink，SCREAMING_SNAKE_CASE） */
export type SourceLink = 'CONNECTED' | 'DEGRADED' | 'DISCONNECTED'

/**
 * 统一时间上下文。
 * 后端刻意把「系统时间 / 服务器时间 / 交易日 / 行情时间 / 抓取时间」拆开，
 * 前端不允许用其中任何一个去冒充另一个。
 */
export interface MarketTimeContext {
  system_time: string
  server_time: string
  server_time_estimated: boolean
  trade_date: string
  /** 行情最后有效时间 */
  data_timestamp: string
  /** 本次抓取时间（≠ 行情时间） */
  data_fetch_time: string
  market_status: string
  market_status_label: string
  data_mode: DataMode
  data_mode_label: string
  is_final: boolean
  source_link: SourceLink
  source_link_label: string
  note: string
}

/** 历史库覆盖情况 */
export interface HistoryCoverage {
  days_requested: number
  dates_expected: string[]
  dates_minute_ready: string[]
  dates_minute_missing: string[]
  latest_trading_day: string
  latest_minute_points: number
  source_note: string
  size_bytes: number
}

/**
 * 统一数据快照：首页四个核心模块必须全部从它读取，
 * 避免各组件各自取「当前时间 / 最新数据」造成数据错位。
 */
/** 分钟序列当日汇总。**由后端计算**，前端只展示，不做任何聚合运算。 */
export interface SeriesSummary {
  points: number
  first_minute: string
  last_minute: string
  open: number
  last: number
  high: number
  low: number
  /** 相对昨收的涨跌额；无基准时为 null */
  change: number | null
  change_pct: number | null
  /** 日内振幅（%）；无基准时为 null */
  amplitude: number | null
  amount: number
  volume: number
  note: string
}

export interface SnapshotResponse {
  time: MarketTimeContext
  /** 市场时钟给出的数据交易日 */
  trade_date: string
  /** 分钟序列**实际所属**的交易日（可能因当天数据未落盘而回退） */
  series_date: string
  /** 是否发生了交易日回退；为 true 时界面必须标注"非当天数据" */
  series_fallback: boolean
  /** 指数分时（完整交易日 09:31..15:00） */
  index_series: MinuteBar[]
  /** 两市资金曲线（完整交易日，累计口径） */
  flow_series: MinuteBar[]
  index_complete: boolean
  flow_complete: boolean
  /** 分时图基准昨收；仅在画的是当前交易日时给出 */
  index_prev_close: number | null
  index_code: string
  index_name: string
  /** 指数分时当日汇总（后端计算） */
  index_summary: SeriesSummary
  /** 两市资金曲线当日汇总（后端计算） */
  flow_summary: SeriesSummary
  coverage: HistoryCoverage
  /** 数据源能力说明（如实告知哪些历史数据不可回溯） */
  capability_note: string
  /** 数据缺失原因（有缺失时非空） */
  missing_note: string
}

/** 时点数据面板：与资金曲线同一份数据集、同一交易日 */
export interface TimelinePointResponse {
  trade_date: string
  minute: string
  index_close: number | null
  main_net: number | null
  super_large: number | null
  large: number | null
  medium: number | null
  small: number | null
  available: boolean
  note: string
}

/** 板块资金流入 / 流出对比（首页核心模块 3） */
export interface SectorCompareResponse {
  trade_date: string
  inflow_top: RankEntry[]
  outflow_top: RankEntry[]
  note: string
  status: DataStatus
  /** 数据来源通道（东方财富 / 腾讯财经 …） */
  source: string
  board_count: number
  industry_count: number
  concept_count: number
  /** 概念板块数量明显偏少（通常是降级通道所致，东财全量 400+） */
  concept_truncated: boolean
}

/** 单粒度数据源可回溯能力（实测值，非估算） */
export interface GranularityCapability {
  minutes: number
  label: string
  source_max_days: number
  meets_requirement: boolean
}

/** 历史库状态清单行 */
export interface ManifestRow {
  trade_date: string
  granularity: number
  expected_symbols: number
  actual_symbols: number
  missing_symbols: number
  total_rows: number
  complete: boolean
  created_at: string
  updated_at: string
  note: string
}

/** 历史数据状态诊断 */
export interface HistoryStatusResponse {
  duckdb_version: string
  root: string
  size_bytes: number
  dates: string[]
  coverage: HistoryCoverage
  manifest: ManifestRow[]
  granularity_capability: GranularityCapability[]
  granularity: number
  granularity_label: string
  capability_note: string
  missing_note: string
}
