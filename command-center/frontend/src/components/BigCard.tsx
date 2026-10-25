import type { ReactNode } from 'react'

import styles from './BigCard.module.css'

interface Props {
  title: string
  subtitle?: string
  icon?: ReactNode
  actions?: ReactNode
  children: ReactNode
  /** 内容区是否可滚动 */
  scroll?: boolean
  className?: string
  style?: React.CSSProperties
  glow?: boolean
}

/** 大卡片（第 5.5 章）：hover 放大 / 边框发光 / 数据变化动画 */
export function BigCard({
  title,
  subtitle,
  icon,
  actions,
  children,
  scroll = false,
  className,
  style,
  glow = false,
}: Props) {
  return (
    <section
      className={`${styles.card} ${glow ? styles.glow : ''} ${className ?? ''}`}
      style={style}
    >
      <header className={styles.head}>
        <div className={styles.titleWrap}>
          {icon ? <span className={styles.icon}>{icon}</span> : null}
          <div style={{ minWidth: 0 }}>
            <div className={styles.title}>{title}</div>
            {subtitle ? <div className={styles.sub}>{subtitle}</div> : null}
          </div>
        </div>
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </header>
      <div className={`${styles.body} ${scroll ? styles.scrollBody : ''}`}>{children}</div>
    </section>
  )
}

export default BigCard
