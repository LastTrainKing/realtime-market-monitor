/** 全局应用状态（Zustand） */

import { create } from 'zustand'
import type { HealthResponse } from '../api/types'

export type PageKey =
  | 'dashboard'
  | 'market'
  | 'capital'
  | 'sector'
  | 'stock'
  | 'anomaly'
  | 'review'
  | 'settings'

export const PAGES: { key: PageKey; label: string; hint: string }[] = [
  { key: 'dashboard', label: '首页', hint: '市场全景与核心指标' },
  { key: 'market', label: '市场', hint: '开盘检测与市场结构' },
  { key: 'capital', label: '资金', hint: '资金流向与曲线' },
  { key: 'sector', label: '板块', hint: '板块资金流排名' },
  { key: 'stock', label: '个股', hint: '个股详情与九榜命中' },
  { key: 'anomaly', label: '异动', hint: '量比 / 成交量 / 主力异动' },
  { key: 'review', label: '复盘', hint: '历史节点回放' },
  { key: 'settings', label: '设置', hint: '数据源与运行参数' },
]

export type ConnectionState = 'booting' | 'connecting' | 'online' | 'degraded' | 'offline'

interface AppStore {
  page: PageKey
  setPage: (p: PageKey) => void

  connection: ConnectionState
  setConnection: (c: ConnectionState) => void

  health: HealthResponse | null
  setHealth: (h: HealthResponse | null) => void

  /** 启动阶段进度 0-100 */
  bootProgress: number
  bootStage: string
  setBoot: (progress: number, stage: string) => void

  bootError: string | null
  setBootError: (e: string | null) => void

  ready: boolean
  setReady: (r: boolean) => void

  /** 待查看的个股代码（从榜单跳转） */
  focusStock: string | null
  setFocusStock: (code: string | null) => void

  /** 待查看的复盘日期 */
  focusReviewDate: string | null
  setFocusReviewDate: (d: string | null) => void

  exitConfirmOpen: boolean
  setExitConfirmOpen: (v: boolean) => void

  toast: { text: string; tone: 'ok' | 'warn' | 'err' } | null
  showToast: (text: string, tone?: 'ok' | 'warn' | 'err') => void
  clearToast: () => void
}

export const useAppStore = create<AppStore>((set) => ({
  page: 'dashboard',
  setPage: (p) => set({ page: p }),

  connection: 'booting',
  setConnection: (c) => set({ connection: c }),

  health: null,
  setHealth: (h) => set({ health: h }),

  bootProgress: 0,
  bootStage: '正在启动…',
  setBoot: (progress, stage) => set({ bootProgress: progress, bootStage: stage }),

  bootError: null,
  setBootError: (e) => set({ bootError: e }),

  ready: false,
  setReady: (r) => set({ ready: r }),

  focusStock: null,
  setFocusStock: (code) => set({ focusStock: code, page: code ? 'stock' : 'dashboard' }),

  focusReviewDate: null,
  setFocusReviewDate: (d) => set({ focusReviewDate: d }),

  exitConfirmOpen: false,
  setExitConfirmOpen: (v) => set({ exitConfirmOpen: v }),

  toast: null,
  showToast: (text, tone = 'ok') => {
    set({ toast: { text, tone } })
    setTimeout(() => {
      const cur = useAppStore.getState().toast
      if (cur && cur.text === text) set({ toast: null })
    }, 3200)
  },
  clearToast: () => set({ toast: null }),
}))
