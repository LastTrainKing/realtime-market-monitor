/**
 * 首页 —— 四个核心模块。
 *
 * 架构约定（本页最重要的约束）：
 *   ① 指数分时        ← /api/snapshot.index_series
 *   ② 主力资金累计曲线 ← /api/snapshot.flow_series
 *   ③ 板块资金流入流出 ← /api/sector/compare
 *   ④ 时点数据面板     ← /api/timeline/point（分钟由曲线交互驱动）
 *
 * ①②④ 的横轴一律来自历史静态分钟库自带的时间字段（09:31..15:00），
 * **没有任何一个模块用 `new Date()` / 系统时间生成历史图**；
 * ③ 在非交易时段读本地持久化快照。四个模块共用同一个交易日，
 * 由 `snapshot.series_date` 统一给出。
 *
 * 页面顶部另有"指数实时行情快照"条 —— 它**不属于**四个核心模块，
 * 走实时行情通道并自带状态标记，与下方历史模块在视觉与文案上明确区分。
 */

import { useCallback, useEffect, useState } from 'react'

import { api } from '../api/client'
import type {
  DashboardResponse,
  HistoryStatusResponse,
  SectorCompareResponse,
  SnapshotResponse,
} from '../api/types'
import BigCard from '../components/BigCard'
import CapitalFlowCurve from '../components/home/CapitalFlowCurve'
import IndexIntraday from '../components/home/IndexIntraday'
import { HistoryStatusBar, ProvenanceBar } from '../components/home/Provenance'
import SectorInOutCompare from '../components/home/SectorInOutCompare'
import TimelinePanel from '../components/home/TimelinePanel'
import StateBox from '../components/StateBox'
import StatusBadge from '../components/StatusBadge'
import { usePolled } from '../hooks/usePolled'
import { changeClass, fmtMoney, fmtNum, fmtPct } from '../utils/format'
import homeStyles from '../components/home/home.module.css'
import layout from './pages.module.css'

/** 历史快照：磁盘数据只在落盘时变化，30 秒足够 */
const SNAPSHOT_MS = 30_000
/** 实时行情条（非核心模块） */
const QUOTE_MS = 8_000
/** 板块资金流 */
const SECTOR_MS = 30_000
/** 历史库状态 */
const HISTORY_MS = 60_000

