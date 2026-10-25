import { useEffect, useState } from 'react'

import { api } from './api/client'
import Background from './components/Background'
import Modal from './components/Modal'
import Navigation from './components/Navigation'
import Splash from './components/Splash'
import Anomaly from './pages/Anomaly'
import CapitalFlow from './pages/CapitalFlow'
import Dashboard from './pages/Dashboard'
import Market from './pages/Market'
import Review from './pages/Review'
import Sector from './pages/Sector'
import Settings from './pages/Settings'
import Stock from './pages/Stock'
import { useAppStore } from './stores/useAppStore'
import { useBackendBoot } from './hooks/useBackendBoot'
import { useFullscreen } from './hooks/useFullscreen'
import styles from './App.module.css'

export function App() {
  const boot = useBackendBoot()
  const { isFullscreen, toggle } = useFullscreen(true)

  const page = useAppStore((s) => s.page)
  const connection = useAppStore((s) => s.connection)
  const setConnection = useAppStore((s) => s.setConnection)
  const setHealth = useAppStore((s) => s.setHealth)
  const toast = useAppStore((s) => s.toast)
  const exitConfirmOpen = useAppStore((s) => s.exitConfirmOpen)
  const setExitConfirmOpen = useAppStore((s) => s.setExitConfirmOpen)
  const showToast = useAppStore((s) => s.showToast)

  const [leaving, setLeaving] = useState(false)
  const [splashGone, setSplashGone] = useState(false)
  const [exiting, setExiting] = useState(false)

  // Splash 离场动画
  useEffect(() => {
    if (boot.ready) {
      setLeaving(true)
      const id = window.setTimeout(() => setSplashGone(true), 600)
      return () => window.clearTimeout(id)
    }
  }, [boot.ready])

  // 健康轮询：连接状态与降级提示
  useEffect(() => {
    if (!boot.ready) return
    let alive = true
    const tick = async () => {
      try {
        const h = await api.health()
        if (!alive) return
        setHealth(h)
        const degraded = useAppStore.getState().connection === 'degraded'
        if (!degraded) setConnection('online')
      } catch {
        if (!alive) return
        setConnection('offline')
      }
    }
    const id = window.setInterval(tick, 8000)
    void tick()
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [boot.ready, setConnection, setHealth])

  const handleExit = async () => {
    setExiting(true)
    try {
      await api.shutdown()
      showToast('正在安全退出…', 'ok')
    } catch {
      /* 后端可能已退出 */
    }
    setExitConfirmOpen(false)
    // 关闭窗口（Tauri 环境）或返回
    try {
      if (window.__TAURI_INTERNALS__) {
        const mod = await import('@tauri-apps/api/window')
        await mod.getCurrentWindow().close()
      }
    } catch {
      /* ignore */
    }
  }

  const renderPage = () => {
    switch (page) {
      case 'dashboard':
        return <Dashboard />
      case 'market':
        return <Market />
      case 'capital':
        return <CapitalFlow />
      case 'sector':
        return <Sector />
      case 'stock':
        return <Stock />
      case 'anomaly':
        return <Anomaly />
      case 'review':
        return <Review />
      case 'settings':
        return <Settings />
      default:
        return <Dashboard />
    }
  }

  return (
    <>
      <Background />

      {!splashGone && (
        <Splash
          progress={boot.progress}
          stage={boot.stage}
          error={boot.error}
          version={boot.health?.version}
          onRetry={boot.retry}
          leaving={leaving}
        />
      )}

      {boot.ready && (
        <div className={styles.shell}>
          <Navigation
            onToggleFullscreen={() => void toggle()}
            isFullscreen={isFullscreen}
            marketStatusLabel={boot.health?.market_status ? marketLabel(boot.health.market_status) : '—'}
            connection={connection}
            version={boot.health?.version}
          />

          {connection === 'offline' && (
            <div className={styles.offline}>
              数据连接暂时异常，系统正在自动恢复 · 已展示最近有效数据
            </div>
          )}

          <main className={styles.main}>
            <div className={styles.page} key={page}>
              {renderPage()}
            </div>
          </main>
        </div>
      )}

      <Modal
        open={exitConfirmOpen}
        title="退出资金流向指挥中心"
        onClose={() => setExitConfirmOpen(false)}
        footer={
          <>
            <button className="btn" onClick={() => setExitConfirmOpen(false)} disabled={exiting}>
              取消
            </button>
            <button className="btn danger" onClick={() => void handleExit()} disabled={exiting}>
              {exiting ? '正在退出…' : '确认退出'}
            </button>
          </>
        }
      >
        <div>
          退出后软件将保存当前数据并安全关闭后台服务。
          <div className="hint" style={{ fontSize: 12.5, color: 'var(--text-mute)', marginTop: 10 }}>
            历史数据与自选股会保留在本机，下次打开自动恢复。
          </div>
        </div>
      </Modal>

      {toast ? <div className={`${styles.toast} ${styles[toast.tone]}`}>{toast.text}</div> : null}
    </>
  )
}

function marketLabel(code: string): string {
  const map: Record<string, string> = {
    NonTradingDay: '休市',
    PreMarket: '等待开盘',
    CallAuction: '集合竞价',
    Open: '开盘',
    MorningSession: '上午交易',
    LunchBreak: '午间休市',
    AfternoonSession: '下午交易',
    Closed: '已收盘',
  }
  return map[code] ?? code
}

export default App
