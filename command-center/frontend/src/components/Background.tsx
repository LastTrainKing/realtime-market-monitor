import styles from './Background.module.css'

/**
 * 动态科技背景（第 5.6 章）。
 * 纯 CSS 实现（渐变 + 光晕 + 网格），不使用 Canvas 粒子，保证 GPU 占用可控。
 */
export function Background() {
  return (
    <div className={styles.bg} aria-hidden="true">
      <div className={`${styles.orb} ${styles.orbA}`} />
      <div className={`${styles.orb} ${styles.orbB}`} />
      <div className={`${styles.orb} ${styles.orbC}`} />
      <div className={styles.grid} />
      <div className={styles.scanline} />
      <div className={styles.vignette} />
    </div>
  )
}

export default Background
