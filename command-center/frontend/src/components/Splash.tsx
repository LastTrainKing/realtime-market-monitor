import { useEffect, useState } from 'react'

import styles from './Splash.module.css'

interface Props {
  progress: number
  stage: string
  error: string | null
  version?: string
  onRetry: () => void
  leaving: boolean
}

/** 开屏动画（第 5.7 / 5.8 章）：Logo 淡入 + 缩放，背景缓慢移动，加载文案面向普通用户 */
export function Splash({ progress, stage, error, version, onRetry, leaving }: Props) {
  const [dot, setDot] = useState(0)

  useEffect(() => {
    const id = window.setInterval(() => setDot((d) => (d + 1) % 4), 420)
    return () => window.clearInterval(id)
  }, [])

  const dots = '.'.repeat(dot)

  return (
    <div className={`${styles.wrap} ${leaving ? styles.leaving : ''}`}>
      <div className={`${styles.orb} ${styles.orbA}`} />
      <div className={`${styles.orb} ${styles.orbB}`} />
      <div className={styles.ring} />
      <div className={`${styles.ring} ${styles.ring2}`} />

      <div className={styles.center}>
        <div className={styles.logo}>
          <div className={styles.mark}>
            <svg viewBox="0 0 64 64" fill="none" aria-hidden="true">
              <defs>
                <linearGradient id="splashGrad" x1="0" y1="0" x2="64" y2="64">
                  <stop offset="0%" stopColor="#7fd0ff" />
                  <stop offset="55%" stopColor="#4fa3ff" />
                  <stop offset="100%" stopColor="#a98bff" />
                </linearGradient>
              </defs>
              <path
                d="M8 42 L20 30 L30 38 L44 18 L56 26"
                stroke="url(#splashGrad)"
                strokeWidth="4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M8 50 L20 44 L30 49 L44 34 L56 40"
                stroke="url(#splashGrad)"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
                opacity="0.5"
              />
              <circle cx="44" cy="18" r="4.5" fill="#7fd0ff" />
            </svg>
          </div>
          <h1 className={styles.title}>资金流向指挥中心</h1>
          <p className={styles.subtitle}>Capital Flow Command Center</p>
        </div>

        {error ? (
          <div className={styles.progressWrap}>
            <div className={styles.error}>
              {error}
              <br />
              请检查网络连接后重试。
            </div>
            <button className={styles.retry} onClick={onRetry}>
              重新连接
            </button>
          </div>
        ) : (
          <div className={styles.progressWrap}>
            <div className={styles.track}>
              <div className={styles.fill} style={{ width: `${Math.max(3, progress)}%` }} />
            </div>
            <div className={styles.meta}>
              <span>
                {stage}
                {dots}
              </span>
              <span className={styles.pct}>{Math.round(progress)}%</span>
            </div>
          </div>
        )}

        <p className={styles.hint}>
          首次启动需要初始化本地数据环境，之后每天打开即可直接使用
        </p>
      </div>

      <div className={styles.version}>
        版本 {version ?? '—'} · 本机离线运行 · 数据仅保存在您的电脑上
      </div>
    </div>
  )
}

export default Splash
