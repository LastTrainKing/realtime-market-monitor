/**
 * 导航栏测试。
 *
 * 重点是**版本号必须可见且来自后端**：
 * 本项目出现过"用户装的是上一版安装包，却在追问新问题"的情况，
 * 而当时界面上根本不显示版本号，用户无从判断自己跑的是哪一版。
 * 这里把"版本号来自后端上报值"锁住，避免以后又被写死或漏显示。
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Navigation } from './Navigation'

function renderNav(version?: string) {
  return render(
    <Navigation
      onToggleFullscreen={vi.fn()}
      isFullscreen={false}
      marketStatusLabel="休市"
      connection="online"
      version={version}
    />,
  )
}

describe('Navigation 版本号', () => {
  it('显示后端上报的版本号', () => {
    renderNav('1.1.0')
    expect(screen.getByText('v1.1.0')).toBeTruthy()
  })

  it('版本号来自传入值，不是写死的', () => {
    renderNav('9.9.9-rc1')
    expect(screen.getByText('v9.9.9-rc1')).toBeTruthy()
    expect(screen.queryByText('v1.1.0')).toBeNull()
  })

  it('拿不到版本时显示占位符，不谎报一个版本', () => {
    renderNav(undefined)
    expect(screen.getByText('v—')).toBeTruthy()
  })
})
