import { useEffect, useState } from 'react'

import { api } from '../api/client'
import type { AppConfig, SystemStatus } from '../api/types'
import BigCard from '../components/BigCard'
import StateBox from '../components/StateBox'
import StatusBadge from '../components/StatusBadge'
import { usePolled } from '../hooks/usePolled'
import { useAppStore } from '../stores/useAppStore'
import { fmtBytes, fmtDuration, fmtInt, fmtNum, statusText } from '../utils/format'
import layout from './pages.module.css'

/** 字段级默认值合并：后端任何字段缺失都不会导致界面崩溃 */
function withDefaults<T extends object>(value: T | undefined | null, defaults: T): T {
  return value ? { ...defaults, ...value } : defaults
}

const EMPTY_NET = {
  total_requests: 0,
  success: 0,
  failed: 0,
  deduped: 0,
  rate_limited: 0,
  circuit_open: false,
  circuit_opens: 0,
  avg_latency_ms: 0,
  hosts: [] as { host: string; state: string; success: number; failure: number; avg_latency_ms: number }[],
}

const EMPTY_DB = {
  path: '—',
  size_bytes: 0,
  snapshots: 0,
  ranking_events: 0,
  minute_rows: 0,
  trading_days: 0,
  last_write: '',
}

const EMPTY_CACHE = { entries: 0, hits: 0, misses: 0, hit_rate: 0, memory_kb: 0 }

