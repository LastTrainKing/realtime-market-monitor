import { useEffect, useState } from 'react'

import { PAGES, useAppStore, type PageKey } from '../stores/useAppStore'
import styles from './Navigation.module.css'

interface Props {
  onToggleFullscreen: () => void
  isFullscreen: boolean
  marketStatusLabel: string
  connection: 'booting' | 'connecting' | 'online' | 'degraded' | 'offline'
  /** 后端上报的版本号。
   *
   * 必须显示出来：本项目曾出现"用户装的是上一版安装包，却在问新问题"
   * 的情况 —— 界面上看不到版本号，就无从判断自己跑的是哪一版。 */
  version?: string
}

const CONN_TEXT: Record<Props['connection'], string> = {
  booting: '启动中',
  connecting: '连接中',
  online: '实时连接',
  degraded: '降级运行',
  offline: '连接中断',
}

/** 大型导航（第 5.4 章）：参考 Steam Big Picture，支持键盘方向控制 */
export function Navigation({
  onToggleFullscreen,
  isFullscreen,
  marketStatusLabel,
  connection,
  version,
}: Props) {
  const page = useAppStore((s) => s.page)
  const setPage = useAppStore((s) => s.setPage)
  const setExitConfirmOpen = useAppStore((s) => s.setExitConfirmOpen)

  const [now, setNow] = useState(() => new Date())
  const [focusIndex, setFocusIndex] = useState(() => PAGES.findIndex((p) => p.key === page))
  const [kbActive, setKbActive] = useState(false)

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(id)
  }, [])

  useEffect(() => {
    setFocusIndex(PAGES.findIndex((p) => p.key === page))
  }, [page])

  // 键盘方向控制：← → 移动焦点，Enter 进入，Esc 取消
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault()
        setKbActive(true)
        setFocusIndex((i) => {
          const next = e.key === 'ArrowRight' ? i + 1 : i - 1
          return (next + PAGES.length) % PAGES.length
        })
      } else if (e.key === 'Enter' && kbActive) {
        const p = PAGES[focusIndex]
        if (p) setPage(p.key)
      } else if (e.key === 'Escape') {
        setKbActive(false)
      } else if (/^[1-8]$/.test(e.key)) {
        const p = PAGES[Number(e.key) - 1]
        if (p) setPage(p.key as PageKey)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [focusIndex, kbActive, setPage])

  const connClass =
    connection === 'online'
      ? 'ok'
      : connection === 'degraded'
        ? 'warn'
        : connection === 'offline'
          ? 'err'
          : 'idle'

  return (
    <nav className={styles.nav}>
      <div className={styles.brand}>
        <div className={styles.mark}>
          <svg viewBox="0 0 64 64" fill="none" aria-hidden="true">
            <defs>
              <linearGradient id="navGrad" x1="0" y1="0" x2="64" y2="64">
                <stop offset="0%" stopColor="#7fd0ff" />
                <stop offset="100%" stopColor="#a98bff" />
              </linearGradient>
            </defs>
            <path
              d="M8 42 L20 30 L30 38 L44 18 L56 26"
              stroke="url(#navGrad)"
              strokeWidth="5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            <circle cx="44" cy="18" r="5" fill="#7fd0ff" />
          </svg>
        </div>
        <div className={styles.brandText}>
          <span className={styles.brandTitle}>资金流向指挥中心</span>
          <span className={styles.brandSub}>
            Command Center
            <span
              className={styles.brandVersion}
              title="后端实际运行的版本号；安装新版后此处会变化，可据此确认是否已升级"
            >
              v{version ?? '—'}
            </span>
          </span>
        </div>
      </div>

      <div className={styles.items}>
        {PAGES.map((p, i) => (
          <button
            key={p.key}
            className={`${styles.item} ${page === p.key ? styles.active : ''} ${
              kbActive && focusIndex === i ? styles.focused : ''
            }`}
            onClick={() => setPage(p.key)}
            onMouseEnter={() => {
              setKbActive(false)
              setFocusIndex(i)
            }}
          >
            {p.label}
            <span className={styles.itemHint}>{p.hint}</span>
          </button>
        ))}
      </div>

      <div className={styles.right}>
        <div className={styles.clock}>
          <span className={styles.time}>
            {now.toLocaleTimeString('zh-CN', { hour12: false })}
          </span>
          <span className={styles.date}>
            {now.toLocaleDateString('zh-CN')} · {marketStatusLabel}
          </span>
        </div>

        <span className={`chip ${connClass}`} title="数据通道状态">
          <i className="dot" />
          {CONN_TEXT[connection]}
        </span>

        <div className={styles.divider} />

        <div className={styles.windowBtns}>
          <button
            className={styles.wbtn}
            onClick={onToggleFullscreen}
            title={isFullscreen ? '退出全屏 (F11)' : '进入全屏 (F11)'}
          >
            {isFullscreen ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 3H5a2 2 0 0 0-2 2v4M15 3h4a2 2 0 0 1 2 2v4M9 21H5a2 2 0 0 1-2-2v-4M15 21h4a2 2 0 0 0 2-2v-4" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 9V5a2 2 0 0 1 2-2h4M21 9V5a2 2 0 0 0-2-2h-4M3 15v4a2 2 0 0 0 2 2h4M21 15v4a2 2 0 0 1-2 2h-4" />
              </svg>
            )}
          </button>
          <button
            className={styles.wbtn}
            onClick={() => setPage('settings')}
            title="设置"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6h.09A1.65 1.65 0 0 0 10 3.09V3a2 2 0 1 1 4 0v.09A1.65 1.65 0 0 0 15 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          <button
            className={`${styles.wbtn} ${styles.close}`}
            onClick={() => setExitConfirmOpen(true)}
            title="退出软件"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
      </div>
    </nav>
  )
}

export default Navigation
