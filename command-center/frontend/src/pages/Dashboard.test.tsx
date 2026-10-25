/**
 * 首页四个核心模块 —— 渲染与数据绑定测试。
 *
 * 这里不截图，而是**真的把 Dashboard 渲染出来**，验证四个模块
 * 确实绑定了统一快照的数据、且遵守"数据缺失就说明原因而不是显示 0"的约定。
 *
 * 夹具数值取自实测（2026-09-30 上证指数 / 东财分钟资金流），
 * 便于人工对照真实数据。
 */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// ECharts 需要 canvas，jsdom 下不可用 —— 图表本体不参与断言，
// 但必须确认"确实拿到了 option"（即数据链路通到了图表层）。
vi.mock('../components/Chart', () => ({
  chartBase: { axisLine: {}, splitLine: {}, axisLabel: {} },
  default: ({ option }: { option: unknown }) => (
    <div data-testid="chart" data-has-option={option ? '1' : '0'} />
  ),
}))

vi.mock('../api/client', () => ({
  api: {
    snapshot: vi.fn(),
    dashboard: vi.fn(),
    sectorCompare: vi.fn(),
    historyStatus: vi.fn(),
    timelinePoint: vi.fn(),
    historySync: vi.fn(),
  },
}))

import { api } from '../api/client'
import type {
  DashboardResponse,
  HistoryStatusResponse,
  MinuteBar,
  SectorCompareResponse,
  SnapshotResponse,
  TimelinePointResponse,
} from '../api/types'
import { Dashboard } from './Dashboard'

/** 造一条分钟序列（真实数据：2026-09-30 上证指数 239 点，这里取 3 点做代表） */
function indexBars(): MinuteBar[] {
  return [
    { code: '1.000001', trade_date: '2026-09-30', minute: '09:31', dt_ms: 0, open: 3838.14, high: 3840.0, low: 3833.086, close: 3838.14, volume: 1e9, amount: 1e10, main_net_inflow: 0 },
    { code: '1.000001', trade_date: '2026-09-30', minute: '13:01', dt_ms: 0, open: 3843.0, high: 3844.0, low: 3841.0, close: 3843.14, volume: 1e9, amount: 1e10, main_net_inflow: 0 },
    { code: '1.000001', trade_date: '2026-09-30', minute: '15:00', dt_ms: 0, open: 3842.0, high: 3851.217, low: 3840.0, close: 3842.2, volume: 1e9, amount: 1e10, main_net_inflow: 0 },
  ]
}

/** 真实值：13:01 主力累计 -27.77 亿（超大单 +5.77 亿 + 大单 -33.54 亿） */
function flowBars(): MinuteBar[] {
  return [
    { code: '1.000001', trade_date: '2026-09-30', minute: '09:31', dt_ms: 0, open: 0, high: 0, low: 0, close: 0, volume: 0, amount: 0, main_net_inflow: 1.45466848e8 },
    { code: '1.000001', trade_date: '2026-09-30', minute: '13:01', dt_ms: 0, open: 0, high: 5.77e8, low: -33.54e8, close: 0, volume: -29.93e8, amount: 57.7e8, main_net_inflow: -27.77e8 },
    { code: '1.000001', trade_date: '2026-09-30', minute: '15:00', dt_ms: 0, open: 0, high: 3.88e8, low: -59.27e8, close: 0, volume: 0, amount: 0, main_net_inflow: -59.27e8 },
  ]
}

