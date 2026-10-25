/**
 * 后端 REST 客户端。
 *
 * 端口发现顺序：
 *   1. Tauri 注入的全局变量
 *   2. Tauri 命令 backend_port()
 *   3. 端口扫描（浏览器开发模式）
 */

import type {
  AnomalyResponse,
  AppConfig,
  BoardResult,
  DashboardResponse,
  Envelope,
  HealthResponse,
  HistoryStatusResponse,
  IndexTick,
  MarketBreadth,
  MarketFlowSeries,
  MessageResponse,
  MinutePoint,
  OpeningResponse,
  ReviewDayResponse,
  ReviewDaysResponse,
  SectorCompareResponse,
  SectorTick,
  SelectionResponse,
  SnapshotResponse,
  StockDetailResponse,
  StockTick,
  SystemStatus,
  TimelinePointResponse,
} from './types'

const DEFAULT_PORT = 47821
const PORT_SCAN_RANGE = 20

declare global {
  interface Window {
    __CAPITAL_FLOW_PORT__?: number
    __CF_TOKEN__?: string
    __TAURI_INTERNALS__?: unknown
  }
}

let resolvedBase: string | null = null
let resolvePromise: Promise<string> | null = null
let resolvedToken = ''
let tokenPromise: Promise<string> | null = null
let tokenResolvedAt = 0

/** 令牌未取到时的重试间隔：启动阶段外壳可能还没把令牌写出来 */
const TOKEN_RETRY_MS = 1500

export function setToken(token: string) {
  resolvedToken = token || ''
  tokenResolvedAt = Date.now()
  tokenPromise = null
}

/**
 * 获取本地 API 访问令牌。
 *
 * 后端启动时生成随机令牌，除 /api/health 外所有接口都必须携带 ——
 * 这样即使浏览器里的其它网页知道了端口，也无法操作本软件。
 *
 * 注意：取不到令牌时**不能把空值永久缓存**。外壳启动后端与前端首次请求
 * 存在竞态，早期实现把空串缓存下来，导致整个进程生命周期内所有请求都 401。
 */
async function resolveToken(force = false): Promise<string> {
  if (force) {
    resolvedToken = ''
    tokenPromise = null
  }
  if (resolvedToken) return resolvedToken
  // 已尝试过但为空：间隔一段时间后允许重试
  if (tokenPromise && !force) {
    if (Date.now() - tokenResolvedAt < TOKEN_RETRY_MS) return tokenPromise
    tokenPromise = null
  }

  tokenPromise = (async () => {
    // 1) 外壳注入的全局变量（启动时注入 / 后端就绪后补注入）
    const injected = window.__CF_TOKEN__
    if (injected) {
      resolvedToken = injected
      return resolvedToken
    }
    // 2) Tauri 命令
    if (window.__TAURI_INTERNALS__) {
      try {
        const mod = await import('@tauri-apps/api/core')
        const token = await mod.invoke<string>('backend_token')
        if (token) {
          resolvedToken = token
          return resolvedToken
        }
      } catch {
        /* 忽略，稍后重试 */
      }
    }
    tokenResolvedAt = Date.now()
    return resolvedToken
  })()

  return tokenPromise
}

export function setBaseUrl(base: string) {
  resolvedBase = base.replace(/\/$/, '')
  resolvePromise = Promise.resolve(resolvedBase)
}

export function getBaseUrlSync(): string | null {
  return resolvedBase
}

async function probe(port: number, timeoutMs = 700): Promise<boolean> {
  try {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), timeoutMs)
    const res = await fetch(`http://127.0.0.1:${port}/api/health`, {
      signal: ctrl.signal,
      cache: 'no-store',
    })
    clearTimeout(timer)
    if (!res.ok) return false
    const body = (await res.json()) as HealthResponse
    return body.status === 'ok'
  } catch {
    return false
  }
}

