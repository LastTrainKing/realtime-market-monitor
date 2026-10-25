import { useEffect, useMemo, useState } from 'react'

import { api } from '../api/client'
import type { SelectionResponse, StockDetailResponse } from '../api/types'
import BigCard from '../components/BigCard'
import Chart, { chartBase } from '../components/Chart'
import StateBox from '../components/StateBox'
import StatusBadge from '../components/StatusBadge'
import { usePolled } from '../hooks/usePolled'
import { useAppStore } from '../stores/useAppStore'
import { changeClass, fmtMoney, fmtNum, fmtPct, fmtVolume } from '../utils/format'
import layout from './pages.module.css'

/** 个股页（第 4.15 章）：个股详情 + 九榜统计（个股精选） + 自选股 */
export function Stock() {
  const focusStock = useAppStore((s) => s.focusStock)
  const setFocusStock = useAppStore((s) => s.setFocusStock)
  const showToast = useAppStore((s) => s.showToast)

  const [code, setCode] = useState(focusStock ?? '')
  const [input, setInput] = useState(focusStock ?? '')

  useEffect(() => {
    if (focusStock) {
      setCode(focusStock)
      setInput(focusStock)
    }
  }, [focusStock])

  const detail = usePolled<StockDetailResponse | null>(
    () => (code ? api.stock(code) : Promise.resolve(null)),
    5000,
    { deps: [code], enabled: Boolean(code) },
  )

  const selection = usePolled<SelectionResponse>(() => api.selection(), 8000)
  const watch = usePolled<[string, string][]>(() => api.watchlist(), 15000)

  const flowOption = useMemo(
    () => buildFlowOption(detail.data?.flow?.data ?? []),
    [detail.data],
  )
  const minuteOption = useMemo(
    () => buildMinuteOption(detail.data?.minute?.data ?? []),
    [detail.data],
  )

  const tick = detail.data?.tick?.data
  const aggregates = selection.data?.stocks ?? []
  const hit = detail.data?.aggregates?.[0]

  const submit = () => {
    const c = input.trim()
    if (!/^\d{6}$/.test(c)) {
      showToast('请输入 6 位股票代码', 'warn')
      return
    }
    setCode(c)
    setFocusStock(c)
  }

  const addWatch = async () => {
    if (!code) return
    try {
      const res = await api.addWatch(code, tick?.name)
      showToast(res.message, res.ok ? 'ok' : 'warn')
      watch.refresh()
    } catch (err) {
      showToast((err as Error).message, 'err')
    }
  }

  return (
    <div className={layout.page}>
      <div className={layout.pageHead}>
        <div>
          <h1 className={layout.pageTitle}>个股与九榜统计</h1>
          <div className={layout.pageDesc}>
            {selection.data
              ? `${selection.data.trade_date} · 累计上榜事件 ${selection.data.total_events} 次`
              : '正在加载九榜统计…'}
          </div>
        </div>
        <div className={layout.headRight}>
          <input
            className={layout.input}
            style={{ minWidth: 190 }}
            placeholder="输入 6 位代码，如 600519"
            value={input}
            onChange={(e) => setInput(e.target.value.replace(/\D/g, '').slice(0, 6))}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
            }}
          />
          <button className="btn primary" onClick={submit}>
            查询
          </button>
          <button className="btn" onClick={() => void addWatch()} disabled={!code}>
            加入自选
          </button>
        </div>
      </div>

      <div className={`${layout.row} ${layout.fill}`}>
        {/* 左：个股详情 */}
        <div className={`${layout.grow} ${layout.page}`} style={{ flex: 1.15 }}>
          <BigCard
            title={tick ? `${tick.name} ${tick.code}` : code ? `个股 ${code}` : '个股详情'}
            subtitle={tick ? (tick.market === 1 ? '上海证券交易所' : tick.market === 2 ? '北京证券交易所' : '深圳证券交易所') : undefined}
            icon={<StockIcon />}
            style={{ flexShrink: 0 }}
            actions={
              detail.data?.tick ? (
                <StatusBadge
                  status={detail.data.tick.status}
                  statusLabel={detail.data.tick.status_label}
                  sourceLabel={detail.data.tick.source_label}
                  updatedAt={detail.data.tick.updated_at}
                  message={detail.data.tick.message}
                />
              ) : null
            }
          >
            {tick ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 20, flexWrap: 'wrap' }}>
                  <span
                    className={`${layout.indexPrice} ${changeClass(tick.change_pct)}`}
                    style={{ fontSize: 42 }}
                  >
                    {fmtNum(tick.price)}
                  </span>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    <span
                      className={changeClass(tick.change_pct)}
                      style={{ fontFamily: 'var(--mono)', fontSize: 19, fontWeight: 600 }}
                    >
                      {tick.change_amount > 0 ? '+' : ''}
                      {fmtNum(tick.change_amount)} ({fmtPct(tick.change_pct)})
                    </span>
                    <span style={{ fontSize: 12, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>
                      昨收 {fmtNum(tick.prev_close)} · 今开 {fmtNum(tick.open)} · 最高 {fmtNum(tick.high)} · 最低 {fmtNum(tick.low)}
                    </span>
                  </div>
                </div>

                <div className={`${layout.grid} ${layout.cols4}`} style={{ gap: 10 }}>
                  <Cell label="成交量" value={fmtVolume(tick.volume)} />
                  <Cell label="成交额" value={fmtMoney(tick.amount)} />
                  <Cell label="量比" value={fmtNum(tick.volume_ratio)} />
                  <Cell label="换手率" value={`${fmtNum(tick.turnover_rate)}%`} />
                  <Cell
                    label="主力净流入"
                    value={fmtMoney(tick.main_net_inflow)}
                    cls={changeClass(tick.main_net_inflow)}
                  />
                  <Cell
                    label="超大单净额"
                    value={fmtMoney(tick.super_large_net)}
                    cls={changeClass(tick.super_large_net)}
                  />
                  <Cell label="大单净额" value={fmtMoney(tick.large_net)} cls={changeClass(tick.large_net)} />
                  <Cell label="振幅" value={`${fmtNum(tick.amplitude)}%`} />
                </div>

                {hit ? (
                  <div
                    style={{
                      padding: '13px 16px',
                      borderRadius: 12,
                      border: '1px solid rgba(124,92,255,0.32)',
                      background: 'rgba(124,92,255,0.1)',
                    }}
                  >
                    <div style={{ fontSize: 13.5, color: '#cbb8ff', marginBottom: 8, letterSpacing: '0.05em' }}>
                      九榜命中 · 共 {hit.appear_count} 次上榜事件 · 命中 {hit.board_count} 个榜单
                    </div>
                    <div className={layout.aggBoards}>
                      {hit.board_labels.map((b) => (
                        <span key={b} className={layout.boardTag}>
                          {b}
                        </span>
                      ))}
                    </div>
                    <div style={{ marginTop: 9, fontSize: 12, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>
                      首次上榜：{hit.history.join(' / ') || '—'} · 最佳排名 {hit.best_rank || '—'}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : (
              <StateBox
                icon="🔎"
                title={code ? '正在读取个股数据' : '请输入股票代码查询'}
                detail={
                  code
                    ? detail.data?.tick?.message || '若该股不在当前扫描范围内，将尝试直接获取实时快照'
                    : '也可以在榜单中点击任意个股直接跳转'
                }
              />
            )}
          </BigCard>

          <BigCard
            title="分时资金流"
            subtitle="主力 / 超大单 / 大单 / 中单 / 小单"
            icon={<FlowIcon />}
            style={{ flex: 1, minHeight: 0 }}
          >
            {flowOption ? (
              <Chart option={flowOption} height="100%" loading={detail.loading} />
            ) : (
              <StateBox
                icon="💰"
                title={code ? '分时资金流暂不可用' : '请选择个股'}
                detail={detail.data?.flow?.message || '该通道若未提供分钟资金流，将明确标注为数据源限制'}
              />
            )}
          </BigCard>

          <BigCard
            title="本机分钟数据"
            subtitle="由本软件采集并保存在本地数据库"
            icon={<DbIcon />}
            style={{ flex: 1, minHeight: 0 }}
          >
            {minuteOption ? (
              <Chart option={minuteOption} height="100%" />
            ) : (
              <StateBox
                icon="🗄"
                title={code ? '本机分钟数据尚未积累' : '请选择个股'}
                detail="软件运行期间每分钟记录一次，用于历史节点重建与复盘"
              />
            )}
          </BigCard>
        </div>

        {/* 右：九榜统计 + 自选 */}
        <div className={`${layout.grow} ${layout.page}`} style={{ flex: 1 }}>
          <BigCard
            title="个股精选 · 九榜统计"
            subtitle="多榜重复出现个股（事件驱动计数）"
            icon={<StarIcon />}
            scroll
            style={{ flex: 1.4, minHeight: 0 }}
          >
            {aggregates.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
                {aggregates.map((a) => (
                  <div
                    key={a.code}
                    className={layout.aggCard}
                    style={{ cursor: 'pointer' }}
                    onClick={() => {
                      setCode(a.code)
                      setFocusStock(a.code)
                      setInput(a.code)
                    }}
                  >
                    <div className={layout.aggTop}>
                      <div>
                        <div className={layout.aggName}>{a.name}</div>
                        <div className={layout.aggCode}>{a.code}</div>
                      </div>
                      <div style={{ textAlign: 'right' }}>
                        <div className={layout.aggScore}>{a.score.toFixed(0)}</div>
                        <div style={{ fontSize: 11, color: 'var(--text-mute)' }}>综合评分</div>
                      </div>
                    </div>
                    <div className={layout.aggBoards}>
                      {a.board_labels.map((b) => (
                        <span key={b} className={layout.boardTag}>
                          {b}
                        </span>
                      ))}
                    </div>
                    <div className={layout.aggMeta}>
                      <span>上榜 {a.appear_count} 次</span>
                      <span>命中 {a.board_count} 榜</span>
                      <span>最佳 {a.best_rank || '—'}</span>
                      <span className={changeClass(a.main_net_inflow)}>
                        {fmtMoney(a.main_net_inflow)}
                      </span>
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>
                      出现时间：{a.history.join(' / ') || '—'} · 最近 {a.last_seen || '—'}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <StateBox
                icon="⭐"
                title="九榜统计正在生成"
                detail="需要至少一轮完整采集；上榜次数按“事件”累计，持续停留不会重复计数"
              />
            )}
          </BigCard>

          <BigCard
            title="自选股"
            subtitle={`${watch.data?.length ?? 0} 只`}
            icon={<HeartIcon />}
            scroll
            style={{ flex: 1, minHeight: 0 }}
          >
            {(watch.data?.length ?? 0) > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {watch.data!.map(([c, n]) => (
                  <div
                    key={c}
                    className={layout.watchItem}
                    onClick={() => {
                      setCode(c)
                      setFocusStock(c)
                      setInput(c)
                    }}
                  >
                    <div>
                      <div className={layout.watchName}>{n || c}</div>
                      <div className={layout.watchCode}>{c}</div>
                    </div>
                    <button
                      className="btn"
                      style={{ padding: '6px 13px', fontSize: 12.5 }}
                      onClick={async (e) => {
                        e.stopPropagation()
                        try {
                          await api.removeWatch(c)
                          showToast('已移出自选', 'ok')
                          watch.refresh()
                        } catch (err) {
                          showToast((err as Error).message, 'err')
                        }
                      }}
                    >
                      移除
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <StateBox
                icon="❤️"
                title="暂无自选股"
                detail="查询个股后点击“加入自选”，即可在这里快速查看"
              />
            )}
          </BigCard>
        </div>
      </div>
    </div>
  )
}

function Cell({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className={layout.stat}>
      <span className={layout.statLabel}>{label}</span>
      <span className={layout.statValue} style={{ fontSize: 18 }}>
        <span className={cls ?? ''}>{value}</span>
      </span>
    </div>
  )
}

function buildFlowOption(points: { time: string; main_net: number; super_large: number; large: number; medium: number; small: number }[]) {
  if (points.length < 2) return null
  const series = [
    { name: '主力净流入', key: 'main_net', color: '#ff4d5e' },
    { name: '超大单', key: 'super_large', color: '#ffb020' },
    { name: '大单', key: 'large', color: '#4fa3ff' },
    { name: '中单', key: 'medium', color: '#7c5cff' },
    { name: '小单', key: 'small', color: '#23c98b' },
  ] as const
  return {
    ...chartBase,
    grid: { left: 64, right: 24, top: 40, bottom: 34 },
    legend: {
      top: 6,
      textStyle: { color: chartBase.axisLabel.color, fontSize: 11 },
      itemWidth: 14,
      itemHeight: 8,
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(12,18,32,0.95)',
      borderColor: 'rgba(140,190,255,0.35)',
      textStyle: { color: '#eaf1ff', fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      data: points.map((p) => p.time),
      boundaryGap: false,
      axisLine: chartBase.axisLine,
      axisLabel: { color: chartBase.axisLabel.color, fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      name: '万元',
      nameTextStyle: { color: chartBase.axisLabel.color, fontSize: 11 },
      axisLine: chartBase.axisLine,
      splitLine: chartBase.splitLine,
      axisLabel: { color: chartBase.axisLabel.color, fontSize: 11 },
    },
    series: series.map((s) => ({
      name: s.name,
      type: 'line',
      smooth: true,
      symbol: 'none',
      data: points.map((p) => Number(((p[s.key] as number) / 1e4).toFixed(1))),
      lineStyle: { width: s.key === 'main_net' ? 2.6 : 1.6, color: s.color },
      itemStyle: { color: s.color },
    })),
  } as never
}

function buildMinuteOption(points: { minute: string; price: number; volume: number }[]) {
  if (points.length < 2) return null
  return {
    ...chartBase,
    grid: { left: 64, right: 24, top: 30, bottom: 34 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(12,18,32,0.95)',
      borderColor: 'rgba(140,190,255,0.35)',
      textStyle: { color: '#eaf1ff', fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      data: points.map((p) => p.minute),
      boundaryGap: false,
      axisLine: chartBase.axisLine,
      axisLabel: { color: chartBase.axisLabel.color, fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      scale: true,
      name: '价格',
      nameTextStyle: { color: chartBase.axisLabel.color, fontSize: 11 },
      axisLine: chartBase.axisLine,
      splitLine: chartBase.splitLine,
      axisLabel: { color: chartBase.axisLabel.color, fontSize: 11 },
    },
    series: [
      {
        name: '价格',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: points.map((p) => p.price),
        lineStyle: { width: 2, color: '#4fa3ff' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(79,163,255,0.3)' },
              { offset: 1, color: 'rgba(79,163,255,0.02)' },
            ],
          },
        },
      },
    ],
  } as never
}

const StockIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M3 3v18h18M7 15l4-5 3 3 5-7" />
  </svg>
)
const FlowIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 3v18M7 8l5-5 5 5M7 16l5 5 5-5" />
  </svg>
)
const DbIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <ellipse cx="12" cy="6" rx="8" ry="3" />
    <path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
  </svg>
)
const StarIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z" />
  </svg>
)
const HeartIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 20s-7-4.4-7-9.2A3.9 3.9 0 0 1 12 8a3.9 3.9 0 0 1 7 2.8C19 15.6 12 20 12 20z" />
  </svg>
)

export default Stock
