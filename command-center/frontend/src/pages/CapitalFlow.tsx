import { useMemo } from 'react'

import { api } from '../api/client'
import type { BoardResult, DashboardResponse, FlowPoint } from '../api/types'
import BigCard from '../components/BigCard'
import Chart, { chartBase } from '../components/Chart'
import RankTable from '../components/RankTable'
import StateBox from '../components/StateBox'
import StatusBadge from '../components/StatusBadge'
import { usePolled } from '../hooks/usePolled'
import { useAppStore } from '../stores/useAppStore'
import { changeClass, fmtMoney, fmtNum } from '../utils/format'
import layout from './pages.module.css'

/** 资金页（第 4.2 / 8.7 章）：资金曲线 + 七类资金榜单 + 资金结构 */
export function CapitalFlow() {
  const setFocusStock = useAppStore((s) => s.setFocusStock)
  const dash = usePolled<DashboardResponse>(() => api.dashboard(), 4000)
  const boardsPoll = usePolled<{ data: BoardResult[] }>(
    () => api.boards().then((r) => ({ data: r.data })),
    6000,
  )

  const d = dash.data
  const marketFlow = d?.capital_curve?.data
  const curveOption = useMemo(
    () => buildCurveOption(marketFlow?.points ?? []),
    [marketFlow],
  )

  const stockBoards = (boardsPoll.data?.data ?? []).filter((b) => b.scope === 'stock')
  const netFlowBoard = stockBoards.find((b) => b.id === 'main_net_flow')
  const netRatioBoard = stockBoards.find((b) => b.id === 'main_net_ratio')

  // 资金结构：由主力净流入榜前 20 名汇总超大单 / 大单 / 中单 / 小单
  const structure = useMemo(() => {
    const ticks = d?.top_stocks?.data ?? []
    let superLarge = 0
    let large = 0
    let medium = 0
    let small = 0
    for (const t of ticks) {
      superLarge += t.super_large_net
      large += t.large_net
      medium += t.medium_net
      small += t.small_net
    }
    return { superLarge, large, medium, small, count: ticks.length }
  }, [d])

  const structureOption = useMemo(() => buildStructureOption(structure), [structure])

  return (
    <div className={layout.page}>
      <div className={layout.pageHead}>
        <div>
          <h1 className={layout.pageTitle}>资金流向</h1>
          <div className={layout.pageDesc}>
            {d ? `${d.trade_date} · ${d.market_status_label} · 主力资金实时追踪` : '正在加载资金数据…'}
          </div>
        </div>
        <div className={layout.headRight}>
          {marketFlow ? (
            <span className={`chip ${marketFlow.scope === 'full' ? 'ok' : 'warn'}`}>
              <i className="dot" />
              {marketFlow.scope === 'source'
                ? '数据源现成口径（东财大盘资金流）'
                : marketFlow.scope === 'full'
                  ? `全市场口径 ${marketFlow.sample_size} 只`
                  : `口径不完整（股票池 ${marketFlow.sample_size} 只，未达全市场规模）`}
            </span>
          ) : null}
          {d ? (
            <StatusBadge
              status={d.capital_curve.status}
              statusLabel={d.capital_curve.status_label}
              sourceLabel={d.capital_curve.source_label}
              updatedAt={d.capital_curve.updated_at}
              compact
            />
          ) : null}
        </div>
      </div>

      <div className={`${layout.row} ${layout.fill}`}>
        <div className={`${layout.grow} ${layout.page}`} style={{ flex: 1.4 }}>
          <BigCard
            title="大盘主力资金曲线"
            subtitle={marketFlow ? marketFlow.note : '东方财富大盘资金流（现成接口，未做累加）'}
            icon={<CurveIcon />}
            style={{ flex: 1.1, minHeight: 0 }}
          >
            {curveOption ? (
              <Chart option={curveOption} height="100%" loading={dash.loading && !d} />
            ) : (
              <StateBox
                icon="⏱"
                title="资金曲线正在积累"
                detail="开盘后每 15 秒采集一个点，曲线会随交易推进展开"
              />
            )}
          </BigCard>

          <BigCard
            title="主力资金结构"
            subtitle={`按主力净流入榜 TOP ${structure.count} 汇总`}
            icon={<PieIcon />}
            style={{ flex: 1, minHeight: 0 }}
          >
            {structure.count > 0 ? (
              <Chart option={structureOption} height="100%" />
            ) : (
              <StateBox icon="🥧" title="资金结构等待个股数据" />
            )}
          </BigCard>
        </div>

        <div className={`${layout.grow} ${layout.page}`} style={{ flex: 1 }}>
          <BigCard
            title="主力净流入 TOP"
            subtitle={netFlowBoard ? `榜单时间 ${netFlowBoard.as_of}` : undefined}
            icon={<FlowIcon />}
            scroll
            style={{ flex: 1, minHeight: 0 }}
          >
            <RankTable entries={netFlowBoard?.entries ?? []} onSelect={setFocusStock} />
          </BigCard>

          <BigCard
            title="主力净占比 TOP"
            subtitle="净流入 / 成交额"
            icon={<RatioIcon />}
            scroll
            style={{ flex: 1, minHeight: 0 }}
          >
            <RankTable entries={netRatioBoard?.entries ?? []} onSelect={setFocusStock} />
          </BigCard>

          <BigCard
            title="资金概览"
            subtitle="当前扫描范围合计"
            icon={<PulseIcon />}
            style={{ flexShrink: 0 }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
              <Row
                label="主力净流入合计"
                value={fmtMoney(d?.breadth?.data?.total_main_net ?? 0)}
                cls={changeClass(d?.breadth?.data?.total_main_net ?? 0)}
              />
              <Row label="成交额合计" value={fmtMoney(d?.breadth?.data?.total_amount ?? 0)} />
              <Row
                label="上涨 / 下跌"
                value={`${d?.breadth?.data?.up_count ?? 0} / ${d?.breadth?.data?.down_count ?? 0}`}
              />
              <Row
                label="市场情绪"
                value={fmtNum(d?.breadth?.data?.sentiment ?? 0, 1)}
                cls={changeClass(d?.breadth?.data?.sentiment ?? 0)}
              />
            </div>
          </BigCard>
        </div>
      </div>
    </div>
  )
}

function Row({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
      <span style={{ fontSize: 13, color: 'var(--text-mute)', letterSpacing: '0.04em' }}>{label}</span>
      <span style={{ fontFamily: 'var(--mono)', fontSize: 17, fontWeight: 600 }} className={cls ?? ''}>
        {value}
      </span>
    </div>
  )
}

function buildCurveOption(points: FlowPoint[]) {
  if (points.length < 2) return null
  const values = points.map((p) => Number((p.main_net / 1e8).toFixed(3)))
  const last = values[values.length - 1] ?? 0
  return {
    ...chartBase,
    grid: { left: 62, right: 26, top: 32, bottom: 36 },
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
      name: '亿元',
      nameTextStyle: { color: chartBase.axisLabel.color, fontSize: 11 },
      axisLine: chartBase.axisLine,
      splitLine: chartBase.splitLine,
      axisLabel: { color: chartBase.axisLabel.color, fontSize: 11 },
    },
    series: [
      {
        name: '主力净流入',
        type: 'line',
        smooth: true,
        symbol: 'none',
        data: values,
        lineStyle: { width: 2.6, color: last >= 0 ? '#ff4d5e' : '#23c98b' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops:
              last >= 0
                ? [
                    { offset: 0, color: 'rgba(255,77,94,0.34)' },
                    { offset: 1, color: 'rgba(255,77,94,0.02)' },
                  ]
                : [
                    { offset: 0, color: 'rgba(35,201,139,0.3)' },
                    { offset: 1, color: 'rgba(35,201,139,0.02)' },
                  ],
          },
        },
      },
    ],
  } as never
}

