import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { DashboardResponse, OpeningResponse } from '../api/types'
import BigCard from '../components/BigCard'
import RankTable from '../components/RankTable'
import StateBox from '../components/StateBox'
import StatusBadge from '../components/StatusBadge'
import { usePolled } from '../hooks/usePolled'
import { useAppStore } from '../stores/useAppStore'
import { changeClass, fmtMoney, fmtNum, fmtPct } from '../utils/format'
import layout from './pages.module.css'

/**
 * 开盘检测页（第 4.13 / 8.10 章）
 * 3 个节点 × 2 个榜单：成交量 Top5、主力净流入 Top5
 * 程序晚启动时由历史分钟数据自动重建，并明确标注状态。
 */
export function Market() {
  const setFocusStock = useAppStore((s) => s.setFocusStock)
  const showToast = useAppStore((s) => s.showToast)

  const opening = usePolled<OpeningResponse>(() => api.opening(), 8000)
  const dash = usePolled<DashboardResponse>(() => api.dashboard(), 6000)

  const [activeNode, setActiveNode] = useState<string>('')
  const [rebuilding, setRebuilding] = useState(false)

  const nodes = opening.data?.nodes ?? []

  useEffect(() => {
    if (!activeNode && nodes.length > 0) {
      const preferred = nodes.find((n) => n.volume_top.length > 0) ?? nodes[0]
      setActiveNode(preferred.node)
    }
  }, [nodes, activeNode])

  const current = nodes.find((n) => n.node === activeNode)
  const breadth = dash.data?.breadth?.data

  const handleRebuild = async () => {
    if (!activeNode) return
    setRebuilding(true)
    try {
      const res = await api.rebuildNode(activeNode)
      showToast(res.message, res.ok ? 'ok' : 'warn')
      opening.refresh()
    } catch (err) {
      showToast((err as Error).message, 'err')
    } finally {
      setRebuilding(false)
    }
  }

  return (
    <div className={layout.page}>
      <div className={layout.pageHead}>
        <div>
          <h1 className={layout.pageTitle}>开盘检测</h1>
          <div className={layout.pageDesc}>
            {opening.data
              ? `${opening.data.trade_date} · 每个节点生成成交量与主力净流入两个榜单`
              : '正在读取节点数据…'}
          </div>
        </div>
        <div className={layout.headRight}>
          <div className={layout.nodeTabs}>
            {nodes.map((n) => (
              <button
                key={n.node}
                className={`${layout.nodeTab} ${activeNode === n.node ? layout.active : ''}`}
                onClick={() => setActiveNode(n.node)}
              >
                <span className={`${layout.nodeDot} ${toneOf(n.status)}`} />
                {n.node}
              </button>
            ))}
          </div>
          <button className="btn" onClick={handleRebuild} disabled={rebuilding || !activeNode}>
            {rebuilding ? '正在重建…' : '重建该节点'}
          </button>
        </div>
      </div>

      {current ? (
        <div
          className="panel"
          style={{
            padding: '14px 20px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 20,
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 20, fontWeight: 600 }}>
              {current.node}
            </span>
            <StatusBadge
              status={current.status}
              statusLabel={current.status_label}
              sourceLabel={current.source_label}
              updatedAt={current.created_at?.slice(11)}
              message={current.note}
            />
          </div>
          <div style={{ display: 'flex', gap: 22, fontFamily: 'var(--mono)', fontSize: 13 }}>
            <span style={{ color: 'var(--text-mute)' }}>
              数据来源：{current.origin === 'realtime' ? '实时捕获' : current.origin === 'replay' ? '历史重建' : '等待中'}
            </span>
          </div>
        </div>
      ) : null}

      {/* 两榜并排 */}
      <div className={`${layout.grid} ${layout.cols2} ${layout.fill}`}>
        <BigCard
          title={`成交量 TOP 5${current ? ` · ${current.node}` : ''}`}
          subtitle="开盘以来累计成交量"
          icon={<BarIcon />}
          scroll
        >
          {current && current.volume_top.length > 0 ? (
            <RankTable entries={current.volume_top} onSelect={setFocusStock} showAppearCount={false} />
          ) : (
            <StateBox
              icon={current?.status === 'WAITING' ? '🕘' : '📊'}
              title={
                current?.status === 'WAITING'
                  ? `${activeNode} 节点尚未到达`
                  : current
                    ? '该节点数据不可用'
                    : '正在读取节点数据'
              }
              detail={
                current?.note ||
                '节点到达时会自动生成；程序晚启动时会尝试用历史分钟数据重建'
              }
            />
          )}
        </BigCard>

        <BigCard
          title={`主力净流入 TOP 5${current ? ` · ${current.node}` : ''}`}
          subtitle="截至该节点的主力资金净流入"
          icon={<FlowIcon />}
          scroll
        >
          {current && current.netflow_top.length > 0 ? (
            <RankTable entries={current.netflow_top} onSelect={setFocusStock} showAppearCount={false} />
          ) : (
            <StateBox
              icon={current?.status === 'WAITING' ? '🕘' : '💰'}
              title={
                current?.status === 'WAITING'
                  ? `${activeNode} 节点尚未到达`
                  : current
                    ? '该节点资金榜不可用'
                    : '正在读取节点数据'
              }
              detail={current?.note || '数据源若不提供分钟资金流，将明确标注为数据源限制'}
            />
          )}
        </BigCard>
      </div>

      {/* 市场结构 */}
      <div className={`${layout.grid} ${layout.cols3}`} style={{ flexShrink: 0 }}>
        <BigCard
          title="涨跌结构"
          subtitle={
            breadth
              ? `${
                  breadth.scope === 'full' ? '全市场' : '口径不完整'
                } · ${breadth.sample_size} 只${
                  breadth.total_symbols ? ` / 共 ${breadth.total_symbols} 只` : ''
                }`
              : undefined
          }
          icon={<PulseIcon />}
        >
          {breadth ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Row label="上涨" value={`${breadth.up_count} 家`} cls="up" />
              <Row label="下跌" value={`${breadth.down_count} 家`} cls="down" />
              <Row label="平盘" value={`${breadth.flat_count} 家`} />
              <Row label="涨停" value={`${breadth.limit_up_count} 家`} cls="up" />
              <Row label="跌停" value={`${breadth.limit_down_count} 家`} cls="down" />
              <Row label="上涨占比" value={`${fmtNum(breadth.up_ratio)}%`} />
            </div>
          ) : (
            <StateBox icon="🌐" title="市场结构统计中" />
          )}
        </BigCard>

        <BigCard title="资金结构" subtitle="两市合计" icon={<FlowIcon />}>
          {breadth ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Row label="两市成交额" value={fmtMoney(breadth.total_amount)} />
              <Row
                label="主力净流入"
                value={fmtMoney(breadth.total_main_net)}
                cls={breadth.total_main_net >= 0 ? 'up' : 'down'}
              />
              <Row
                label="市场情绪"
                value={`${fmtNum(breadth.sentiment, 1)}`}
                cls={breadth.sentiment >= 0 ? 'up' : 'down'}
              />
              <Row label="数据来源" value={breadth.source || '—'} />
            </div>
          ) : (
            <StateBox icon="💰" title="资金结构统计中" />
          )}
        </BigCard>

        <BigCard title="指数表现" subtitle="主要宽基指数" icon={<ChartIcon />} scroll>
          {(dash.data?.indices.data ?? []).length > 0 ? (
            <table className="grid-table">
              <thead>
                <tr>
                  <th>指数</th>
                  <th className="num">点位</th>
                  <th className="num">涨跌幅</th>
                  <th className="num">成交额</th>
                </tr>
              </thead>
              <tbody>
                {dash.data!.indices.data.map((i) => (
                  <tr key={i.code}>
                    <td>{i.name}</td>
                    <td className={`num ${changeClass(i.change_pct)}`}>{fmtNum(i.price)}</td>
                    <td className={`num ${changeClass(i.change_pct)}`}>{fmtPct(i.change_pct)}</td>
                    <td className="num">{fmtMoney(i.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <StateBox icon="📈" title="指数数据加载中" />
          )}
        </BigCard>
      </div>
    </div>
  )
}

function Row({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12 }}>
      <span style={{ fontSize: 13, color: 'var(--text-mute)', letterSpacing: '0.04em' }}>{label}</span>
      <span
        style={{ fontFamily: 'var(--mono)', fontSize: 17, fontWeight: 600 }}
        className={cls ?? ''}
      >
        {value}
      </span>
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
const PulseIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M3 12h4l2-6 3 12 2-8 2 4h5" />
  </svg>
)
const ChartIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M3 3v18h18M7 15l4-5 3 3 5-7" />
  </svg>
)

export default Market
