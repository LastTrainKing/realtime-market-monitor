/**
 * 首页核心模块 ④：时点数据面板。
 *
 * 与资金累计曲线**共享同一交易日、同一分钟**：
 * 曲线悬停/点击发出的 `minute` 直接驱动本面板去
 * `/api/timeline/point?minute=HH:MM&trade_date=...` 取数，
 * 因此不存在"曲线画的是 A 日、面板显示的是 B 时点"的错位。
 *
 * 面板不自己取"当前时间"，分钟只能来自曲线交互或下方下拉框。
 */

import { useEffect, useState } from 'react'

import { api } from '../../api/client'
import type { TimelinePointResponse } from '../../api/types'
import StateBox from '../StateBox'
import { changeClass, fmtMoney, fmtNum } from '../../utils/format'
import styles from './home.module.css'

interface Props {
  minute: string | null
  tradeDate: string
  /** 可选分钟列表（来自快照的资金曲线横轴） */
  minuteOptions: string[]
  onPickMinute: (minute: string) => void
}

export function TimelinePanel({ minute, tradeDate, minuteOptions, onPickMinute }: Props) {
  const [data, setData] = useState<TimelinePointResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!minute) {
      setData(null)
      setError(null)
      return
    }
    let alive = true
    setLoading(true)
    api
      .timelinePoint(minute, tradeDate)
      .then((r) => {
        if (!alive) return
        setData(r)
        setError(null)
      })
      .catch((e: unknown) => {
        if (!alive) return
        setError((e as Error).message || '时点数据获取失败')
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [minute, tradeDate])

  return (
    <div className={styles.tlWrap}>
      <div className={styles.tlHead}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, minWidth: 0 }}>
          <span className={styles.tlMinute}>{minute ?? '--:--'}</span>
          <span className={styles.tlDate}>{tradeDate}</span>
        </div>
        <select
          className={styles.picker}
          value={minute ?? ''}
          onChange={(e) => e.target.value && onPickMinute(e.target.value)}
          aria-label="选择时点"
        >
          <option value="">选择时点…</option>
          {minuteOptions.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>

      {!minute ? (
        <StateBox
          icon="🎯"
          title="把鼠标移到资金曲线上"
          detail="曲线悬停或点击的分钟，就是本面板查询的时点；也可用右上角下拉框直接选一个时点。"
        />
      ) : loading && !data ? (
        <StateBox icon="⏳" title="正在读取该时点的历史分钟数据…" />
      ) : error ? (
        <StateBox icon="⚠️" title="时点数据获取失败" detail={error} />
      ) : data && !data.available ? (
        <StateBox icon="📭" title={`${tradeDate} ${minute} 无历史分钟数据`} detail={data.note} />
      ) : data ? (
        <>
          <div className={styles.tlGrid}>
            <div className={`${styles.tlCell} ${styles.wide}`}>
              <span className={styles.tlCellLabel}>上证指数点位</span>
              <span className={styles.tlCellValue}>
                {data.index_close == null ? '—' : fmtNum(data.index_close)}
              </span>
              <span className={styles.tlCellSub}>来自本地历史分钟库</span>
            </div>
            <div className={`${styles.tlCell} ${styles.wide}`}>
              <span className={styles.tlCellLabel}>主力净流入（当日累计）</span>
              <span className={`${styles.tlCellValue} ${changeClass(data.main_net)}`}>
                {data.main_net == null ? '—' : fmtMoney(data.main_net)}
              </span>
              <span className={styles.tlCellSub}>主力 = 超大单 + 大单</span>
            </div>
            <FundCell label="超大单（累计）" value={data.super_large} />
            <FundCell label="大单（累计）" value={data.large} />
            <FundCell label="中单（累计）" value={data.medium} />
            <FundCell label="小单（累计）" value={data.small} />
          </div>
          <div className={styles.tlNote}>{data.note}</div>
        </>
      ) : null}
    </div>
  )
}

function FundCell({ label, value }: { label: string; value: number | null }) {
  return (
    <div className={styles.tlCell}>
      <span className={styles.tlCellLabel}>{label}</span>
      <span className={`${styles.tlCellValue} ${changeClass(value)}`}>
        {value == null ? '—' : fmtMoney(value)}
      </span>
    </div>
  )
}

export default TimelinePanel