async function resolveBase(): Promise<string> {
  if (resolvedBase) return resolvedBase
  if (resolvePromise) return resolvePromise

  resolvePromise = (async () => {
    // 1) Tauri 注入
    const injected = window.__CAPITAL_FLOW_PORT__
    if (typeof injected === 'number' && injected > 0) {
      resolvedBase = `http://127.0.0.1:${injected}`
      return resolvedBase
    }

    // 2) Tauri 命令
    if (window.__TAURI_INTERNALS__) {
      try {
        const mod = await import('@tauri-apps/api/core')
        const port = await mod.invoke<number>('backend_port')
        if (typeof port === 'number' && port > 0) {
          resolvedBase = `http://127.0.0.1:${port}`
          return resolvedBase
        }
      } catch {
        /* 忽略，继续扫描 */
      }
    }

    // 3) 端口扫描
    for (let i = 0; i < PORT_SCAN_RANGE; i++) {
      const port = DEFAULT_PORT + i
      if (await probe(port)) {
        resolvedBase = `http://127.0.0.1:${port}`
        return resolvedBase
      }
    }
    throw new Error('BACKEND_UNREACHABLE')
  })()

  return resolvePromise
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly kind: 'network' | 'http' | 'parse' | 'backend',
    public readonly status?: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs = 20000,
  retriedAuth = false,
): Promise<T> {
  const base = await resolveBase()
  const token = await resolveToken()
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(`${base}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'X-CF-Token': token } : {}),
        ...(init?.headers ?? {}),
      },
    })
    if (res.status === 401 && !retriedAuth) {
      // 令牌缺失或过期：强制重新解析后重试一次
      clearTimeout(timer)
      await resolveToken(true)
      return request<T>(path, init, timeoutMs, true)
    }
    if (!res.ok) {
      let detail = ''
      try {
        const body = (await res.json()) as MessageResponse
        detail = body.message ?? ''
      } catch {
        /* ignore */
      }
      throw new ApiError(detail || `请求失败（HTTP ${res.status}）`, 'http', res.status)
    }
    const text = await res.text()
    if (!text.trim()) {
      throw new ApiError('本地数据服务返回为空，请稍后重试', 'parse')
    }
    try {
      return JSON.parse(text) as T
    } catch {
      throw new ApiError('数据格式异常，请稍后重试', 'parse')
    }
  } catch (err) {
    if (err instanceof ApiError) throw err
    if ((err as Error).name === 'AbortError') {
      throw new ApiError('请求超时', 'network')
    }
    throw new ApiError('无法连接到本地数据服务', 'network')
  } finally {
    clearTimeout(timer)
  }
}

export const api = {
  health: () => request<HealthResponse>('/api/health', undefined, 4000),
  dashboard: () => request<DashboardResponse>('/api/dashboard'),

  /**
   * 统一数据快照：首页四个核心模块的唯一数据源。
   * 指数分时 / 资金曲线 / 时点面板 / 板块对比都从这里取同一个交易日的同一份数据。
   */
  snapshot: () => request<SnapshotResponse>('/api/snapshot', undefined, 30000),
  /** 时点数据面板：必须传入与曲线同一交易日的 HH:MM */
  timelinePoint: (minute: string, tradeDate?: string) => {
    const q = new URLSearchParams({ minute })
    if (tradeDate) q.set('trade_date', tradeDate)
    return request<TimelinePointResponse>(`/api/timeline/point?${q.toString()}`)
  },
  /** 板块资金流入 / 流出对比 */
  sectorCompare: () => request<SectorCompareResponse>('/api/sector/compare'),
  /** 历史库状态诊断 */
  historyStatus: () => request<HistoryStatusResponse>('/api/history/status', undefined, 30000),
  /** 手动触发一次历史增量补齐 */
  historySync: () =>
    request<MessageResponse>('/api/history/sync', { method: 'POST' }, 30000),

  indices: () => request<Envelope<IndexTick[]>>('/api/market/indices'),
  breadth: () => request<Envelope<MarketBreadth>>('/api/market/breadth'),
  sectors: (kind?: string, limit?: number) => {
    const q = new URLSearchParams()
    if (kind) q.set('kind', kind)
    if (limit) q.set('limit', String(limit))
    return request<Envelope<SectorTick[]>>(`/api/market/sectors?${q.toString()}`)
  },
  boards: () => request<Envelope<BoardResult[]>>('/api/boards'),
  board: (id: string) => request<Envelope<BoardResult>>(`/api/boards/${id}`),
  stocks: (limit = 200) => request<Envelope<StockTick[]>>(`/api/stocks?limit=${limit}`),
  stock: (code: string) => request<StockDetailResponse>(`/api/stocks/${code}`),
  capitalCurve: () => request<Envelope<MarketFlowSeries>>('/api/capital/curve'),
  nodes: () => request<import('./types').NodeState[]>('/api/nodes'),
  nodeDetail: (node: string) =>
    request<import('./types').OpeningSnapshotRaw>(`/api/nodes/${node}`),
  rebuildNode: (node: string) =>
    request<MessageResponse>(`/api/nodes/${node}/rebuild`, { method: 'POST' }),
  opening: () => request<OpeningResponse>('/api/opening'),
  anomaly: () => request<AnomalyResponse>('/api/anomaly'),
  selection: () => request<SelectionResponse>('/api/selection'),
  reviewDays: () => request<ReviewDaysResponse>('/api/review/days'),
  reviewDay: (date: string) => request<ReviewDayResponse>(`/api/review/${date}`),
  systemStatus: () =>
    request<{ status: SystemStatus }>('/api/system/status').then((r) => r.status),
  config: () => request<AppConfig>('/api/config'),
  saveConfig: (cfg: AppConfig) =>
    request<MessageResponse>('/api/config', { method: 'PUT', body: JSON.stringify(cfg) }),
  shutdown: () => request<MessageResponse>('/api/shutdown', { method: 'POST' }),
  watchlist: () => request<[string, string][]>('/api/watchlist'),
  addWatch: (code: string, name?: string) =>
    request<MessageResponse>('/api/watchlist', {
      method: 'POST',
      body: JSON.stringify({ code, name }),
    }),
  removeWatch: (code: string) =>
    request<MessageResponse>(`/api/watchlist/${code}`, { method: 'DELETE' }),
  raw: <T,>(path: string) => request<T>(path),
  /** 前端异常上报（写入后端日志，便于离线排查） */
  reportClientError: (detail: string) =>
    request<MessageResponse>('/api/client-log', {
      method: 'POST',
      body: JSON.stringify({ detail }),
    }),
  minuteSeries: (code: string) =>
    request<StockDetailResponse>(`/api/stocks/${code}`).then((r) => r.minute),
  minute: (code: string) =>
    request<Envelope<MinutePoint[]>>(`/api/stocks/${code}`).then(
      (r) => (r as unknown as StockDetailResponse).minute,
    ),
}

export { resolveBase }
