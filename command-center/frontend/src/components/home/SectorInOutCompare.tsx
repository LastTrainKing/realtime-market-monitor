/**
 * 首页核心模块 ③：主力资金流入 / 流出板块对比。
 *
 * 数据来源：`/api/sector/compare`。
 *
 * 口径说明（必须如实展示，不允许含糊）：
 * 数据源只提供**当前时点**的板块资金流快照，不提供历史交易日的
 * 板块分钟资金流，因此本模块展示的是"最近一次采集到的板块资金流"，
 * 其所属交易日由接口返回的 `trade_date` 标注。若与首页统一交易日不一致，
 * 界面会显式提示，绝不冒充同一天的数据。
 */

import type { RankEntry, SectorCompareResponse } from '../../api/types'
import StateBox from '../StateBox'
import { changeClass, fmtMoney, fmtPct } from '../../utils/format'
import styles from './home.module.css'

interface Props {
  data: SectorCompareResponse | null
  /** 首页统一交易日（快照给出） */
  snapshotDate: string
  loading: boolean
  error: string | null
}

const KIND_LABEL: Record<string, string> = {
  industry: '行业',
  concept: '概念',
  region: '地域',
}

export function SectorInOutCompare({ data, snapshotDate, loading, error }: Props) {
  if (error) {
    return <StateBox icon="⚠️" title="板块资金流获取失败" detail={error} />
  }
  if (!data) {
    return <StateBox icon="🗂" title="板块资金流正在加载" detail="首次启动需连接行情通道，通常数秒内完成" />
  }
  if (data.inflow_top.length === 0 && data.outflow_top.length === 0) {
    return <StateBox icon="🗂" title="板块资金流尚未就绪" detail={data.note} />
  }

  const mismatch = data.trade_date !== snapshotDate

  return (
    <div className={styles.secWrap}>
      <div className={styles.secMeta}>
        <span className={`chip ${data.source.includes('东方财富') ? 'ok' : 'warn'}`}>
          <i className="dot" />
          {data.source || '来源未知'}
        </span>
        <span className="chip idle">
          <i className="dot" />
          共 {data.board_count} 个板块（行业 {data.industry_count} / 概念 {data.concept_count}）
        </span>
      </div>
      {data.concept_truncated ? (
        <div className={`${styles.notice} ${styles.warn}`}>
          <span className={styles.mark}>⚠️</span>
          <span>
            概念板块只有 <b>{data.concept_count}</b> 个，明显少于东方财富全量（400+）——
            当前很可能是降级通道（腾讯财经）在供数，板块覆盖不完整，请勿据此下结论。
          </span>
        </div>
      ) : null}
      {mismatch ? (
        <div className={`${styles.notice} ${styles.warn}`}>
          <span className={styles.mark}>⚠️</span>
          <span>
            板块快照所属交易日为 <b>{data.trade_date}</b>，与首页统一交易日{' '}
            <b>{snapshotDate}</b> 不一致，请勿把两者当作同一天的数据交叉解读。
          </span>
        </div>
      ) : null}
      <div className={styles.secCols}>
        <Column
          title="净流入 TOP"
          tone="in"
          rows={data.inflow_top}
          emptyText="暂无净流入板块"
          loading={loading}
        />
        <Column
          title="净流出 TOP"
          tone="out"
          rows={data.outflow_top}
          emptyText="暂无净流出板块"
          loading={loading}
        />
      </div>
      <div className={styles.tlNote}>{data.note}</div>
    </div>
  )
}

function Column({
  title,
  tone,
  rows,
  emptyText,
  loading,
}: {
  title: string
  tone: 'in' | 'out'
  rows: RankEntry[]
  emptyText: string
  loading: boolean
}) {
  return (
    <div className={styles.secCol}>
      <div className={`${styles.secHead} ${tone === 'in' ? styles.in : styles.out}`}>
        <span>{title}</span>
        <span style={{ fontFamily: 'var(--mono)', fontWeight: 400, opacity: 0.75 }}>
          {rows.length} 个板块
        </span>
      </div>
      <div className={styles.secBody}>
        {rows.length === 0 ? (
          <div style={{ padding: '14px 12px', fontSize: 12.5, color: 'var(--text-mute)' }}>
            {loading ? '加载中…' : emptyText}
          </div>
        ) : (
          rows.map((r, i) => (
            <div key={r.code || `${r.name}-${i}`} className={styles.secRow}>
              <div className={styles.secName}>
                <span className={styles.secRank}>{r.rank || i + 1}</span>
                <span className={styles.secLabel} title={r.name}>
                  {r.name}
                </span>
                {r.kind ? <span className={styles.secKind}>{KIND_LABEL[r.kind] ?? r.kind}</span> : null}
              </div>
              <div className={styles.secRight}>
                <div className={`${styles.secValue} ${changeClass(r.main_net_inflow)}`}>
                  {fmtMoney(r.main_net_inflow)}
                </div>
                <div className={`${styles.secPct} ${changeClass(r.change_pct)}`}>
                  {fmtPct(r.change_pct)}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default SectorInOutCompare
