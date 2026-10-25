import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, getBaseUrlSync, resolveBase, setBaseUrl, setToken } from './client'

describe('后端端口发现', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('可以通过 setBaseUrl 注入地址', async () => {
    setBaseUrl('http://127.0.0.1:47821/')
    expect(getBaseUrlSync()).toBe('http://127.0.0.1:47821')
    await expect(resolveBase()).resolves.toBe('http://127.0.0.1:47821')
  })

  it('未注入地址且无后端时抛出 BACKEND_UNREACHABLE', async () => {
    vi.resetModules()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    const mod = await import('./client')
    await expect(mod.resolveBase()).rejects.toThrow('BACKEND_UNREACHABLE')
  })
})

describe('API 请求', () => {
  it('健康检查返回结构化数据', async () => {
    setBaseUrl('http://127.0.0.1:47821')
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'ok',
          version: '1.0.0',
          service: 'capital-flow-command-center',
          uptime_secs: 12,
          trade_date: '2026-09-30',
          market_status: 'Closed',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const h = await api.health()
    expect(h.status).toBe('ok')
    expect(h.version).toBe('1.0.0')
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:47821/api/health',
      expect.objectContaining({ headers: expect.anything() }),
    )
  })

  it('HTTP 错误转为可读文案', async () => {
    setBaseUrl('http://127.0.0.1:47821')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ ok: false, message: '榜单正在计算，请稍候' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
          }),
        ),
      ),
    )
    await expect(api.boards()).rejects.toBeInstanceOf(ApiError)
    await expect(api.boards()).rejects.toThrow('榜单正在计算，请稍候')
  })

  it('网络异常转为友好提示', async () => {
    setBaseUrl('http://127.0.0.1:47821')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(api.dashboard()).rejects.toThrow('无法连接到本地数据服务')
  })

  it('配置保存使用 PUT', async () => {
    setBaseUrl('http://127.0.0.1:47821')
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: true, message: '配置已保存并即时生效' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const res = await api.saveConfig({} as never)
    expect(res.ok).toBe(true)
    expect(fetchMock.mock.calls[0][1].method).toBe('PUT')
  })

  it('自选股增删使用正确方法', async () => {
    setBaseUrl('http://127.0.0.1:47821')
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ ok: true, message: 'ok' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)
    await api.addWatch('600519', '贵州茅台')
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:47821/api/watchlist')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')

    await api.removeWatch('600519')
    expect(fetchMock.mock.calls[1][1].method).toBe('DELETE')
  })

  it('令牌会随请求发送，且 401 时会刷新令牌重试一次', async () => {
    setBaseUrl('http://127.0.0.1:47821')
    setToken('token-abc')

    let calls = 0
    const fetchMock = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      calls += 1
      const headers = init.headers as Record<string, string>
      if (calls === 1) {
        // 第一次：旧令牌被拒绝
        expect(headers['X-CF-Token']).toBe('token-abc')
        return Promise.resolve(
          new Response(JSON.stringify({ ok: false, message: '本地访问令牌校验失败' }), {
            status: 401,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }
      // 第二次：刷新后的令牌
      expect(headers['X-CF-Token']).toBe('token-new')
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, message: 'ok' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    // 模拟外壳在后端就绪后补注入新令牌
    ;(window as unknown as { __CF_TOKEN__?: string }).__CF_TOKEN__ = 'token-new'
    const res = await api.raw<{ ok: boolean }>('/api/probe')
    expect(res.ok).toBe(true)
    expect(calls).toBe(2)
    delete (window as unknown as { __CF_TOKEN__?: string }).__CF_TOKEN__
  })

  it('空响应体视为异常而不是静默返回空对象', async () => {
    setBaseUrl('http://127.0.0.1:47821')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 200 })))
    await expect(api.raw<Record<string, never>>('/api/whatever')).rejects.toThrow(
      '本地数据服务返回为空',
    )
  })

  it('系统状态接口会自动解包 status 字段', async () => {
    setBaseUrl('http://127.0.0.1:47821')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              status: {
                version: '1.0.0',
                net: { total_requests: 12 },
                db: {},
                cache: {},
                collectors: [],
                notes: [],
              },
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          ),
        ),
      ),
    )
    const s = await api.systemStatus()
    expect(s.net.total_requests).toBe(12)
  })
})
