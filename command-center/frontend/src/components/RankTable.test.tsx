import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { RankEntry } from '../api/types'
import RankTable, { ValueTable } from './RankTable'
import StateBox from './StateBox'
import StatusBadge from './StatusBadge'

function entry(rank: number, code: string, name: string, value: number): RankEntry {
  return {
    rank,
    code,
    name,
    value,
    value_label: value.toFixed(2) + '亿',
    change_pct: 3.2,
    price: 10,
    volume_ratio: 1.5,
    turnover_rate: 2.1,
    main_net_inflow: value * 1e8,
    amount: 1e9,
    volume: 1e6,
    rank_delta: 1,
    first_seen: '09:35',
    appear_count: 3,
    boards: ['volume'],
    history: ['09:35'],
  }
}

describe('RankTable', () => {
  it('渲染榜单条目与上榜次数', () => {
    const entries = [entry(1, '600519', '贵州茅台', 5.4), entry(2, '000001', '平安银行', 2.1)]
    render(<RankTable entries={entries} />)
    expect(screen.getByText('贵州茅台')).toBeTruthy()
    expect(screen.getByText('平安银行')).toBeTruthy()
    expect(screen.getByText('600519')).toBeTruthy()
    // 指标列与主力净流入列都会展示该数值
    expect(screen.getAllByText('5.40亿').length).toBeGreaterThan(0)
    expect(screen.getAllByText('3').length).toBeGreaterThan(0)
  })

  it('空数据展示明确的空状态而非空白', () => {
    render(<RankTable entries={[]} />)
    expect(screen.getByText('该榜单暂无数据')).toBeTruthy()
  })

  it('点击行可回传股票代码', () => {
    const onSelect = vi.fn()
    render(<RankTable entries={[entry(1, '600519', '贵州茅台', 5.4)]} onSelect={onSelect} />)
    screen.getByText('贵州茅台').closest('tr')?.click()
    expect(onSelect).toHaveBeenCalledWith('600519')
  })

  it('可以隐藏上榜次数列', () => {
    render(<RankTable entries={[entry(1, '600519', '贵州茅台', 5.4)]} showAppearCount={false} />)
    expect(screen.queryByText('上榜')).toBeNull()
  })
})

describe('ValueTable', () => {
  it('渲染数值表', () => {
    render(
      <ValueTable
        rows={[
          {
            key: 'BK0447',
            name: '通信设备',
            code: 'BK0447',
            value: '15.00亿',
            pct: 2.1,
            extra: '中兴通讯',
          },
        ]}
      />,
    )
    expect(screen.getByText('通信设备')).toBeTruthy()
    expect(screen.getByText('15.00亿')).toBeTruthy()
    expect(screen.getByText('中兴通讯')).toBeTruthy()
  })
})

describe('StatusBadge（第 5.18 章）', () => {
  it('展示状态、来源与更新时间', () => {
    render(
      <StatusBadge
        status="CACHE"
        statusLabel="缓存"
        sourceLabel="腾讯财经"
        updatedAt="09:45:30"
        message="数据源连接异常，正在使用缓存数据"
      />,
    )
    expect(screen.getByText('缓存')).toBeTruthy()
    expect(screen.getByText(/腾讯财经/)).toBeTruthy()
    expect(screen.getByText(/最后更新 09:45:30/)).toBeTruthy()
    expect(screen.getByText(/正在使用缓存数据/)).toBeTruthy()
  })

  it('未知状态回退为原始字符串而不是空', () => {
    render(<StatusBadge status="SOMETHING_NEW" />)
    expect(screen.getByText('SOMETHING_NEW')).toBeTruthy()
  })
})

describe('StateBox', () => {
  it('同时展示标题与原因说明', () => {
    render(<StateBox icon="!" title="数据源连接异常" detail="正在使用 09:45 缓存数据" />)
    expect(screen.getByText('数据源连接异常')).toBeTruthy()
    expect(screen.getByText('正在使用 09:45 缓存数据')).toBeTruthy()
  })
})
