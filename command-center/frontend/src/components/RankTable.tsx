import { memo } from 'react'

import type { RankEntry } from '../api/types'
import { changeClass, fmtMoney, fmtNum, fmtPct, fmtRankDelta } from '../utils/format'
import styles from './RankTable.module.css'

export interface RankColumn {
  key: string
  label: string
  align?: 'left' | 'right'
  render: (e: RankEntry) => React.ReactNode
}

interface Props {
  entries: RankEntry[]
  onSelect?: (code: string) => void
  showRankDelta?: boolean
  showAppearCount?: boolean
  /** 用于迷你柱状图的比例基准 */
  maxValue?: number
}

function rankClass(rank: number): string {
  if (rank === 1) return 'rank-badge top1'
  if (rank === 2) return 'rank-badge top2'
  if (rank === 3) return 'rank-badge top3'
  return 'rank-badge'
}

/** 榜单表格（第 5.16 章：memo 化，避免整页重渲染） */
export const RankTable = memo(function RankTable({
  entries,
  onSelect,
  showRankDelta = true,
  showAppearCount = true,
}: Props) {
  if (entries.length === 0) {
    return (
      <div className="state-box">
        <span className="icon">📊</span>
        <span className="title">该榜单暂无数据</span>
        <span className="detail">数据将在采集完成后自动出现</span>
      </div>
    )
  }

  return (
    <div className={styles.wrap}>
      <table className="grid-table">
        <thead>
          <tr>
            <th style={{ width: 54 }}>排名</th>
            <th>名称</th>
            <th className="num">指标</th>
            <th className="num">涨跌幅</th>
            <th className="num">主力净流入</th>
            {showAppearCount ? <th className="num" style={{ width: 72 }}>上榜</th> : null}
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr
              key={`${e.code}-${e.rank}`}
              className={onSelect ? 'clickable' : undefined}
              onClick={onSelect ? () => onSelect(e.code) : undefined}
            >
              <td className="rank-cell">
                <span className={rankClass(e.rank)}>{e.rank}</span>
              </td>
              <td>
                <span className={styles.name}>{e.name}</span>
                {showRankDelta && e.rank_delta !== 0 ? (
                  <span
                    className={`${styles.delta} ${
                      e.rank_delta > 0 ? styles.deltaUp : styles.deltaDown
                    }`}
                  >
                    {fmtRankDelta(e.rank_delta)}
                  </span>
                ) : null}
                <br />
                <span className={styles.code}>{e.code}</span>
                {e.first_seen ? (
                  <span className={styles.tag} title="首次上榜时间">
                    {e.first_seen}
                  </span>
                ) : null}
              </td>
              <td className="num">
                <strong>{e.value_label}</strong>
              </td>
              <td className={`num ${changeClass(e.change_pct)}`}>{fmtPct(e.change_pct)}</td>
              <td className={`num ${changeClass(e.main_net_inflow)}`}>
                {fmtMoney(e.main_net_inflow)}
              </td>
              {showAppearCount ? (
                <td className="num">
                  {e.appear_count > 0 ? (
                    <span className={`${styles.count} ${e.appear_count >= 5 ? styles.hot : ''}`}>
                      {e.appear_count}
                    </span>
                  ) : (
                    <span style={{ color: 'var(--text-mute)' }}>—</span>
                  )}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
})

/** 通用数值表（板块等） */
export const ValueTable = memo(function ValueTable({
  rows,
  onSelect,
}: {
  rows: {
    key: string
    name: string
    code?: string
    value: string
    valueClass?: string
    pct?: number
    extra?: string
  }[]
  onSelect?: (code: string) => void
}) {
  if (rows.length === 0) {
    return (
      <div className="state-box">
        <span className="icon">📋</span>
        <span className="title">暂无数据</span>
      </div>
    )
  }
  return (
    <div className={styles.wrap}>
      <table className="grid-table">
        <thead>
          <tr>
            <th style={{ width: 54 }}>排名</th>
            <th>名称</th>
            <th className="num">数值</th>
            <th className="num">涨跌幅</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={r.key}
              className={onSelect && r.code ? 'clickable' : undefined}
              onClick={onSelect && r.code ? () => onSelect(r.code!) : undefined}
            >
              <td className="rank-cell">
                <span className={rankClass(i + 1)}>{i + 1}</span>
              </td>
              <td>
                <span className={styles.name}>{r.name}</span>
                {r.code ? (
                  <>
                    <br />
                    <span className={styles.code}>{r.code}</span>
                  </>
                ) : null}
              </td>
              <td className={`num ${r.valueClass ?? ''}`}>{r.value}</td>
              <td className={`num ${changeClass(r.pct)}`}>{fmtPct(r.pct)}</td>
              <td style={{ color: 'var(--text-mute)', fontSize: 12.5 }}>{r.extra ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
})

export { fmtNum }
export default RankTable
