import { useMemo, useState } from 'react'

import { api } from '../api/client'
import type { SectorTick } from '../api/types'
import BigCard from '../components/BigCard'
import Chart, { chartBase } from '../components/Chart'
import StateBox from '../components/StateBox'
import StatusBadge from '../components/StatusBadge'
import { usePolled } from '../hooks/usePolled'
import { changeClass, fmtMoney, fmtNum, fmtPct } from '../utils/format'
import layout from './pages.module.css'

type SortKey = 'main_net_inflow' | 'change_pct' | 'amount' | 'turnover_rate'

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'main_net_inflow', label: '主力净流入' },
  { key: 'change_pct', label: '涨跌幅' },
  { key: 'amount', label: '成交额' },
  { key: 'turnover_rate', label: '换手率' },
]

/** 板块页（第 4.3 / 8.3 章）：行业 / 概念板块资金流排名与图表 */
export function Sector() {
  const [kind, setKind] = useState<'all' | 'industry' | 'concept'>('all')
  const [sortKey, setSortKey] = useState<SortKey>('main_net_inflow')
  const poll = usePolled<{ data: SectorTick[]; status: string; status_label: string; source_label: string; updated_at: string; message: string }>(
    () =>
      api.sectors(kind === 'all' ? undefined : kind).then((r) => ({
        data: r.data,
        status: r.status,
        status_label: r.status_label,
        source_label: r.source_label,
        updated_at: r.updated_at,
        message: r.message,
      })),
    6000,
    { deps: [kind] },
  )

  const rows = useMemo(() => {
    const list = [...(poll.data?.data ?? [])]
    list.sort((a, b) => {
      const av = a[sortKey] as number
      const bv = b[sortKey] as number
      return bv - av
    })
    return list
  }, [poll.data, sortKey])

  const barOption = useMemo(() => buildBarOption(rows.slice(0, 15)), [rows])

  return (
    <div className={layout.page}>
      <div className={layout.pageHead}>
        <div>
          <h1 className={layout.pageTitle}>板块资金流</h1>
          <div className={layout.pageDesc}>
            {poll.data
              ? `共 ${rows.length} 个板块 · 按${SORTS.find((s) => s.key === sortKey)?.label}排序`
              : '正在加载板块数据…'}
          </div>
        </div>
        <div className={layout.headRight}>
          <div className={layout.nodeTabs}>
            {(
              [
                { k: 'all', label: '全部' },
                { k: 'industry', label: '行业' },
                { k: 'concept', label: '概念' },
              ] as const
            ).map((t) => (
              <button
                key={t.k}
                className={`${layout.nodeTab} ${kind === t.k ? layout.active : ''}`}
                onClick={() => setKind(t.k)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className={layout.nodeTabs}>
            {SORTS.map((s) => (
              <button
                key={s.key}
                className={`${layout.nodeTab} ${sortKey === s.key ? layout.active : ''}`}
                onClick={() => setSortKey(s.key)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className={`${layout.row} ${layout.fill}`}>
        <BigCard
          title="板块资金流排名"
          subtitle="实时净流入与涨跌表现"
          icon={<SectorIcon />}
          scroll
          style={{ flex: 1.35 }}
          actions={
            poll.data ? (
              <StatusBadge
                status={poll.data.status}
                statusLabel={poll.data.status_label}
                sourceLabel={poll.data.source_label}
                updatedAt={poll.data.updated_at}
                compact
              />
            ) : null
          }
        >
          {rows.length > 0 ? (
            <table className="grid-table">
              <thead>
                <tr>
                  <th style={{ width: 50 }}>#</th>
                  <th>板块</th>
                  <th className="num">涨跌幅</th>
                  <th className="num">主力净流入</th>
                  <th className="num">成交额</th>
                  <th className="num">换手率</th>
                  <th>领涨股</th>
                  <th className="num">涨跌家数</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s, i) => (
                  <tr key={s.code}>
                    <td className="rank-cell">
                      <span className={i < 3 ? `rank-badge top${i + 1}` : 'rank-badge'}>
                        {i + 1}
                      </span>
                    </td>
                    <td>
                      <strong>{s.name}</strong>
                      <br />
                      <span style={{ fontSize: 11.5, color: 'var(--text-mute)' }}>
                        {s.kind === 'concept' ? '概念' : s.kind === 'region' ? '地域' : '行业'}
                      </span>
                    </td>
                    <td className={`num ${changeClass(s.change_pct)}`}>{fmtPct(s.change_pct)}</td>
                    <td className={`num ${changeClass(s.main_net_inflow)}`}>
                      {fmtMoney(s.main_net_inflow)}
                    </td>
                    <td className="num">{fmtMoney(s.amount)}</td>
                    <td className="num">{fmtNum(s.turnover_rate)}%</td>
                    <td style={{ fontSize: 13 }}>
                      {s.leader_name || '—'}
                      {s.leader_name ? (
                        <span className={changeClass(s.leader_change_pct)} style={{ marginLeft: 7 }}>
                          {fmtPct(s.leader_change_pct)}
                        </span>
                      ) : null}
                    </td>
                    <td className="num">
                      <span className="up">{s.up_count}</span>
                      <span style={{ color: 'var(--text-mute)' }}> / </span>
                      <span className="down">{s.down_count}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <StateBox
              icon="🗂"
              title="板块数据正在加载"
              detail={poll.data?.message || '行业与概念板块资金流将在此实时更新'}
            />
          )}
        </BigCard>

        <div className={`${layout.grow} ${layout.page}`} style={{ flex: 1 }}>
          <BigCard
            title="净流入 TOP 15"
            subtitle="主力净流入（亿元）"
            icon={<BarIcon />}
            style={{ flex: 1.2, minHeight: 0 }}
          >
            {barOption ? (
              <Chart option={barOption} height="100%" />
            ) : (
              <StateBox icon="📊" title="等待板块数据" />
            )}
          </BigCard>

          <BigCard
            title="板块强弱分布"
            subtitle="上涨 / 下跌板块数量"
            icon={<PulseIcon />}
            style={{ flexShrink: 0 }}
          >
            {rows.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <Row
                  label="净流入板块"
                  value={`${rows.filter((r) => r.main_net_inflow > 0).length} 个`}
                  cls="up"
                />
                <Row
                  label="净流出板块"
                  value={`${rows.filter((r) => r.main_net_inflow < 0).length} 个`}
                  cls="down"
                />
                <Row label="上涨板块" value={`${rows.filter((r) => r.change_pct > 0).length} 个`} cls="up" />
                <Row label="下跌板块" value={`${rows.filter((r) => r.change_pct < 0).length} 个`} cls="down" />
                <Row
                  label="合计净流入"
                  value={fmtMoney(rows.reduce((a, b) => a + b.main_net_inflow, 0))}
                  cls={changeClass(rows.reduce((a, b) => a + b.main_net_inflow, 0))}
                />
              </div>
            ) : (
              <StateBox icon="📈" title="等待板块数据" />
            )}
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

function buildBarOption(rows: SectorTick[]) {
  if (rows.length === 0) return null
  const data = [...rows].reverse()
  return {
    ...chartBase,
    grid: { left: 108, right: 52, top: 20, bottom: 28 },
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
      data: data.map((r) => r.name),
      axisLine: chartBase.axisLine,
      axisLabel: { color: chartBase.axisLabel.color, fontSize: 12 },
    },
    series: [
      {
        type: 'bar',
        barWidth: 13,
        data: data.map((r) => ({
          value: Number((r.main_net_inflow / 1e8).toFixed(2)),
          itemStyle: {
            borderRadius: [0, 5, 5, 0],
            color:
              r.main_net_inflow >= 0
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
      },
    ],
  } as never
}

const SectorIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" />
  </svg>
)
const BarIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M5 20V9M12 20V4M19 20v-7" />
  </svg>
)
const PulseIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M3 12h4l2-6 3 12 2-8 2 4h5" />
  </svg>
)

export default Sector
