import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { AnomalyResponse } from '../api/types'
import BigCard from '../components/BigCard'
import RankTable from '../components/RankTable'
import StateBox from '../components/StateBox'
import StatusBadge from '../components/StatusBadge'
import { usePolled } from '../hooks/usePolled'
import { useAppStore } from '../stores/useAppStore'
import layout from './pages.module.css'

/**
 * 个股异动页（第 4.14 / 8.11 章）
 * 3 个节点 × 3 个榜单：量比 Top10、成交量 Top10、主力净流入 Top10
 * 三个榜独立计算，禁止一个排序冒充三个榜。
 */
export function Anomaly() {
  const setFocusStock = useAppStore((s) => s.setFocusStock)
  const showToast = useAppStore((s) => s.showToast)
  const poll = usePolled<AnomalyResponse>(() => api.anomaly(), 8000)
  const [activeNode, setActiveNode] = useState('')
  const [rebuilding, setRebuilding] = useState(false)

  const nodes = poll.data?.nodes ?? []

  useEffect(() => {
    if (!activeNode && nodes.length > 0) {
      const preferred = nodes.find((n) => n.ratio_top.length > 0) ?? nodes[0]
      setActiveNode(preferred.node)
    }
  }, [nodes, activeNode])

  const current = nodes.find((n) => n.node === activeNode)

  const handleRebuild = async () => {
    if (!activeNode) return
    setRebuilding(true)
    try {
      const res = await api.rebuildNode(activeNode)
      showToast(res.message, res.ok ? 'ok' : 'warn')
      poll.refresh()
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
          <h1 className={layout.pageTitle}>个股异动</h1>
          <div className={layout.pageDesc}>
            {poll.data
              ? `${poll.data.trade_date} · 每个节点独立计算量比 / 成交量 / 主力净流入三个榜单`
              : '正在读取异动数据…'}
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
              updatedAt={undefined}
              message={poll.data?.note}
            />
          </div>
          <span style={{ fontSize: 13, color: 'var(--text-mute)', fontFamily: 'var(--mono)' }}>
            数据来源：{current.origin === 'realtime' ? '实时捕获' : current.origin === 'replay' ? '历史重建' : '等待中'}
          </span>
        </div>
      ) : null}

      <div className={`${layout.grid} ${layout.cols3} ${layout.fill}`}>
        <BigCard
          title={`量比 TOP 10${current ? ` · ${current.node}` : ''}`}
          subtitle="当前成交量 / 历史平均成交量"
          icon={<GaugeIcon />}
          scroll
        >
          {current && current.ratio_top.length > 0 ? (
            <RankTable entries={current.ratio_top} onSelect={setFocusStock} />
          ) : (
            <StateBox
              icon={current?.status === 'WAITING' ? '🕘' : '📉'}
              title={
                current?.status === 'WAITING'
                  ? `${activeNode} 节点尚未到达`
                  : '该节点量比榜不可用'
              }
              detail="量比需要数据源提供量比字段或历史基线；缺失时会明确标注，不会用其他指标替代"
            />
          )}
        </BigCard>

        <BigCard
          title={`成交量 TOP 10${current ? ` · ${current.node}` : ''}`}
          subtitle="开盘以来累计成交量"
          icon={<BarIcon />}
          scroll
        >
          {current && current.volume_top.length > 0 ? (
            <RankTable entries={current.volume_top} onSelect={setFocusStock} />
          ) : (
            <StateBox
              icon={current?.status === 'WAITING' ? '🕘' : '📊'}
              title={current?.status === 'WAITING' ? `${activeNode} 节点尚未到达` : '该节点成交量榜不可用'}
            />
          )}
        </BigCard>

        <BigCard
          title={`主力净流入 TOP 10${current ? ` · ${current.node}` : ''}`}
          subtitle="截至该节点主力资金净流入"
          icon={<FlowIcon />}
          scroll
        >
          {current && current.netflow_top.length > 0 ? (
            <RankTable entries={current.netflow_top} onSelect={setFocusStock} />
          ) : (
            <StateBox
              icon={current?.status === 'WAITING' ? '🕘' : '💰'}
              title={current?.status === 'WAITING' ? `${activeNode} 节点尚未到达` : '该节点资金榜不可用'}
            />
          )}
        </BigCard>
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

const GaugeIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 14l4-4M4 18a8 8 0 1 1 16 0" />
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

export default Anomaly