/** 设置页（第 6.11 / 8.20 章）：运行状态自检 + 参数配置 */
export function Settings() {
  const showToast = useAppStore((s) => s.showToast)
  const status = usePolled<SystemStatus>(() => api.systemStatus(), 6000)
  const [cfg, setCfg] = useState<AppConfig | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api
      .config()
      .then((c) => setCfg(c))
      .catch(() => showToast('读取配置失败', 'err'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const update = <K extends keyof AppConfig>(key: K, value: AppConfig[K]) => {
    setCfg((c) => (c ? { ...c, [key]: value } : c))
    setDirty(true)
  }

  const updateRefresh = (key: keyof AppConfig['refresh'], value: number) => {
    setCfg((c) => (c ? { ...c, refresh: { ...c.refresh, [key]: value } } : c))
    setDirty(true)
  }

  const updateNet = (key: keyof AppConfig['net'], value: number) => {
    setCfg((c) => (c ? { ...c, net: { ...c.net, [key]: value } } : c))
    setDirty(true)
  }

  const toggleBoard = (id: string) => {
    setCfg((c) =>
      c
        ? {
            ...c,
            boards: c.boards.map((b) => (b.id === id ? { ...b, enabled: !b.enabled } : b)),
          }
        : c,
    )
    setDirty(true)
  }

  const save = async () => {
    if (!cfg) return
    setSaving(true)
    try {
      const res = await api.saveConfig(cfg)
      showToast(res.message, res.ok ? 'ok' : 'warn')
      setDirty(false)
    } catch (err) {
      showToast((err as Error).message, 'err')
    } finally {
      setSaving(false)
    }
  }

  const raw = status.data
  // 防御式取值：任何字段缺失都不应导致界面崩溃
  const s = raw
    ? {
        ...raw,
        collectors: raw.collectors ?? [],
        notes: raw.notes ?? [],
        gated_sources: raw.gated_sources ?? [],
        net: withDefaults(raw.net, EMPTY_NET),
        db: withDefaults(raw.db, EMPTY_DB),
        cache: withDefaults(raw.cache, EMPTY_CACHE),
      }
    : null

  return (
    <div className={layout.page}>
      <div className={layout.pageHead}>
        <div>
          <h1 className={layout.pageTitle}>设置与运行状态</h1>
          <div className={layout.pageDesc}>
            {s
              ? `版本 ${s.version} · 已运行 ${fmtDuration(s.uptime_secs)} · ${s.trade_date}`
              : '正在读取运行状态…'}
          </div>
        </div>
        <div className={layout.headRight}>
          {dirty ? (
            <span className="chip warn">
              <i className="dot" />
              有未保存的修改
            </span>
          ) : null}
          <button className="btn primary" onClick={() => void save()} disabled={!dirty || saving}>
            {saving ? '保存中…' : '保存配置'}
          </button>
        </div>
      </div>

      <div className={`${layout.row} ${layout.fill}`}>
        {/* 左：运行状态 */}
        <div className={`${layout.grow} ${layout.page}`} style={{ flex: 1.15 }}>
          <BigCard
            title="采集任务状态"
            subtitle="每个采集器独立运行、独立计时"
            icon={<PulseIcon />}
            scroll
            style={{ flex: 1.2, minHeight: 0 }}
          >
            {(s?.collectors?.length ?? 0) > 0 ? (
              <table className="grid-table">
                <thead>
                  <tr>
                    <th>采集任务</th>
                    <th>状态</th>
                    <th className="num">数据量</th>
                    <th className="num">耗时</th>
                    <th className="num">成功/失败</th>
                    <th>最后更新</th>
                  </tr>
                </thead>
                <tbody>
                  {s!.collectors.map((c) => (
                    <tr key={c.name}>
                      <td>{c.label}</td>
                      <td>
                        <StatusBadge
                          status={c.status}
                          statusLabel={statusText(c.status)}
                          compact
                        />
                      </td>
                      <td className="num">{fmtInt(c.data_points)}</td>
                      <td className="num">{c.latency_ms}ms</td>
                      <td className="num">
                        <span className="up">{c.runs - c.failures}</span>
                        <span style={{ color: 'var(--text-mute)' }}> / </span>
                        <span className={c.failures > 0 ? 'down' : ''}>{c.failures}</span>
                      </td>
                      <td style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>
                        {c.last_success}
                        {c.last_error ? (
                          <div style={{ color: '#ffc4ca', fontSize: 11, maxWidth: 260, whiteSpace: 'normal' }}>
                            {c.last_error}
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <StateBox icon="⚙️" title="正在读取采集状态" />
            )}
          </BigCard>

          <BigCard
            title="数据通道健康度"
            subtitle="请求限流 / 去重 / 熔断 / 主机延迟"
            icon={<NetIcon />}
            scroll
            style={{ flex: 1, minHeight: 0 }}
          >
            {s ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                <div className={`${layout.grid} ${layout.cols4}`} style={{ gap: 10 }}>
                  <MiniStat label="总请求" value={fmtInt(s.net.total_requests)} />
                  <MiniStat label="成功" value={fmtInt(s.net.success)} cls="up" />
                  <MiniStat label="失败" value={fmtInt(s.net.failed)} cls={s.net.failed > 0 ? 'down' : ''} />
                  <MiniStat label="请求合并" value={fmtInt(s.net.deduped)} />
                  <MiniStat label="限流等待" value={fmtInt(s.net.rate_limited)} />
                  <MiniStat label="平均延迟" value={`${s.net.avg_latency_ms}ms`} />
                  <MiniStat label="熔断次数" value={fmtInt(s.net.circuit_opens)} />
                  <MiniStat
                    label="熔断状态"
                    value={s.net.circuit_open ? '已熔断' : '正常'}
                    cls={s.net.circuit_open ? 'down' : 'up'}
                  />
                </div>
                {s.net.hosts.length > 0 ? (
                  <table className="grid-table">
                    <thead>
                      <tr>
                        <th>数据主机</th>
                        <th>状态</th>
                        <th className="num">成功</th>
                        <th className="num">失败</th>
                        <th className="num">平均延迟</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.net.hosts.map((h) => (
                        <tr key={h.host}>
                          <td style={{ fontFamily: 'var(--mono)', fontSize: 12.5 }}>{h.host}</td>
                          <td>
                            <span className={`chip ${h.state === 'closed' ? 'ok' : h.state === 'half_open' ? 'warn' : 'err'}`}>
                              <i className="dot" />
                              {h.state === 'closed' ? '正常' : h.state === 'half_open' ? '探测中' : '熔断'}
                            </span>
                          </td>
                          <td className="num">{fmtInt(h.success)}</td>
                          <td className="num">{fmtInt(h.failure)}</td>
                          <td className="num">{h.avg_latency_ms}ms</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <StateBox icon="🌐" title="尚未产生网络请求记录" />
                )}
              </div>
            ) : (
              <StateBox icon="🌐" title="正在读取通道状态" />
            )}
          </BigCard>

          <BigCard title="本地存储" subtitle="数据仅保存在本机，不上传任何服务器" icon={<DbIcon />} style={{ flexShrink: 0 }}>
            {s ? (
              <div className={layout.kv}>
                <span className={layout.kvKey}>数据库文件</span>
                <span className={layout.kvVal}>{s.db.path}</span>
                <span className={layout.kvKey}>占用空间</span>
                <span className={layout.kvVal}>{fmtBytes(s.db.size_bytes)}</span>
                <span className={layout.kvKey}>节点快照</span>
                <span className={layout.kvVal}>{fmtInt(s.db.snapshots)} 条</span>
                <span className={layout.kvKey}>榜单事件</span>
                <span className={layout.kvVal}>{fmtInt(s.db.ranking_events)} 条</span>
                <span className={layout.kvKey}>分钟数据</span>
                <span className={layout.kvVal}>{fmtInt(s.db.minute_rows)} 行</span>
                <span className={layout.kvKey}>交易日数</span>
                <span className={layout.kvVal}>{fmtInt(s.db.trading_days)} 天</span>
                <span className={layout.kvKey}>缓存条目</span>
                <span className={layout.kvVal}>
                  {fmtInt(s.cache.entries)}（命中率 {fmtNum(s.cache.hit_rate * 100, 1)}%）
                </span>
              </div>
            ) : (
              <StateBox icon="🗄" title="正在读取存储状态" />
            )}
          </BigCard>
        </div>

        {/* 右：配置 */}
        <div className={`${layout.grow} ${layout.page}`} style={{ flex: 1 }}>
          <BigCard title="刷新频率" subtitle="分级刷新，避免高频请求" icon={<ClockIcon />} scroll style={{ flexShrink: 0 }}>
            {cfg ? (
              <div>
                <NumRow
                  label="指数 / 大盘"
                  hint="秒级刷新重要指标"
                  value={cfg.refresh.index_interval_secs}
                  min={1}
                  max={60}
                  onChange={(v) => updateRefresh('index_interval_secs', v)}
                />
                <NumRow
                  label="板块资金流"
                  hint="行业与概念板块排名"
                  value={cfg.refresh.sector_interval_secs}
                  min={3}
                  max={120}
                  onChange={(v) => updateRefresh('sector_interval_secs', v)}
                />
                <NumRow
                  label="个股资金流"
                  hint="覆盖全部九个榜单所需字段"
                  value={cfg.refresh.market_interval_secs}
                  min={5}
                  max={180}
                  onChange={(v) => updateRefresh('market_interval_secs', v)}
                />
                <NumRow
                  label="全市场扫描"
                  hint="分页拉取全部 A 股，用于真实涨跌家数"
                  value={cfg.refresh.full_scan_interval_secs}
                  min={30}
                  max={600}
                  onChange={(v) => updateRefresh('full_scan_interval_secs', v)}
                />
                <NumRow
                  label="分钟数据落库"
                  hint="用于历史节点重建与复盘"
                  value={cfg.refresh.minute_persist_secs}
                  min={20}
                  max={300}
                  onChange={(v) => updateRefresh('minute_persist_secs', v)}
                />
              </div>
            ) : (
              <StateBox icon="⏱" title="正在读取配置" />
            )}
          </BigCard>

          <BigCard title="请求保护" subtitle="限流 / 退避 / 熔断（第 4.6 / 8.4 章）" icon={<ShieldIcon />} scroll style={{ flexShrink: 0 }}>
            {cfg ? (
              <div>
                <NumRow
                  label="每秒最大请求"
                  hint="全局限流上限"
                  value={cfg.net.requests_per_second}
                  min={1}
                  max={30}
                  onChange={(v) => updateNet('requests_per_second', v)}
                />
                <NumRow
                  label="每分钟最大请求"
                  hint="保护数据源，避免触发 IP 限制"
                  value={cfg.net.requests_per_minute}
                  min={10}
                  max={1200}
                  onChange={(v) => updateNet('requests_per_minute', v)}
                />
                <NumRow
                  label="并发请求数"
                  hint="同时进行的请求数量"
                  value={cfg.net.max_concurrent}
                  min={1}
                  max={16}
                  onChange={(v) => updateNet('max_concurrent', v)}
                />
                <NumRow
                  label="最大重试次数"
                  hint="指数退避：1s → 2s → 4s → 8s"
                  value={cfg.net.retry_max}
                  min={0}
                  max={6}
                  onChange={(v) => updateNet('retry_max', v)}
                />
                <NumRow
                  label="熔断阈值"
                  hint="连续失败多少次后暂停该通道"
                  value={cfg.net.breaker_failure_threshold}
                  min={2}
                  max={20}
                  onChange={(v) => updateNet('breaker_failure_threshold', v)}
                />
                <NumRow
                  label="熔断冷却"
                  hint="暂停后多少秒尝试恢复（秒）"
                  value={cfg.net.breaker_cooldown_secs}
                  min={10}
                  max={600}
                  onChange={(v) => updateNet('breaker_cooldown_secs', v)}
                />
                <NumRow
                  label="请求最小间隔"
                  hint="毫秒，防止突发请求"
                  value={cfg.net.min_interval_ms}
                  min={0}
                  max={2000}
                  onChange={(v) => updateNet('min_interval_ms', v)}
                />
              </div>
            ) : (
              <StateBox icon="🛡" title="正在读取配置" />
            )}
          </BigCard>

          <BigCard title="数据源开关" subtitle="多通道自动降级" icon={<NetIcon />} style={{ flexShrink: 0 }}>
            {cfg ? (
              <div>
                <SwitchRow
                  label="东方财富（主数据源）"
                  hint="提供完整榜单与资金流字段"
                  on={cfg.enable_eastmoney}
                  onChange={(v) => update('enable_eastmoney', v)}
                />
                <SwitchRow
                  label="腾讯财经（备用通道）"
                  hint="提供主力净流入与板块数据"
                  on={cfg.enable_tencent}
                  onChange={(v) => update('enable_tencent', v)}
                />
                <SwitchRow
                  label="新浪财经（备用通道）"
                  hint="提供行情与换手率数据"
                  on={cfg.enable_sina}
                  onChange={(v) => update('enable_sina', v)}
                />
                <SwitchRow
                  label="收盘后继续采集"
                  hint="关闭后非交易时段自动降频"
                  on={cfg.collect_after_close}
                  onChange={(v) => update('collect_after_close', v)}
                />
                <SwitchRow
                  label="启动进入全屏"
                  hint="按 F11 可随时切换窗口模式"
                  on={cfg.fullscreen}
                  onChange={(v) => update('fullscreen', v)}
                />
                <SwitchRow
                  label="退出时二次确认"
                  hint="避免误触关闭"
                  on={cfg.confirm_on_exit}
                  onChange={(v) => update('confirm_on_exit', v)}
                />
              </div>
            ) : (
              <StateBox icon="🔌" title="正在读取配置" />
            )}
          </BigCard>

          <BigCard
            title="九榜配置"
            subtitle="榜单完全由配置驱动，可随时启停"
            icon={<BoardIcon />}
            scroll
            style={{ flex: 1, minHeight: 0 }}
          >
            {cfg ? (
              <div>
                {cfg.boards.map((b) => (
                  <SwitchRow
                    key={b.id}
                    label={`${b.name}（${b.scope === 'sector' ? '板块' : '个股'}）`}
                    hint={`计算口径：${b.kind} · 容量 ${b.size}`}
                    on={b.enabled}
                    onChange={() => toggleBoard(b.id)}
                  />
                ))}
                <div className={layout.formRow}>
                  <div>
                    <div className={layout.formLabel}>早盘节点</div>
                    <div className={layout.formHint}>多个时间点用英文逗号分隔</div>
                  </div>
                  <input
                    className={layout.input}
                    style={{ minWidth: 200 }}
                    value={cfg.nodes.join(',')}
                    onChange={(e) => {
                      const nodes = e.target.value
                        .split(',')
                        .map((s) => s.trim())
                        .filter((s) => /^\d{1,2}:\d{2}$/.test(s))
                      update('nodes', nodes)
                    }}
                  />
                </div>
              </div>
            ) : (
              <StateBox icon="🏆" title="正在读取配置" />
            )}
          </BigCard>
        </div>
      </div>
    </div>
  )
}

function NumRow({
  label,
  hint,
  value,
  min,
  max,
  onChange,
}: {
  label: string
  hint: string
  value: number
  min: number
  max: number
  onChange: (v: number) => void
}) {
  return (
    <div className={layout.formRow}>
      <div>
        <div className={layout.formLabel}>{label}</div>
        <div className={layout.formHint}>{hint}</div>
      </div>
      <input
        className={layout.input}
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => {
          const v = Number(e.target.value)
          if (!Number.isNaN(v)) onChange(Math.max(min, Math.min(max, v)))
        }}
      />
    </div>
  )
}

function SwitchRow({
  label,
  hint,
  on,
  onChange,
}: {
  label: string
  hint: string
  on: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className={layout.formRow}>
      <div>
        <div className={layout.formLabel}>{label}</div>
        <div className={layout.formHint}>{hint}</div>
      </div>
      <button
        className={`${layout.switch} ${on ? layout.on : ''}`}
        onClick={() => onChange(!on)}
        aria-label={label}
      />
    </div>
  )
}

function MiniStat({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className={layout.stat}>
      <span className={layout.statLabel}>{label}</span>
      <span className={`${layout.statValue} ${cls ?? ''}`} style={{ fontSize: 18 }}>
        {value}
      </span>
    </div>
  )
}

const PulseIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M3 12h4l2-6 3 12 2-8 2 4h5" />
  </svg>
)
const NetIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3c2.5 2.7 3.8 5.8 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.8-3.8-9S9.5 5.7 12 3z" />
  </svg>
)
const DbIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <ellipse cx="12" cy="6" rx="8" ry="3" />
    <path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
  </svg>
)
const ClockIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </svg>
)
const ShieldIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 3l7 3v6c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6z" />
  </svg>
)
const BoardIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
  </svg>
)

export default Settings
