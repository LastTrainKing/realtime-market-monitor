/**
 * 数据口径条 + 历史库状态条。
 *
 * 首页最重要的"诚实性"组件：把
 *   系统时间 / 行情时间 / 数据交易日 / 数据模式 / 数据源连接状态 / 抓取时间
 * 全部**分开**展示，避免用户把系统时间当成行情时间、
 * 把历史静态数据当成实时数据。
 */

import type { HistoryStatusResponse, SnapshotResponse } from '../../api/types'
import { fmtBytes } from '../../utils/format'
import styles from './home.module.css'

const MODE_TONE: Record<string, string> = {
  REALTIME: 'ok',
  DELAYED: 'warn',
  HISTORICAL_CLOSE: 'warn',
  STATIC: 'warn',
  UNAVAILABLE: 'err',
}

const LINK_TONE: Record<string, string> = {
  CONNECTED: 'ok',
  DEGRADED: 'warn',
  DISCONNECTED: 'err',
}

interface Props {
  snap: SnapshotResponse | null
}

export function ProvenanceBar({ snap }: Props) {
  if (!snap) {
    return (
      <div className={styles.provenance}>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>数据口径</span>
          <span className={styles.provValue}>正在读取本地历史分钟库…</span>
        </div>
      </div>
    )
  }

  const t = snap.time
  const dateShown = snap.series_date
  const dateMismatch = snap.series_fallback

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flexShrink: 0 }}>
      <div className={styles.provenance}>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>数据交易日</span>
          <span className={styles.provValue}>
            {dateShown}
            {dateMismatch ? (
              <span style={{ color: 'var(--warn)', fontSize: 12, marginLeft: 6 }}>（非当天）</span>
            ) : null}
          </span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>数据模式</span>
          <span className={`chip ${MODE_TONE[t.data_mode] ?? 'idle'}`} style={{ marginTop: 2 }}>
            <i className="dot" />
            {t.data_mode_label}
          </span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>市场状态</span>
          <span className={styles.provValue}>{t.market_status_label}</span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>行情时间</span>
          <span className={styles.provValue}>{t.data_timestamp || '—'}</span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>数据源</span>
          <span className={`chip ${LINK_TONE[t.source_link] ?? 'idle'}`} style={{ marginTop: 2 }}>
            <i className="dot" />
            {t.source_link_label}
          </span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>抓取时间</span>
          <span className={`${styles.provValue} ${styles.dim}`}>{t.data_fetch_time || '—'}</span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>系统时间</span>
          <span className={`${styles.provValue} ${styles.dim}`}>{t.system_time || '—'}</span>
        </div>
        <div className={styles.provItem}>
          <span className={styles.provLabel}>数据定稿</span>
          <span className={styles.provValue} style={{ fontSize: 13.5 }}>
            {t.is_final ? '已定稿（收盘后不再变化）' : '未定稿'}
          </span>
        </div>
      </div>

      {snap.missing_note ? (
        <div className={`${styles.notice} ${styles.warn}`}>
          <span className={styles.mark}>⚠️</span>
          <span>{snap.missing_note}</span>
        </div>
      ) : null}
      {snap.capability_note ? (
        <div className={`${styles.notice} ${styles.info}`}>
          <span className={styles.mark}>ℹ️</span>
          <span>{snap.capability_note}</span>
        </div>
      ) : null}
    </div>
  )
}

interface HistoryProps {
  status: HistoryStatusResponse | null
  onSync: () => void
  syncing: boolean
  syncMessage: string
}

export function HistoryStatusBar({ status, onSync, syncing, syncMessage }: HistoryProps) {
  if (!status) {
    return (
      <div className={styles.histBar}>
        <span className={styles.histItem}>历史库状态读取中…</span>
      </div>
    )
  }

  return (
    <div className={styles.histBar}>
      <div className={styles.histItem}>
        <span className={styles.histKey}>查询引擎</span>
        <span className={styles.histVal}>{status.duckdb_version}</span>
      </div>
      <div className={styles.histItem}>
        <span className={styles.histKey}>已落盘交易日</span>
        <span className={styles.histVal}>{status.dates.length} 个</span>
      </div>
      <div className={styles.histItem}>
        <span className={styles.histKey}>最新交易日</span>
        <span className={styles.histVal}>{status.coverage.latest_trading_day || '—'}</span>
      </div>
      <div className={styles.histItem}>
        <span className={styles.histKey}>磁盘占用</span>
        <span className={styles.histVal}>{fmtBytes(status.size_bytes)}</span>
      </div>
      <div className={styles.histItem}>
        <span className={styles.histKey}>当前粒度</span>
        <span className={styles.histVal}>{status.granularity_label}</span>
      </div>
      <div className={styles.histItem}>
        <span className={styles.histKey}>清单条目</span>
        <span className={styles.histVal}>{status.manifest.length}</span>
      </div>
      <div className={styles.histSpacer} />
      <div className={styles.histItem} style={{ gap: 8 }}>
        <span className={styles.histKey}>可回溯能力（实测）</span>
        <span className={styles.capList}>
          {status.granularity_capability.map((c) => (
            <span
              key={c.minutes}
              className={`${styles.capChip} ${c.meets_requirement ? styles.ok : styles.no}`}
              title={
                c.meets_requirement
                  ? `${c.label}：数据源单次可覆盖 ${c.source_max_days} 个交易日，满足配置要求`
                  : `${c.label}：数据源单次只能覆盖 ${c.source_max_days} 个交易日，不满足配置要求`
              }
            >
              {c.label} {c.source_max_days}日
              {c.meets_requirement ? ' ✓' : ' ✗'}
            </span>
          ))}
        </span>
      </div>
      <button
        className={`chip ${syncing ? 'warn' : 'idle'}`}
        style={{ cursor: syncing ? 'default' : 'pointer', border: 'none' }}
        onClick={onSync}
        disabled={syncing}
        title="按当前粒度补齐缺失交易日（在后台增量执行）"
      >
        <i className="dot" />
        {syncing ? '补齐中…' : '增量补齐历史'}
      </button>
      {syncMessage ? (
        <span className={styles.histItem} style={{ color: 'var(--text-dim)' }}>
          {syncMessage}
        </span>
      ) : null}
    </div>
  )
}
