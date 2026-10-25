import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { ReviewDay, ReviewDayResponse, ReviewDaysResponse } from '../api/types'
import BigCard from '../components/BigCard'
import RankTable from '../components/RankTable'
import StateBox from '../components/StateBox'
import StatusBadge from '../components/StatusBadge'
import { usePolled } from '../hooks/usePolled'
import { useAppStore } from '../stores/useAppStore'
import { sourceText, statusText } from '../utils/format'
import layout from './pages.module.css'

/**
 * 复盘页（第 4.11 章）。
 * 与实时盯盘使用同一套计算引擎，只是数据输入不同（历史数据）。
 */
export function Review() {
  const setFocusStock = useAppStore((s) => s.setFocusStock)
  const showToast = useAppStore((s) => s.showToast)
  const focusReviewDate = useAppStore((s) => s.focusReviewDate)

  const days = usePolled<ReviewDaysResponse>(() => api.reviewDays(), 30000)
  const [selected, setSelected] = useState<string>('')
  const [day, setDay] = useState<ReviewDayResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeNode, setActiveNode] = useState('')

  useEffect(() => {
    if (focusReviewDate) setSelected(focusReviewDate)
  }, [focusReviewDate])

  useEffect(() => {
    if (!selected && days.data?.days?.length) {
      setSelected(days.data.days[0].trade_date)
    }
  }, [days.data, selected])

  useEffect(() => {
    if (!selected) return
    let alive = true
    setLoading(true)
    setError(null)
    api
      .reviewDay(selected)
      .then((res) => {
        if (!alive) return
        setDay(res)
        const first = res.snapshots?.[0]?.node ?? ''
        setActiveNode((prev) => prev || first)
      })
      .catch((err) => {
        if (!alive) return
        setDay(null)
        setError((err as Error).message)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [selected])

  const snap = day?.snapshots?.find((s) => s.node === activeNode) ?? day?.snapshots?.[0]

  const handleRebuild = async () => {
    if (!activeNode) return
    try {
      const res = await api.rebuildNode(activeNode)
      showToast(res.message, res.ok ? 'ok' : 'warn')
      if (selected) {
        const res2 = await api.reviewDay(selected)
        setDay(res2)
      }
    } catch (err) {
      showToast((err as Error).message, 'err')
    }
  }

  return (
    <div className={layout.page}>
      <div className={layout.pageHead}>
        <div>
          <h1 className={layout.pageTitle}>历史复盘</h1>
          <div className={layout.pageDesc}>
            回放任意交易日的关键节点，验证当时的市场状态与榜单结构
          </div>
        </div>
        <div className={layout.headRight}>
          {snap ? (
            <StatusBadge
              status={snap.status}
              statusLabel={statusText(snap.status)}
              sourceLabel={sourceText(snap.source)}
              updatedAt={snap.created_at?.slice(11)}
              message={snap.note}
              compact
            />
          ) : null}
          <button className="btn" onClick={handleRebuild} disabled={!activeNode}>
            重建当前节点
          </button>
        </div>
      </div>

      <div className={`${layout.row} ${layout.fill}`}>
        {/* 左：交易日列表 */}
        <BigCard
          title="交易日"
          subtitle={`${days.data?.days?.length ?? 0} 个交易日已存档`}
          icon={<CalendarIcon />}
          scroll
          style={{ width: 300, flexShrink: 0 }}
        >
          {(days.data?.days?.length ?? 0) > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {days.data!.days.map((d: ReviewDay) => (
                <div
                  key={d.trade_date}
                  className={`${layout.dayItem} ${selected === d.trade_date ? layout.active : ''}`}
                  onClick={() => {
                    setSelected(d.trade_date)
                    setActiveNode('')
                  }}
                >
                  <div>
                    <div className={layout.dayDate}>{d.trade_date}</div>
                    <div className={layout.dayMeta}>
                      节点 {d.snapshots} · 事件 {d.events} · 分钟 {d.minute_rows}
                    </div>
                  </div>
                  <span className={`chip ${d.complete ? 'ok' : 'warn'}`} style={{ fontSize: 11 }}>
                    <i className="dot" />
                    {d.complete ? '完整' : '不完整'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <StateBox
              icon="📅"
              title="暂无历史数据"
              detail="软件运行并采集数据后，每个交易日会自动存档，可在此回放"
            />
          )}
        </BigCard>

        {/* 右：当日详情 */}
        <div className={`${layout.grow} ${layout.page}`} style={{ flex: 1 }}>
          {error ? (
            <BigCard title="读取失败" icon={<AlertIcon />}>
              <StateBox icon="⚠️" title="该日期数据不可用" detail={error} />
            </BigCard>
          ) : loading && !day ? (
            <BigCard title="正在读取历史数据" icon={<CalendarIcon />}>
              <StateBox icon="⏳" title="正在读取…" />
            </BigCard>
          ) : day ? (
            <>
              <div className={layout.nodeTabs} style={{ flexShrink: 0 }}>
                {(day.snapshots ?? []).map((s) => (
                  <button
                    key={s.node}
                    className={`${layout.nodeTab} ${activeNode === s.node ? layout.active : ''}`}
                    onClick={() => setActiveNode(s.node)}
                  >
                    <span className={`${layout.nodeDot} ${toneOf(s.status)}`} />
                    {s.node}
                  </button>
                ))}
                {day.snapshots.length === 0 ? (
                  <span className="chip warn">
                    <i className="dot" />
                    该交易日无节点快照
                  </span>
                ) : null}
              </div>

              {snap ? (
                <div className={`${layout.grid} ${layout.cols2} ${layout.fill}`}>
                  <BigCard
                    title={`成交量 TOP · ${snap.node}`}
                    subtitle={`数据来源：${snap.origin === 'realtime' ? '实时捕获' : '历史重建'}`}
                    icon={<BarIcon />}
                    scroll
                  >
                    <RankTable entries={snap.volume_top} onSelect={setFocusStock} showAppearCount={false} />
                  </BigCard>
                  <BigCard
                    title={`主力净流入 TOP · ${snap.node}`}
                    subtitle="截至该节点"
                    icon={<FlowIcon />}
                    scroll
                  >
                    <RankTable entries={snap.netflow_top} onSelect={setFocusStock} showAppearCount={false} />
                  </BigCard>
                  <BigCard title={`量比 TOP · ${snap.node}`} icon={<GaugeIcon />} scroll>
                    <RankTable entries={snap.ratio_top} onSelect={setFocusStock} />
                  </BigCard>
                  <BigCard title={`异动成交量 TOP · ${snap.node}`} icon={<BarIcon />} scroll>
                    <RankTable entries={snap.anomaly_volume_top} onSelect={setFocusStock} />
                  </BigCard>
                </div>
              ) : (
                <BigCard title="节点数据" icon={<CalendarIcon />}>
                  <StateBox
                    icon="🕘"
                    title="该交易日暂无节点快照"
                    detail="可以在实时运行期间生成，或点击右上角“重建当前节点”从历史分钟数据重建"
                  />
                </BigCard>
              )}

              <BigCard
                title="当日九榜统计"
                subtitle="由持久化的榜单事件重建"
                icon={<StarIcon />}
                scroll
                style={{ maxHeight: 300, flexShrink: 0 }}
              >
                {(day.aggregates ?? []).length > 0 ? (
                  <table className="grid-table">
                    <thead>
                      <tr>
                        <th style={{ width: 50 }}>#</th>
                        <th>名称</th>
                        <th className="num">命中榜数</th>
                        <th>命中榜单</th>
                        <th className="num">最佳排名</th>
                        <th className="num">最近排名</th>
                        <th>出现时间</th>
                      </tr>
                    </thead>
                    <tbody>
                      {day.aggregates.map((a, i) => (
                        <tr
                          key={a.code}
                          className="clickable"
                          onClick={() => setFocusStock(a.code)}
                        >
                          <td className="rank-cell">
                            <span className={i < 3 ? `rank-badge top${i + 1}` : 'rank-badge'}>
                              {i + 1}
                            </span>
                          </td>
                          <td>
                            <strong>{a.name}</strong>
                            <br />
                            <span style={{ fontSize: 11.5, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>
                              {a.code}
                            </span>
                          </td>
                          <td className="num">{a.board_count}</td>
                          <td style={{ whiteSpace: 'normal', maxWidth: 260 }}>
                            {a.board_labels.join(' · ')}
                          </td>
                          <td className="num">{a.best_rank || '—'}</td>
                          <td className="num">{a.last_rank || '—'}</td>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
                            {a.history.join(' / ') || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <StateBox icon="⭐" title="当日暂无上榜事件" />
                )}
              </BigCard>
            </>
          ) : (
            <BigCard title="历史复盘" icon={<CalendarIcon />}>
              <StateBox
                icon="📅"
                title="请选择左侧交易日"
                detail="系统会保存每个交易日的节点快照、榜单事件与分钟数据"
              />
            </BigCard>
          )}
        </div>
      </div>
    </div>
  )
}

function toneOf(status: string): 'ok' | 'warn' | 'err' | 'idle' {
  switch (status) {
    case 'REALTIME':
      return 'ok'
    case 'CACHE':
    case 'REBUILD':
    case 'DELAY':
    case 'PARTIAL':
      return 'warn'
    case 'ERROR':
    case 'UNAVAILABLE':
    case 'DATA_SOURCE_LIMITATION':
      return 'err'
    default:
      return 'idle'
  }
}

const CalendarIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M3 10h18M8 3v4M16 3v4" />
  </svg>
)
const BarIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M5 20V9M12 20V4M19 20v-7" />
  </svg>
)
const FlowIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 3v18M7 8l5-5 5 5M7 16l5 5 5-5" />
  </svg>
)
const GaugeIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 14l4-4M4 18a8 8 0 1 1 16 0" />
  </svg>
)
const StarIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z" />
  </svg>
)
const AlertIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 9v4M12 17h.01M10.3 3.9L2 18a2 2 0 0 0 1.7 3h16.6A2 2 0 0 0 22 18L13.7 3.9a2 2 0 0 0-3.4 0z" />
  </svg>
)

export default Review
