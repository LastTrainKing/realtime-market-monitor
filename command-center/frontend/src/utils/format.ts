/** 数值格式化工具（纯展示逻辑） */

export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const abs = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (abs >= 1e8) return `${sign}${(abs / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${sign}${(abs / 1e4).toFixed(2)}万`
  return `${sign}${abs.toFixed(0)}`
}

export function fmtVolume(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿手`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)}万手`
  return `${v.toFixed(0)}手`
}

export function fmtPct(v: number | null | undefined, digits = 2, signed = true): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const s = v.toFixed(digits)
  return signed && v > 0 ? `+${s}%` : `${s}%`
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

export function fmtInt(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return v.toLocaleString('zh-CN')
}

/** 涨跌颜色类名（A 股习惯：涨红跌绿） */
export function changeClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || Math.abs(v) < 0.005) return 'flat'
  return v > 0 ? 'up' : 'down'
}

export function fmtBytes(v: number): string {
  if (v >= 1024 * 1024 * 1024) return `${(v / 1024 / 1024 / 1024).toFixed(2)} GB`
  if (v >= 1024 * 1024) return `${(v / 1024 / 1024).toFixed(2)} MB`
  if (v >= 1024) return `${(v / 1024).toFixed(1)} KB`
  return `${v} B`
}

export function fmtDuration(secs: number): string {
  if (secs < 60) return `${secs} 秒`
  if (secs < 3600) return `${Math.floor(secs / 60)} 分 ${secs % 60} 秒`
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  return `${h} 小时 ${m} 分`
}

export function fmtRankDelta(d: number): string {
  if (d > 0) return `↑${d}`
  if (d < 0) return `↓${Math.abs(d)}`
  return '—'
}

export function marketLabel(code: number): string {
  if (code === 1) return '沪'
  if (code === 2) return '京'
  return '深'
}

/** 数据状态 → 视觉级别 */
export function statusTone(status: string): 'ok' | 'warn' | 'err' | 'idle' {
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

/** 数据状态中文标签（与后端一致） */
export function statusText(status: string): string {
  const map: Record<string, string> = {
    REALTIME: '实时',
    CACHE: '缓存',
    REBUILD: '历史重建',
    DELAY: '延迟',
    ERROR: '数据源异常',
    UNAVAILABLE: '暂无数据',
    WAITING: '等待生成',
    PARTIAL: '部分缺失',
    DATA_SOURCE_LIMITATION: '数据源限制',
  }
  return map[status] ?? status
}

/** 数据来源中文标签 */
export function sourceText(source: string): string {
  const map: Record<string, string> = {
    eastmoney_realtime: '东方财富实时',
    eastmoney_history: '东方财富历史',
    sina_realtime: '新浪财经',
    tencent_realtime: '腾讯财经',
    memory_cache: '本地缓存',
    database_replay: '本地数据库',
    none: '无数据源',
  }
  return map[source] ?? source
}
