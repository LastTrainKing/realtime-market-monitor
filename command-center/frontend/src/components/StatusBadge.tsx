import type { DataSource, DataStatus } from '../api/types'
import { statusTone } from '../utils/format'
import styles from './StatusBadge.module.css'

interface Props {
  status: DataStatus | string
  statusLabel?: string
  sourceLabel?: string
  updatedAt?: string
  message?: string
  compact?: boolean
}

const STATUS_TEXT: Record<string, string> = {
  REALTIME: '实时',
  CACHE: '缓存',
  REBUILD: '历史重建',
  DELAY: '延迟',
  ERROR: '数据源异常',
  UNAVAILABLE: '暂无数据',
  WAITING: '等待生成',
  PARTIAL: '部分缺失',
  DATA_SOURCE_LIMITATION: '数据源限制',
}

/**
 * 数据状态视觉反馈（第 5.18 章）。
 * 禁止简单显示“暂无数据”，必须说明原因与最后更新时间。
 */
export function StatusBadge({
  status,
  statusLabel,
  sourceLabel,
  updatedAt,
  message,
  compact = false,
}: Props) {
  const tone = statusTone(status)
  const text = statusLabel || STATUS_TEXT[status] || status
  const timePart = updatedAt && updatedAt !== '--:--:--' ? `最后更新 ${updatedAt}` : ''

  return (
    <span className={styles.wrap} title={message || undefined}>
      <span className={`chip ${tone}`}>
        <i className="dot" />
        {text}
      </span>
      {!compact && (sourceLabel || timePart) && (
        <span className={styles.meta}>
          {[sourceLabel, timePart].filter(Boolean).join(' · ')}
        </span>
      )}
      {message ? <span className={styles.message}>{message}</span> : null}
    </span>
  )
}

export function sourceText(source: DataSource): string {
  const map: Record<string, string> = {
    eastmoney_realtime: '东方财富实时',
    eastmoney_history: '东方财富历史',
    sina_realtime: '新浪财经',
    tencent_realtime: '腾讯财经',
    memory_cache: '本地缓存',
    database_replay: '本地数据库',
    none: '无数据源',
  }
  return map[source] ?? source
}

export default StatusBadge
