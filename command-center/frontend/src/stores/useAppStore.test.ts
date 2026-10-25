import { beforeEach, describe, expect, it } from 'vitest'

import { PAGES, useAppStore } from './useAppStore'

describe('全局状态', () => {
  beforeEach(() => {
    useAppStore.setState({
      page: 'dashboard',
      connection: 'booting',
      focusStock: null,
      exitConfirmOpen: false,
      toast: null,
      ready: false,
    })
  })

  it('提供八个导航页面', () => {
    expect(PAGES.map((p) => p.key)).toEqual([
      'dashboard',
      'market',
      'capital',
      'sector',
      'stock',
      'anomaly',
      'review',
      'settings',
    ])
    PAGES.forEach((p) => {
      expect(p.label.length).toBeGreaterThan(0)
      expect(p.hint.length).toBeGreaterThan(0)
    })
  })

  it('切换页面', () => {
    useAppStore.getState().setPage('anomaly')
    expect(useAppStore.getState().page).toBe('anomaly')
  })

  it('选择个股时自动跳转到个股页', () => {
    useAppStore.getState().setFocusStock('600519')
    expect(useAppStore.getState().focusStock).toBe('600519')
    expect(useAppStore.getState().page).toBe('stock')

    useAppStore.getState().setFocusStock(null)
    expect(useAppStore.getState().page).toBe('dashboard')
  })

  it('连接状态可切换', () => {
    useAppStore.getState().setConnection('online')
    expect(useAppStore.getState().connection).toBe('online')
    useAppStore.getState().setConnection('offline')
    expect(useAppStore.getState().connection).toBe('offline')
  })

  it('退出确认弹窗可开关', () => {
    useAppStore.getState().setExitConfirmOpen(true)
    expect(useAppStore.getState().exitConfirmOpen).toBe(true)
    useAppStore.getState().setExitConfirmOpen(false)
    expect(useAppStore.getState().exitConfirmOpen).toBe(false)
  })

  it('提示信息可展示与清除', () => {
    useAppStore.getState().showToast('已保存', 'ok')
    expect(useAppStore.getState().toast?.text).toBe('已保存')
    expect(useAppStore.getState().toast?.tone).toBe('ok')
    useAppStore.getState().clearToast()
    expect(useAppStore.getState().toast).toBeNull()
  })

  it('启动进度可更新', () => {
    useAppStore.getState().setBoot(52, '正在加载今日行情…')
    expect(useAppStore.getState().bootProgress).toBe(52)
    expect(useAppStore.getState().bootStage).toContain('行情')
  })

  it('复盘日期可传递', () => {
    useAppStore.getState().setFocusReviewDate('2026-09-30')
    expect(useAppStore.getState().focusReviewDate).toBe('2026-09-30')
  })
})