function buildStructureOption(s: {
  superLarge: number
  large: number
  medium: number
  small: number
  count: number
}) {
  const items = [
    { name: '超大单', value: Number((s.superLarge / 1e8).toFixed(2)) },
    { name: '大单', value: Number((s.large / 1e8).toFixed(2)) },
    { name: '中单', value: Number((s.medium / 1e8).toFixed(2)) },
    { name: '小单', value: Number((s.small / 1e8).toFixed(2)) },
  ]
  return {
    ...chartBase,
    grid: { left: 66, right: 30, top: 26, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: 'rgba(12,18,32,0.95)',
      borderColor: 'rgba(140,190,255,0.35)',
      textStyle: { color: '#eaf1ff', fontSize: 12 },
    },
    xAxis: {
      type: 'value',
      name: '亿元',
      nameTextStyle: { color: chartBase.axisLabel.color, fontSize: 11 },
      axisLine: chartBase.axisLine,
      splitLine: chartBase.splitLine,
      axisLabel: { color: chartBase.axisLabel.color, fontSize: 11 },
    },
    yAxis: {
      type: 'category',
      data: items.map((i) => i.name),
      axisLine: chartBase.axisLine,
      axisLabel: { color: chartBase.axisLabel.color, fontSize: 12 },
    },
    series: [
      {
        type: 'bar',
        barWidth: 18,
        data: items.map((i) => ({
          value: i.value,
          itemStyle: {
            borderRadius: [0, 6, 6, 0],
            color:
              i.value >= 0
                ? {
                    type: 'linear',
                    x: 0,
                    y: 0,
                    x2: 1,
                    y2: 0,
                    colorStops: [
                      { offset: 0, color: 'rgba(255,77,94,0.25)' },
                      { offset: 1, color: '#ff4d5e' },
                    ],
                  }
                : {
                    type: 'linear',
                    x: 0,
                    y: 0,
                    x2: 1,
                    y2: 0,
                    colorStops: [
                      { offset: 0, color: 'rgba(35,201,139,0.25)' },
                      { offset: 1, color: '#23c98b' },
                    ],
                  },
          },
        })),
        label: {
          show: true,
          position: 'right',
          color: '#cfdcf0',
          fontSize: 11,
          formatter: (p: { value: number }) => `${p.value}`,
        },
      },
    ],
  } as never
}

const CurveIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M3 17l5-6 4 3 5-8 4 4" />
  </svg>
)
const FlowIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 3v18M7 8l5-5 5 5M7 16l5 5 5-5" />
  </svg>
)
const RatioIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="7" cy="7" r="3" />
    <circle cx="17" cy="17" r="3" />
    <path d="M19 5L5 19" />
  </svg>
)
const PieIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 3v9h9a9 9 0 1 1-9-9z" />
  </svg>
)
const PulseIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M3 12h4l2-6 3 12 2-8 2 4h5" />
  </svg>
)

export default CapitalFlow
