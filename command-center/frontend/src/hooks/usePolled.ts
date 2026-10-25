/**
 * 通用轮询 Hook。
 *
 * 性能要求（第 5.16 章）：
 *   * 每个页面只订阅自己需要的数据
 *   * 页面不可见时自动暂停轮询
 *   * 请求失败时保留上一次有效数据并给出状态
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export interface PolledState<T> {
  data: T | null
  error: string | null
  loading: boolean
  /** 最后一次成功刷新的时间戳 */
  updatedAt: number
  /** 失败次数（连续） */
  failures: number
  refresh: () => void
}

export function usePolled<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  options: { enabled?: boolean; immediate?: boolean; deps?: unknown[] } = {},
): PolledState<T> {
  const { enabled = true, immediate = true } = options
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [updatedAt, setUpdatedAt] = useState(0)
  const [failures, setFailures] = useState(0)

  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const aliveRef = useRef(true)
  const inFlightRef = useRef(false)

  const tick = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    setLoading(true)
    try {
      const result = await fetcherRef.current()
      if (!aliveRef.current) return
      setData(result)
      setError(null)
      setUpdatedAt(Date.now())
      setFailures(0)
    } catch (err) {
      if (!aliveRef.current) return
      setError((err as Error).message || '数据获取失败')
      setFailures((f) => f + 1)
    } finally {
      inFlightRef.current = false
      if (aliveRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    aliveRef.current = true
    return () => {
      aliveRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    if (immediate) void tick()
    if (intervalMs <= 0) return
    const id = window.setInterval(() => {
      if (document.hidden) return
      void tick()
    }, intervalMs)
    const onVisible = () => {
      if (!document.hidden) void tick()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, immediate, tick, ...(options.deps ?? [])])

  return { data, error, loading, updatedAt, failures, refresh: () => void tick() }
}