export function Dashboard() {
  const snapPoll = usePolled<SnapshotResponse>(() => api.snapshot(), SNAPSHOT_MS)
  const quotePoll = usePolled<DashboardResponse>(() => api.dashboard(), QUOTE_MS)
  const sectorPoll = usePolled<SectorCompareResponse>(() => api.sectorCompare(), SECTOR_MS)
  const histPoll = usePolled<HistoryStatusResponse>(() => api.historyStatus(), HISTORY_MS)

  const snap = snapPoll.data
  const d = quotePoll.data
  const seriesDate = snap?.series_date ?? ''

  // 时点面板的当前分钟：由资金曲线悬停/点击驱动，也允许下拉框直接选。
  // 快照更新时，若当前分钟已不在新横轴上，则回落到该交易日最后一分钟。
  const [activeMinute, setActiveMinute] = useState<string | null>(null)
  useEffect(() => {
    const fs = snap?.flow_series
    if (!fs || fs.length === 0) return
    setActiveMinute((prev) => {
      if (prev && fs.some((b) => b.minute === prev)) return prev
      return fs[fs.length - 1].minute
    })
  }, [snap])

  const minuteOptions = snap?.flow_series.map((b) => b.minute) ?? []

  const [syncing, setSyncing] = useState(false)
  const [syncMessage, setSyncMessage] = useState('')
  const onSync = useCallback(async () => {
    setSyncing(true)
    setSyncMessage('')
    try {
      const r = await api.historySync()
      setSyncMessage(r.message)
    } catch (e) {
      setSyncMessage((e as Error).message || '补齐请求失败')
    } finally {
      setSyncing(false)
      histPoll.refresh()
    }
  }, [histPoll])

  const breadth = d?.breadth?.data

  return (
    <div className={layout.page}>
      <div className={layout.pageHead}>
        <div>
          <h1 className={layout.pageTitle}>资金流向指挥中心</h1>
          <div className={layout.pageDesc}>
            {snap
              ? `统一数据交易日 ${snap.series_date} · ${snap.time.market_status_label} · ${snap.time.data_mode_label}`
              : '正在读取本地历史分钟库…'}
          </div>
        </div>
        <div className={layout.headRight}>
          {breadth ? (
            <>
              <span className="chip up">
                <i className="dot" />
                涨 {breadth.up_count}
              </span>
              <span className="chip down">
                <i className="dot" />
                跌 {breadth.down_count}
              </span>
              <span className="chip up">
                <i className="dot" />
                涨停 {breadth.limit_up_count}
              </span>
              <span className="chip down">
                <i className="dot" />
                跌停 {breadth.limit_down_count}
              </span>
              <span className="chip idle">
                <i className="dot" />
                成交 {fmtMoney(breadth.total_amount)}
              </span>
              <span className={`chip ${breadth.total_main_net >= 0 ? 'up' : 'down'}`}>
                <i className="dot" />
                主力 {fmtMoney(breadth.total_main_net)}
              </span>
            </>
          ) : null}
          {d?.degraded ? (
            <span className="chip warn">
              <i className="dot" />
              降级运行
            </span>
          ) : null}
        </div>
      </div>

      {/* 数据口径：系统时间 / 行情时间 / 交易日 / 数据模式 全部拆开显示 */}
      <ProvenanceBar snap={snap} />

      {/* 指数实时行情快照（不属于四个核心模块，明确标注走实时通道） */}
      <div className={`${layout.grid} ${layout.cols6}`} style={{ flexShrink: 0 }}>
        {(d?.indices?.data ?? []).map((idx) => (
          <div key={idx.code} className={layout.indexCard}>
            <span className={layout.indexName}>{idx.name}</span>
            <span className={`${layout.indexPrice} ${changeClass(idx.change_pct)}`}>
              {fmtNum(idx.price)}
            </span>
            <div className={layout.indexMeta}>
              <span className={changeClass(idx.change_pct)}>{fmtPct(idx.change_pct)}</span>
              <span className={changeClass(idx.change_amount)}>
                {idx.change_amount > 0 ? '+' : ''}
                {fmtNum(idx.change_amount)}
              </span>
            </div>
            <span className={layout.indexSub}>
              成交 {fmtMoney(idx.amount)} · 量 {fmtNum(idx.volume / 1e4, 0)}万手
            </span>
          </div>
        ))}
        {!d || (d.indices?.data ?? []).length === 0 ? (
          <div className={layout.indexCard} style={{ gridColumn: 'span 3' }}>
            <StateBox
              icon="📈"
              title="实时指数行情正在连接"
              detail={d?.indices?.message || '本条走实时行情通道，与下方历史模块相互独立'}
            />
          </div>
        ) : null}
      </div>

      {/* 四个核心模块 */}
      <div className={homeStyles.modGrid}>
          {/* ① 指数分时 */}
          <BigCard
            title="① 指数分时"
            subtitle={
              snap
                ? `${snap.index_name} · 本地历史分钟库 · ${snap.index_summary.points} 点 · ${snap.index_summary.first_minute}→${snap.index_summary.last_minute}`
                : '正在读取本地历史分钟库'
            }
            style={{ minHeight: 0 }}
          >
            <IndexIntraday
              series={snap?.index_series ?? []}
              summary={snap?.index_summary ?? null}
              prevClose={snap?.index_prev_close ?? null}
              seriesDate={seriesDate}
              complete={snap?.index_complete ?? false}
              loading={snapPoll.loading && !snap}
            />
          </BigCard>

          {/* ③ 板块资金流入流出对比 */}
          <BigCard
            title="③ 主力资金流入流出板块对比"
            subtitle={
              sectorPoll.data
                ? `交易日 ${sectorPoll.data.trade_date} · 数据状态：${sectorPoll.data.status}`
                : '正在读取板块资金流'
            }
            style={{ minHeight: 0 }}
            actions={
              <StatusBadge
                status={sectorPoll.data?.status ?? 'WAITING'}
                sourceLabel={
                  sectorPoll.data?.source ||
                  (sectorPoll.data?.status === 'REALTIME' ? '实时通道' : '本地持久化快照')
                }
              />
            }
          >
            <SectorInOutCompare
              data={sectorPoll.data}
              snapshotDate={seriesDate}
              loading={sectorPoll.loading && !sectorPoll.data}
              error={sectorPoll.error}
            />
          </BigCard>

          {/* ② 主力资金累计曲线 */}
          <BigCard
            title="② 主力资金累计曲线"
            subtitle={
              snap
                ? `东财大盘资金流（fflow/kline）· 接口本身即累计口径 · 本地历史分钟库 · ${snap.flow_summary.points} 点 · ${snap.flow_summary.first_minute}→${snap.flow_summary.last_minute}`
                : '正在读取本地历史分钟库'
            }
            style={{ minHeight: 0 }}
            actions={
              snap ? (
                <span className={`chip ${snap.flow_complete ? 'ok' : 'warn'}`}>
                  <i className="dot" />
                  {snap.flow_complete ? '完整交易日' : '序列不完整'}
                </span>
              ) : null
            }
          >
            <CapitalFlowCurve
              series={snap?.flow_series ?? []}
              summary={snap?.flow_summary ?? null}
              seriesDate={seriesDate}
              activeMinute={activeMinute}
              onPickMinute={setActiveMinute}
              loading={snapPoll.loading && !snap}
            />
          </BigCard>

          {/* ④ 时点数据面板 */}
          <BigCard
            title="④ 时点数据面板"
            subtitle="与上方资金曲线同一交易日、同一分钟"
            style={{ minHeight: 0 }}
          >
            <TimelinePanel
              minute={activeMinute}
              tradeDate={seriesDate}
              minuteOptions={minuteOptions}
              onPickMinute={setActiveMinute}
            />
          </BigCard>
      </div>

      {/* 历史库状态 */}
      <HistoryStatusBar
        status={histPoll.data}
        onSync={onSync}
        syncing={syncing}
        syncMessage={syncMessage}
      />
    </div>
  )
}

export default Dashboard