function snapshot(over: Partial<SnapshotResponse> = {}): SnapshotResponse {
  const idx = indexBars()
  const flw = flowBars()
  return {
    time: {
      system_time: '2026-10-02 15:43:43',
      server_time: '2026-09-30 15:00:00',
      server_time_estimated: false,
      trade_date: '2026-09-30',
      data_timestamp: '2026-09-30 15:00',
      data_fetch_time: '2026-10-02 15:43:43',
      market_status: 'NonTradingDay',
      market_status_label: '休市',
      data_mode: 'HISTORICAL_CLOSE',
      data_mode_label: '历史收盘',
      is_final: true,
      source_link: 'CONNECTED',
      source_link_label: '数据源已连接',
      note: '',
    },
    trade_date: '2026-09-30',
    series_date: '2026-09-30',
    series_fallback: false,
    index_series: idx,
    flow_series: flw,
    index_complete: true,
    flow_complete: true,
    index_prev_close: 3830.45,
    index_code: '000001',
    index_name: '上证指数',
    index_summary: {
      points: 239,
      first_minute: '09:31',
      last_minute: '15:00',
      open: 3838.14,
      last: 3842.2,
      high: 3851.217,
      low: 3833.086,
      change: 11.75,
      change_pct: 0.31,
      amplitude: 0.47,
      amount: 6.79398998016e11,
      volume: 4.14560247e10,
      note: '2026-09-30 指数分时，基准为昨收 3830.45',
    },
    flow_summary: {
      points: 240,
      first_minute: '09:31',
      last_minute: '15:00',
      open: 1.45466848e8,
      last: -59.27e8,
      high: 3.88e8,
      low: -59.27e8,
      change: null,
      change_pct: null,
      amplitude: null,
      amount: 0,
      volume: 0,
      note: '2026-09-30 两市主力资金累计净流入（股票层面合计）',
    },
    coverage: {
      days_requested: 90,
      dates_expected: [],
      dates_minute_ready: ['2026-09-30'],
      dates_minute_missing: [],
      latest_trading_day: '2026-09-30',
      latest_minute_points: 240,
      source_note: '',
      size_bytes: 1000,
    },
    capability_note: '数据源单次请求最多返回 1023 个点：1分钟可回溯 4 个交易日。',
    missing_note: '',
    ...over,
  }
}

function dashboard(): DashboardResponse {
  return {
    indices: {
      data: [
        { code: '000001', name: '上证指数', price: 3842.2, change_pct: 0.31, change_amount: 11.75, volume: 4.1e8, amount: 6.79e11, high: 3851.2, low: 3833.1, open: 3838.1, prev_close: 3830.45, main_net_inflow: null },
      ],
      status: 'REALTIME',
      source: 'eastmoney_realtime',
      source_label: '东方财富实时',
      status_label: '实时',
      updated_at: '15:00:00',
      age_secs: 1,
      message: '',
    },
    breadth: {
      data: {
        up_count: 2566, down_count: 2830, flat_count: 175, limit_up_count: 66, limit_down_count: 27,
        total_amount: 1.449854e12, total_main_net: -5.9e9, up_ratio: 47.5,
        sample_size: 5571, scope: 'full', source: '新浪财经', sentiment: 3.2,
      },
      status: 'REALTIME', source: 'sina_realtime', source_label: '新浪财经',
      status_label: '实时', updated_at: '15:00:00', age_secs: 1, message: '',
    },
    sectors: { data: [], status: 'WAITING', source: 'none', source_label: '无数据源', status_label: '等待生成', updated_at: '--:--:--', age_secs: 0, message: '' },
    boards: { data: [], status: 'WAITING', source: 'none', source_label: '无数据源', status_label: '等待生成', updated_at: '--:--:--', age_secs: 0, message: '' },
    top_stocks: { data: [], status: 'WAITING', source: 'none', source_label: '无数据源', status_label: '等待生成', updated_at: '--:--:--', age_secs: 0, message: '' },
    capital_curve: { data: { points: [], scope: 'full', sample_size: 5571, note: '' }, status: 'WAITING', source: 'none', source_label: '无数据源', status_label: '等待生成', updated_at: '--:--:--', age_secs: 0, message: '' },
    node_summary: [],
    market_status: 'NonTradingDay',
    market_status_label: '休市',
    trade_date: '2026-09-30',
    server_time: '2026-10-02 15:43:43',
    degraded: false,
    cycle: 3,
  }
}

function sectorCompare(): SectorCompareResponse {
  const entry = (rank: number, name: string, net: number, kind: string) => ({
    rank, code: `BK${rank}`, name, value: net, value_label: '', change_pct: 2.73,
    price: 0, volume_ratio: 0, turnover_rate: 0, main_net_inflow: net, amount: 0,
    volume: 0, rank_delta: 0, first_seen: '', appear_count: 0, boards: [], history: [], kind,
  })
  return {
    trade_date: '2026-09-30',
    inflow_top: [entry(1, '医药生物', 60.24e8, 'industry'), entry(2, '生物医药', 51.1e8, 'concept')],
    outflow_top: [entry(1, '芯片概念', -196.0e8, 'concept'), entry(2, 'TMT', -171.0e8, 'concept')],
    note: '板块资金流读取自本地持久化快照（交易日 2026-09-30）；来源 东方财富实时；共 4 个板块（行业 1 / 概念 3）；数据源不提供历史交易日的板块分钟资金流',
    status: 'REBUILD',
    source: '东方财富实时',
    board_count: 4,
    industry_count: 1,
    concept_count: 3,
    concept_truncated: false,
  }
}

