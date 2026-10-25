/**
 * 首页核心模块 ①：指数分时。
 *
 * 数据来源：`/api/snapshot` 的 `index_series`（本地历史静态分钟库）。
 * 横轴严格使用历史数据自带的 `minute`（09:31..15:00），
 * **不使用程序当前时间**，因此休市/复盘场景下画出的仍是该交易日的完整分时。
 *
 * 汇总指标（最新/涨跌/最高/最低/振幅/成交额/点数）全部由后端算好，
 * 前端只做格式化展示。
 */

import { useMemo } from 'react'

import type { MinuteBar, SeriesSummary } from '../../api/types'
import Chart, { chartBase } from '../Chart'
import StateBox from '../StateBox'
import { fmtMoney, fmtNum, fmtPct } from '../../utils/format'
import styles from './home.module.css'

const UP = '#ff4d5e'
const DOWN = '#23c98b'

interface Props {
  series: MinuteBar[]
  summary: SeriesSummary | null
  prevClose: number | null
  seriesDate: string
  complete: boolean
  loading: boolean
}

export function IndexIntraday({ series, summary, prevClose, seriesDate, complete, loading }: Props) {
  const option = useMemo(() => buildOption(series, prevClose), [series, prevClose])
  const pct = summary?.change_pct ?? null
  const tone = pct == null ? '' : pct >= 0 ? 'up' : 'down'

  if (series.length < 2) {
    return (
      <StateBox
        icon="📉"
        title={`${seriesDate} 的指数分时尚未落盘`}
        detail="本程序只在数据源提供分钟数据时落盘，不会用实时值回填历史，也不会补 0。"
      />
    )
  }

  return (
    <div className={styles.modWrap}>
      <div className={styles.headStats}>
        <Stat label="最新" value={fmtNum(summary?.last)} tone={tone} />
        <Stat
          label="涨跌"
          value={
            summary?.change == null
              ? '—'
              : `${summary.change > 0 ? '+' : ''}${fmtNum(summary.change)}`
          }
          tone={tone}
        />
        <Stat label="涨跌幅" value={fmtPct(pct)} tone={tone} />
        <Stat label="最高" value={fmtNum(summary?.high)} />
        <Stat label="最低" value={fmtNum(summary?.low)} />
        <Stat
          label="振幅"
          value={summary?.amplitude == null ? '—' : `${fmtNum(summary.amplitude)}%`}
        />
        <Stat label="成交额" value={summary && summary.amount > 0 ? fmtMoney(summary.amount) : '—'} />
        <Stat
          label="分钟点数"
          value={`${summary?.points ?? 0}`}
          sub={`${summary?.first_minute ?? ''}→${summary?.last_minute ?? ''}${
            complete ? '' : ' · 不完整'
          }`}
        />
        <Stat label="数据交易日" value={seriesDate} plain />
      </div>
      <div className={styles.chartBox} style={{ flex: 1, minHeight: 0 }}>
        <Chart option={option!} height="100%" loading={loading} />
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  tone = '',
  sub,
  plain,
}: {
  label: string
  value: string
  tone?: string
  sub?: string
  plain?: boolean
}) {
  return (
    <div className={styles.headStat} style={plain ? undefined : { flexDirection: 'column', gap: 3 }}>
      <span className={styles.headStatLabel}>{label}</span>
      <span
        className={`${styles.headStatValue} ${tone}`}
        style={plain ? { fontSize: 13.5 } : undefined}
      >
        {value}
        {sub ? (
          <span style={{ fontSize: 11.5, color: 'var(--text-mute)', fontWeight: 400 }}> {sub}</span>
        ) : null}
      </span>
    </div>
  )
}

/**
 * 分时图 option。
 * 有昨收时以昨收为基准线、右轴显示相对昨收的涨跌幅（标准分时图形态）；
 * 昨收不可得时退化为绝对点位，**不伪造基准**。
 */
function buildOption(series: MinuteBar[], prevClose: number | null) {
  if (series.length < 2) return null
  const times = series.map((b) => b.minute)
  const closes = series.map((b) => Number(b.close.toFixed(2)))
  const baseline = prevClose != null && prevClose > 0 ? prevClose : null
  const last = closes[closes.length - 1] ?? 0
  const up = baseline != null ? last >= baseline : last >= (closes[0] ?? 0)
  const color = up ? UP : DOWN
  // 有基准时画"相对昨收的点位差"，基准线即 0 轴 —— 与券商分时图一致
  const values =
    baseline != null ? closes.map((c) => Number((c - baseline).toFixed(3))) : closes

  return {
    ...chartBase,
    animation: false,
    grid: { left: 62, right: baseline != null ? 64 : 26, top: 20, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(12,18,32,0.95)',
      borderColor: 'rgba(140,190,255,0.35)',
      textStyle: { color: '#eaf1ff', fontSize: 12 },
      formatter: (params: unknown) => {
        const arr = params as { axisValue: string; data: number }[]
        if (!arr?.length) return ''
        const p = arr[0]
        const abs = baseline != null ? baseline + p.data : p.data
        const diff = baseline != null ? p.data : p.data - (closes[0] ?? 0)
        const pctv = baseline != null ? (diff / baseline) * 100 : null
        return [
          `<b>${p.axisValue}</b>`,
          `点位 ${abs.toFixed(2)}`,
          `涨跌 ${diff > 0 ? '+' : ''}${diff.toFixed(2)}${
            pctv == null ? '' : ` (${pctv > 0 ? '+' : ''}${pctv.toFixed(2)}%)`
          }`,
        ].join('<br/>')
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
      name: baseline != null ? '较昨收(点)' : '点位',
      scale: true,
      nameTextStyle: { color: chartBase.axisLabel.color, fontSize: 11 },
      axisLine: chartBase.axisLine,
      splitLine: chartBase.splitLine,
      axisLabel: { color: chartBase.axisLabel.color, fontSize: 11 },
    },
    series: [
      {
        name: '指数',
        type: 'line',
        smooth: false,
        symbol: 'none',
        data: values,
        lineStyle: { width: 1.9, color },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: up
              ? [
                  { offset: 0, color: 'rgba(255,77,94,0.3)' },
                  { offset: 1, color: 'rgba(255,77,94,0.02)' },
                ]
              : [
                  { offset: 0, color: 'rgba(35,201,139,0.28)' },
                  { offset: 1, color: 'rgba(35,201,139,0.02)' },
                ],
          },
        },
        markLine:
          baseline != null
            ? {
                silent: true,
                symbol: 'none',
                data: [{ yAxis: 0 }],
                lineStyle: { color: 'rgba(154,169,196,0.5)', type: 'dashed', width: 1 },
                label: { show: false },
              }
            : undefined,
      },
    ],
  } as never
}

export default IndexIntraday
