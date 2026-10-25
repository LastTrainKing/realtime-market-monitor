/**
 * 首页核心模块 ②：主力资金累计曲线。
 *
 * 数据来源：`/api/snapshot` 的 `flow_series`（本地历史静态分钟库）。
 * 口径：东财分钟资金流为**累计**口径（`主力 = 大单 + 超大单`），
 * 因此这条曲线本身就是"当日累计净流入"曲线，前端不再做任何累加。
 *
 * 横轴严格来自历史数据的 `minute`；鼠标悬停/点击会把该分钟抛给父组件，
 * 由父组件去 `/api/timeline/point` 取**同一交易日同一分钟**的时点数据，
 * 从而保证曲线与时点面板永不脱节。
 */

import type * as echarts from 'echarts'
import { useCallback, useMemo } from 'react'

import type { MinuteBar, SeriesSummary } from '../../api/types'
import Chart, { chartBase } from '../Chart'
import StateBox from '../StateBox'
import { fmtMoney, fmtNum } from '../../utils/format'
import styles from './home.module.css'

const UP = '#ff4d5e'
const DOWN = '#23c98b'

interface Props {
  series: MinuteBar[]
  summary: SeriesSummary | null
  seriesDate: string
  activeMinute: string | null
  onPickMinute: (minute: string) => void
  loading: boolean
}

export function CapitalFlowCurve({
  series,
  summary,
  seriesDate,
  activeMinute,
  onPickMinute,
  loading,
}: Props) {
  const option = useMemo(
    () => buildOption(series, activeMinute),
    [series, activeMinute],
  )

  // 悬停/点击 → 上报分钟；分钟由父组件统一去查时点数据
  const onReady = useCallback(
    (chart: echarts.ECharts) => {
      chart.on('updateAxisPointer', (ev: unknown) => {
        const e = ev as { axesInfo?: { value?: unknown }[] }
        const v = e.axesInfo?.[0]?.value
        if (typeof v === 'string' && /^\d{2}:\d{2}$/.test(v)) onPickMinute(v)
      })
      chart.on('click', (ev: unknown) => {
        const e = ev as { name?: unknown }
        if (typeof e.name === 'string' && /^\d{2}:\d{2}$/.test(e.name)) onPickMinute(e.name)
      })
    },
    [onPickMinute],
  )

  if (series.length < 2) {
    return (
      <StateBox
        icon="⏱"
        title={`${seriesDate} 的两市资金曲线尚未落盘`}
        detail="东财分钟资金流仅提供最近一个交易日，更早日期需由本程序逐日累积；不会用实时值回填，也不会补 0。"
      />
    )
  }

  const last = summary?.last ?? 0
  const tone = last >= 0 ? 'up' : 'down'

  return (
    <div className={styles.modWrap}>
      <div className={styles.headStats}>
        <div className={styles.headStat} style={{ flexDirection: 'column', gap: 3 }}>
          <span className={styles.headStatLabel}>最新累计主力净流入</span>
          <span className={`${styles.headStatValue} ${tone}`} style={{ fontSize: 20 }}>
            {fmtMoney(last)}
          </span>
        </div>
        <div className={styles.headStat} style={{ flexDirection: 'column', gap: 3 }}>
          <span className={styles.headStatLabel}>日内峰值</span>
          <span className={styles.headStatValue} style={{ fontSize: 15 }}>
            {fmtMoney(summary?.high ?? 0)}
          </span>
        </div>
        <div className={styles.headStat} style={{ flexDirection: 'column', gap: 3 }}>
          <span className={styles.headStatLabel}>日内谷值</span>
          <span className={styles.headStatValue} style={{ fontSize: 15 }}>
            {fmtMoney(summary?.low ?? 0)}
          </span>
        </div>
        <div className={styles.headStat} style={{ flexDirection: 'column', gap: 3 }}>
          <span className={styles.headStatLabel}>分钟点数</span>
          <span className={styles.headStatValue} style={{ fontSize: 15 }}>
            {summary?.points ?? 0}
            <span style={{ fontSize: 11.5, color: 'var(--text-mute)', fontWeight: 400 }}>
              {' '}
              {summary?.first_minute ?? ''}→{summary?.last_minute ?? ''}
            </span>
          </span>
        </div>
        <div className={styles.headStat} style={{ flexDirection: 'column', gap: 3 }}>
          <span className={styles.headStatLabel}>口径</span>
          <span className={styles.headStatValue} style={{ fontSize: 13 }}>
            东财大盘资金流 · 接口即累计
          </span>
        </div>
      </div>
      <div className={styles.chartBox} style={{ flex: 1, minHeight: 0 }}>
        <Chart option={option!} height="100%" loading={loading} onReady={onReady} />
      </div>
    </div>
  )
}

function buildOption(series: MinuteBar[], activeMinute: string | null) {
  if (series.length < 2) return null
  const times = series.map((b) => b.minute)
  const values = series.map((b) => Number((b.main_net_inflow / 1e8).toFixed(4)))
  const last = values[values.length - 1] ?? 0
  const color = last >= 0 ? UP : DOWN

  return {
    ...chartBase,
    animation: false,
    grid: { left: 66, right: 26, top: 20, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(12,18,32,0.95)',
      borderColor: 'rgba(140,190,255,0.35)',
      textStyle: { color: '#eaf1ff', fontSize: 12 },
      formatter: (params: unknown) => {
        const arr = params as { axisValue: string; data: number }[]
        if (!arr?.length) return ''
        const p = arr[0]
        return `<b>${p.axisValue}</b><br/>累计主力净流入 ${p.data.toFixed(2)} 亿元`
      },
    },
    xAxis: {
      type: 'category',
      data: times,
      boundaryGap: false,
      axisLine: chartBase.axisLine,
      axisLabel: {
        color: chartBase.axisLabel.color,
        fontSize: 11,
        interval: Math.max(1, Math.floor(times.length / 6)),
      },
    },
    yAxis: {
      type: 'value',
      name: '亿元',
      scale: true,
      nameTextStyle: { color: chartBase.axisLabel.color, fontSize: 11 },
      axisLine: chartBase.axisLine,
      splitLine: chartBase.splitLine,
      axisLabel: {
        color: chartBase.axisLabel.color,
        fontSize: 11,
        formatter: (v: number) => fmtNum(v, 0),
      },
    },
    series: [
      {
        name: '累计主力净流入',
        type: 'line',
        smooth: false,
        symbol: 'none',
        data: values,
        lineStyle: { width: 2.2, color },
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
                    { offset: 0, color: 'rgba(255,77,94,0.32)' },
                    { offset: 1, color: 'rgba(255,77,94,0.02)' },
                  ]
                : [
                    { offset: 0, color: 'rgba(35,201,139,0.3)' },
                    { offset: 1, color: 'rgba(35,201,139,0.02)' },
                  ],
          },
        },
        // 零轴 + 当前选中的时点标记：曲线与时点面板共享同一分钟
        markLine: {
          silent: true,
          symbol: 'none',
          data:
            activeMinute && times.includes(activeMinute)
              ? [
                  { yAxis: 0 },
                  { xAxis: activeMinute, lineStyle: { color: '#4fa3ff', width: 1.4 } },
                ]
              : [{ yAxis: 0 }],
          lineStyle: { color: 'rgba(154,169,196,0.5)', type: 'dashed', width: 1 },
          label: {
            show: Boolean(activeMinute && times.includes(activeMinute)),
            formatter: activeMinute ?? '',
            color: '#bfe0ff',
            fontSize: 11,
            position: 'insideEndTop',
          },
        },
      },
    ],
  } as never
}

export default CapitalFlowCurve
