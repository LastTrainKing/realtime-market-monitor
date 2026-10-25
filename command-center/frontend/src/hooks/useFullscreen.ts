/**
 * 真正的系统级全屏（第 5.9 / 5.10 / 5.11 章）。
 *
 * 必须使用 Tauri 的 setFullscreen(true)（Windows 系统级全屏），
 * 而不是 maximize / 设置窗口尺寸 / CSS 撑满。
 * 浏览器调试环境回退到 Fullscreen API。
 */

import { useCallback, useEffect, useState } from 'react'

type TauriWindow = {
  setFullscreen: (v: boolean) => Promise<void>
  isFullscreen: () => Promise<boolean>
  setDecorations: (v: boolean) => Promise<void>
}

async function getWindow(): Promise<TauriWindow | null> {
  if (typeof window === 'undefined' || !window.__TAURI_INTERNALS__) return null
  try {
    const mod = await import('@tauri-apps/api/window')
    return mod.getCurrentWindow() as unknown as TauriWindow
  } catch {
    return null
  }
}

export function useFullscreen(autoEnter = true) {
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [supported, setSupported] = useState(true)

  const apply = useCallback(async (value: boolean) => {
    const win = await getWindow()
    if (win) {
      try {
        await win.setDecorations(false)
        await win.setFullscreen(value)
        setIsFullscreen(value)
        return true
      } catch {
        /* fallthrough */
      }
    }
    try {
      if (value) {
        await document.documentElement.requestFullscreen()
        setIsFullscreen(true)
      } else if (document.fullscreenElement) {
        await document.exitFullscreen()
        setIsFullscreen(false)
      }
      return true
    } catch {
      setSupported(false)
      return false
    }
  }, [])

  const toggle = useCallback(async () => {
    const next = !isFullscreen
    return apply(next)
  }, [apply, isFullscreen])

  const enter = useCallback(() => apply(true), [apply])
  const exit = useCallback(() => apply(false), [apply])

  useEffect(() => {
    void (async () => {
      const win = await getWindow()
      if (win) {
        try {
          const cur = await win.isFullscreen()
          setIsFullscreen(cur)
          if (autoEnter && !cur) await apply(true)
          return
        } catch {
          /* fallthrough */
        }
      }
      if (autoEnter) await apply(true)
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // F11 切换（第 5.11 章）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'F11') {
        e.preventDefault()
        void toggle()
      }
    }
    const onFsChange = () => setIsFullscreen(Boolean(document.fullscreenElement))
    window.addEventListener('keydown', onKey)
    document.addEventListener('fullscreenchange', onFsChange)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('fullscreenchange', onFsChange)
    }
  }, [toggle])

  return { isFullscreen, toggle, enter, exit, supported }
}