function historyStatus(): HistoryStatusResponse {
  return {
    duckdb_version: 'v1.5.6',
    root: 'C:/data/history',
    size_bytes: 12345678,
    dates: ['2026-09-24', '2026-09-28', '2026-09-29', '2026-09-30'],
    coverage: {
      days_requested: 90, dates_expected: [], dates_minute_ready: ['2026-09-30'],
      dates_minute_missing: [], latest_trading_day: '2026-09-30',
      latest_minute_points: 240, source_note: '', size_bytes: 12345678,
    },
    manifest: [],
    granularity_capability: [
      { minutes: 1, label: '1分钟', source_max_days: 4, meets_requirement: false },
      { minutes: 30, label: '30分钟', source_max_days: 127, meets_requirement: true },
    ],
    granularity: 1,
    granularity_label: '1分钟',
    capability_note: '',
    missing_note: '',
  }
}

function timelinePoint(minute: string): TimelinePointResponse {
  return {
    trade_date: '2026-09-30',
    minute,
    index_close: 3843.14,
    main_net: -27.77e8,
    super_large: 5.77e8,
    large: -33.54e8,
    medium: -29.93e8,
    small: 57.7e8,
    available: true,
    note: `2026-09-30 ${minute} 的真实历史分钟数据`,
  }
}

const mocked = api as unknown as {
  snapshot: ReturnType<typeof vi.fn>
  dashboard: ReturnType<typeof vi.fn>
  sectorCompare: ReturnType<typeof vi.fn>
  historyStatus: ReturnType<typeof vi.fn>
  timelinePoint: ReturnType<typeof vi.fn>
  historySync: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  vi.clearAllMocks()
  mocked.snapshot.mockResolvedValue(snapshot())
  mocked.dashboard.mockResolvedValue(dashboard())
  mocked.sectorCompare.mockResolvedValue(sectorCompare())
  mocked.historyStatus.mockResolvedValue(historyStatus())
  mocked.timelinePoint.mockImplementation((m: string) => Promise.resolve(timelinePoint(m)))
})

// 本文件渲染的 DOM 较大，且轮询 Hook 会留下定时器 ——
// 显式 cleanup，避免污染同进程内后续测试文件（vitest 配置为 singleFork）。
afterEach(() => {
  cleanup()
})

/** 取某个模块卡片（BigCard 渲染为 <section>），把断言限定在卡片内部 */
function cardOf(title: string): HTMLElement {
  const el = screen.getByText(title).closest('section')
  if (!el) throw new Error(`未找到模块卡片: ${title}`)
  return el as HTMLElement
}

