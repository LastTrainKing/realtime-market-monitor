import { describe, expect, it } from 'vitest'

import {
  changeClass,
  fmtBytes,
  fmtDuration,
  fmtInt,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtRankDelta,
  fmtVolume,
  marketLabel,
  sourceText,
  statusText,
  statusTone,
} from './format'

describe('金额格式化', () => {
  it('按亿 / 万分级展示', () => {
    expect(fmtMoney(1.285e9)).toBe('12.85亿')
    expect(fmtMoney(-3.4e4)).toBe('-3.40万')
    expect(fmtMoney(520)).toBe('520')
  })

  it('空值不产生 NaN', () => {
    expect(fmtMoney(null)).toBe('—')
    expect(fmtMoney(undefined)).toBe('—')
    expect(fmtMoney(Number.NaN)).toBe('—')
  })
})

describe('成交量格式化', () => {
  it('按亿手 / 万手分级', () => {
    expect(fmtVolume(2.5e8)).toBe('2.50亿手')
    expect(fmtVolume(38331)).toBe('3.83万手')
    expect(fmtVolume(500)).toBe('500手')
  })
})

describe('涨跌幅格式化', () => {
  it('正数带 + 号', () => {
    expect(fmtPct(1.86)).toBe('+1.86%')
    expect(fmtPct(-2.5)).toBe('-2.50%')
    expect(fmtPct(0)).toBe('0.00%')
  })

  it('可以关闭正号', () => {
    expect(fmtPct(1.86, 2, false)).toBe('1.86%')
  })
})

describe('涨跌颜色（A 股习惯：涨红跌绿）', () => {
  it('正数为 up（红）', () => {
    expect(changeClass(1.2)).toBe('up')
    expect(changeClass(-1.2)).toBe('down')
    expect(changeClass(0)).toBe('flat')
    expect(changeClass(0.001)).toBe('flat')
    expect(changeClass(null)).toBe('flat')
  })
})

describe('其它格式化', () => {
  it('整数千分位', () => {
    expect(fmtInt(1234567)).toBe('1,234,567')
    expect(fmtInt(null)).toBe('—')
  })

  it('字节单位', () => {
    expect(fmtBytes(512)).toBe('512 B')
    expect(fmtBytes(2048)).toBe('2.0 KB')
    expect(fmtBytes(5 * 1024 * 1024)).toBe('5.00 MB')
  })

  it('运行时长', () => {
    expect(fmtDuration(45)).toBe('45 秒')
    expect(fmtDuration(125)).toBe('2 分 5 秒')
    expect(fmtDuration(7325)).toBe('2 小时 2 分')
  })

  it('排名变化箭头', () => {
    expect(fmtRankDelta(3)).toBe('↑3')
    expect(fmtRankDelta(-2)).toBe('↓2')
    expect(fmtRankDelta(0)).toBe('—')
  })

  it('数值保留小数', () => {
    expect(fmtNum(1.2345, 2)).toBe('1.23')
    expect(fmtNum(undefined)).toBe('—')
  })

  it('市场前缀', () => {
    expect(marketLabel(1)).toBe('沪')
    expect(marketLabel(0)).toBe('深')
    expect(marketLabel(2)).toBe('京')
  })
})

describe('数据状态映射（第 5.18 章：禁止统一显示暂无数据）', () => {
  it('实时为绿色，缓存/重建为黄色，异常为红色', () => {
    expect(statusTone('REALTIME')).toBe('ok')
    expect(statusTone('CACHE')).toBe('warn')
    expect(statusTone('REBUILD')).toBe('warn')
    expect(statusTone('PARTIAL')).toBe('warn')
    expect(statusTone('ERROR')).toBe('err')
    expect(statusTone('UNAVAILABLE')).toBe('err')
    expect(statusTone('DATA_SOURCE_LIMITATION')).toBe('err')
    expect(statusTone('WAITING')).toBe('idle')
  })

  it('状态与来源都有中文文案', () => {
    expect(statusText('REALTIME')).toBe('实时')
    expect(statusText('REBUILD')).toBe('历史重建')
    expect(statusText('DATA_SOURCE_LIMITATION')).toBe('数据源限制')
    expect(sourceText('eastmoney_realtime')).toBe('东方财富实时')
    expect(sourceText('tencent_realtime')).toBe('腾讯财经')
    expect(sourceText('unknown_value')).toBe('unknown_value')
  })
})
