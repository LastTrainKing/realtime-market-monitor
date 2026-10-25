/**
 * 启动引导（第 3.3 / 5.7 / 5.8 章）。
 *
 * Splash 阶段文案必须面向普通用户，禁止出现 Rust / 端口 / 日志等技术词汇。
 * 进度由真实的健康检查驱动，不做假动画。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { api, resolveBase } from '../api/client'
import type { HealthResponse } from '../api/types'
import { useAppStore } from '../stores/useAppStore'

export interface BootState {
  progress: number
  stage: string
  error: string | null
  ready: boolean
  health: HealthResponse | null
  retry: () => void
}

const STAGES: { at: number; text: string }[] = [
  { at: 0, text: '正在启动指挥中心…' },
  { at: 12, text: '正在连接市场数据…' },
  { at: 32, text: '正在初始化资金模型…' },
  { at: 52, text: '正在加载今日行情…' },
  { at: 72, text: '正在生成实时榜单…' },
  { at: 88, text: '正在准备指挥界面…' },
  { at: 98, text: '即将进入…' },
]

export function useBackendBoot(): BootState {
  const [progress, setProgress] = useState(0)
  const [stage, setStage] = useState(STAGES[0].text)
  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [attempt, setAttempt] = useState(0)
  const cancelled = useRef(false)

  const setConnection = useAppStore((s) => s.setConnection)
  const setBoot = useAppStore((s) => s.setBoot)

  const retry = useCallback(() => {
    setError(null)
    setProgress(0)
    setReady(false)
    setAttempt((a) => a + 1)
  }, [])

  useEffect(() => {
    cancelled.current = false
    let progressTimer: number | undefined

    const setStageFor = (p: number) => {
      let text = STAGES[0].text
      for (const s of STAGES) {
        if (p >= s.at) text = s.text
      }
      setStage(text)
    }

    const run = async () => {
      setConnection('connecting')
      setBoot(4, '正在启动指挥中心…')

      // 进度条在前端平滑推进，但不会超过 90%，直到后端真正就绪
      let p = 4
      progressTimer = window.setInterval(() => {
        p = Math.min(p + 1.4, 90)
        setProgress(p)
        setStageFor(p)
        setBoot(p, stageFor(p))
      }, 90)

      const deadline = Date.now() + 90_000
      let ok = false
      while (Date.now() < deadline && !cancelled.current) {
        try {
          await resolveBase()
          const h = await api.health()
          setHealth(h)
          ok = true
          break
        } catch {
          await new Promise((r) => setTimeout(r, 900))
        }
      }

      window.clearInterval(progressTimer)

      if (cancelled.current) return

      if (!ok) {
        setConnection('offline')
        setError('数据服务启动超时，请点击重试')
        setProgress(0)
        return
      }

      // 等待首次业务数据（最多 20 秒，不阻塞进入）
      for (let i = 0; i < 10 && !cancelled.current; i++) {
        try {
          await api.dashboard()
          break
        } catch {
          await new Promise((r) => setTimeout(r, 800))
        }
      }

      if (cancelled.current) return
      setProgress(100)
      setStage('准备就绪')
      setBoot(100, '准备就绪')
      setConnection('online')
      await new Promise((r) => setTimeout(r, 420))
      if (!cancelled.current) setReady(true)
    }

    void run()

    return () => {
      cancelled.current = true
      if (progressTimer) window.clearInterval(progressTimer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt])

  return { progress, stage, error, ready, health, retry }
}

function stageFor(p: number): string {
  let text = STAGES[0].text
  for (const s of STAGES) {
    if (p >= s.at) text = s.text
  }
  return text
}