describe('首页四个核心模块', () => {
  it('四个模块标题都渲染出来', async () => {
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('① 指数分时')).toBeInTheDocument())
    expect(screen.getByText('② 主力资金累计曲线')).toBeInTheDocument()
    expect(screen.getByText('③ 主力资金流入流出板块对比')).toBeInTheDocument()
    expect(screen.getByText('④ 时点数据面板')).toBeInTheDocument()
  })

  it('① 指数分时绑定后端汇总值，并显示数据交易日', async () => {
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('① 指数分时')).toBeInTheDocument())
    const card = cardOf('① 指数分时')
    expect(within(card).getByText('3842.20')).toBeInTheDocument() // summary.last
    expect(within(card).getByText('+11.75')).toBeInTheDocument() // summary.change
    expect(within(card).getByText('+0.31%')).toBeInTheDocument() // summary.change_pct
    expect(within(card).getByText('3851.22')).toBeInTheDocument() // summary.high
    expect(within(card).getByText('3833.09')).toBeInTheDocument() // summary.low
    // 点数与首尾（副标题与指标格都会出现该区间）
    expect(within(card).getByText('239')).toBeInTheDocument()
    expect(within(card).getAllByText(/09:31→15:00/).length).toBeGreaterThan(0)
    // 数据交易日（卡片内的 Stat 标签，与口径条里的同名标签不冲突）
    expect(within(card).getByText('2026-09-30')).toBeInTheDocument()
    // 图表确实拿到了 option（数据链路通到图表层）
    const charts = screen.getAllByTestId('chart')
    expect(charts.length).toBeGreaterThan(0)
    expect(charts.every((c) => c.getAttribute('data-has-option') === '1')).toBe(true)
  })

  it('② 资金累计曲线标明数据源就是东财现成接口，不做累加', async () => {
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('② 主力资金累计曲线')).toBeInTheDocument())
    const card = cardOf('② 主力资金累计曲线')
    expect(within(card).getByText('最新累计主力净流入')).toBeInTheDocument()
    // 末值与谷值同为 -59.27 亿（夹具里就是如此），因此会出现两次
    expect(within(card).getAllByText('-59.27亿').length).toBe(2)
    expect(within(card).getByText('日内峰值')).toBeInTheDocument()
    expect(within(card).getByText('日内谷值')).toBeInTheDocument()
    // 口径必须写明是"东财现成接口、接口本身即累计"，不能写成"股票层面合计"
    expect(within(card).getByText('东财大盘资金流 · 接口即累计')).toBeInTheDocument()
    expect(within(card).queryByText(/股票层面合计/)).toBeNull()
    expect(within(card).getByText(/fflow\/kline/)).toBeInTheDocument()
    expect(within(card).getByText('完整交易日')).toBeInTheDocument()
  })

  it('③ 板块模块显示流入/流出两侧，并标明来源通道与覆盖数', async () => {
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('③ 主力资金流入流出板块对比')).toBeInTheDocument())
    const card = cardOf('③ 主力资金流入流出板块对比')
    expect(within(card).getByText('净流入 TOP')).toBeInTheDocument()
    expect(within(card).getByText('净流出 TOP')).toBeInTheDocument()
    await waitFor(() => expect(within(card).getByText('医药生物')).toBeInTheDocument())
    expect(within(card).getByText('芯片概念')).toBeInTheDocument()
    expect(within(card).getByText('60.24亿')).toBeInTheDocument()
    expect(within(card).getByText('-196.00亿')).toBeInTheDocument()
    // 板块类型标注：流入 1 行业 + 1 概念，流出 2 概念 → 共 3 个「概念」
    expect(within(card).getByText('行业')).toBeInTheDocument()
    expect(within(card).getAllByText('概念').length).toBe(3)
    // 来源通道与覆盖数必须显式给出（元信息条与说明文字里都会出现）
    expect(within(card).getAllByText('东方财富实时').length).toBeGreaterThan(0)
    expect(
      within(card).getAllByText(/共 4 个板块（行业 1 \/ 概念 3）/).length,
    ).toBeGreaterThan(0)
    // 非交易时段的数据来源必须写明
    expect(within(card).getAllByText(/本地持久化快照/).length).toBeGreaterThan(0)
  })

  it('③ 概念板块被截断时必须告警（降级通道覆盖不全）', async () => {
    mocked.sectorCompare.mockResolvedValue({
      ...sectorCompare(),
      source: '腾讯财经',
      board_count: 231,
      industry_count: 31,
      concept_count: 200,
      concept_truncated: true,
    })
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('③ 主力资金流入流出板块对比')).toBeInTheDocument())
    const card = cardOf('③ 主力资金流入流出板块对比')
    await waitFor(() => expect(within(card).getByText(/明显少于东方财富全量/)).toBeInTheDocument())
    expect(within(card).getAllByText('腾讯财经').length).toBeGreaterThan(0)
    expect(
      within(card).getAllByText(/共 231 个板块（行业 31 \/ 概念 200）/).length,
    ).toBeGreaterThan(0)
  })

  it('④ 时点面板默认取曲线最后一分钟，且展示四个资金分项', async () => {
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('④ 时点数据面板')).toBeInTheDocument())
    // 默认分钟 = 资金曲线最后一根 15:00
    await waitFor(() => expect(mocked.timelinePoint).toHaveBeenCalledWith('15:00', '2026-09-30'))
    const card = cardOf('④ 时点数据面板')
    await waitFor(() => expect(within(card).getByText('3843.14')).toBeInTheDocument())
    expect(within(card).getByText('主力净流入（当日累计）')).toBeInTheDocument()
    expect(within(card).getByText('超大单（累计）')).toBeInTheDocument()
    expect(within(card).getByText('大单（累计）')).toBeInTheDocument()
    expect(within(card).getByText('中单（累计）')).toBeInTheDocument()
    expect(within(card).getByText('小单（累计）')).toBeInTheDocument()
  })

  it('数据口径条把系统时间 / 行情时间 / 数据模式分开显示', async () => {
    const { container } = render(<Dashboard />)
    await waitFor(() => expect(screen.getAllByText('数据交易日').length).toBeGreaterThan(0))
    const bar = container.querySelector('[class*="provenance"]') as HTMLElement
    expect(bar).toBeTruthy()
    for (const label of ['数据交易日', '数据模式', '行情时间', '数据源', '抓取时间', '系统时间']) {
      expect(within(bar).getByText(label)).toBeInTheDocument()
    }
    expect(within(bar).getByText('历史收盘')).toBeInTheDocument()
    expect(within(bar).getByText('2026-09-30 15:00')).toBeInTheDocument() // 行情时间
    // 抓取时间与系统时间在本夹具里相同，因此会出现两次
    expect(within(bar).getAllByText('2026-10-02 15:43:43').length).toBe(2)
  })

  it('交易日回退时必须显式告警，不得冒充当天数据', async () => {
    mocked.snapshot.mockResolvedValue(
      snapshot({
        trade_date: '2026-10-08',
        series_date: '2026-09-30',
        series_fallback: true,
        index_prev_close: null,
        missing_note: '2026-10-08 的分钟数据尚未落盘，当前展示的是历史库中最近一个交易日 2026-09-30 的数据',
      }),
    )
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('（非当天）')).toBeInTheDocument())
    expect(
      screen.getByText(/2026-10-08 的分钟数据尚未落盘/),
    ).toBeInTheDocument()
  })

  it('指数分时为空时说明原因，而不是画一条 0 线', async () => {
    const s = snapshot()
    mocked.snapshot.mockResolvedValue({
      ...s,
      index_series: [],
      index_summary: { ...s.index_summary, points: 0, last: 0, high: 0, low: 0, first_minute: '', last_minute: '', change: null, change_pct: null, amplitude: null },
    })
    render(<Dashboard />)
    await waitFor(() =>
      expect(screen.getByText('2026-09-30 的指数分时尚未落盘')).toBeInTheDocument(),
    )
    expect(screen.getByText(/不会用实时值回填历史，也不会补 0/)).toBeInTheDocument()
  })

  it('板块交易日与首页统一交易日不一致时必须告警', async () => {
    mocked.sectorCompare.mockResolvedValue({ ...sectorCompare(), trade_date: '2026-09-25' })
    render(<Dashboard />)
    await waitFor(() =>
      expect(screen.getByText(/与首页统一交易日/)).toBeInTheDocument(),
    )
  })

  it('历史库状态条显示 DuckDB 版本与各粒度实测可回溯天数', async () => {
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('v1.5.6')).toBeInTheDocument())
    expect(screen.getByText('4 个')).toBeInTheDocument()          // 已落盘交易日
    expect(screen.getByText('1分钟 4日 ✗')).toBeInTheDocument()
    expect(screen.getByText('30分钟 127日 ✓')).toBeInTheDocument()
    expect(screen.getByText('增量补齐历史')).toBeInTheDocument()
  })

  it('历史库状态条可触发增量补齐', async () => {
    mocked.historySync.mockResolvedValue({ ok: true, message: '已在后台开始补齐 86 个缺失交易日（1分钟）' })
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('增量补齐历史')).toBeInTheDocument())
    const btn = screen.getByText('增量补齐历史')
    btn.click()
    await waitFor(() => expect(mocked.historySync).toHaveBeenCalled())
  })
})

describe('时点面板与曲线的联动', () => {
  it('点击曲线上的分钟会把面板切到该分钟', async () => {
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('④ 时点数据面板')).toBeInTheDocument())

    // 下拉框里包含曲线横轴的全部分钟
    const select = screen.getByLabelText('选择时点') as HTMLSelectElement
    const options = within(select)
      .getAllByRole('option')
      .map((o) => (o as HTMLOptionElement).value)
    expect(options).toContain('09:31')
    expect(options).toContain('13:01')
    expect(options).toContain('15:00')

    // 切到 13:01 —— 面板必须用同一交易日去查
    select.value = '13:01'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await waitFor(() => expect(mocked.timelinePoint).toHaveBeenCalledWith('13:01', '2026-09-30'))
  })
})
