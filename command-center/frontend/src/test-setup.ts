import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// ---------------------------------------------------------------------------
// 显式注册 DOM 清理（重要）
//
// Testing Library 的"自动清理"依赖它在模块被首次 import 时注册的全局
// `afterEach`。vitest 的 `poolOptions.forks.singleFork = true` 会让多个测试
// 文件共享同一个进程/模块注册表，于是注册只对**第一个**加载该模块的文件生效，
// 其它文件不再清理 —— 表现为"某个文件单独跑能过、一起跑就报
// Found multiple elements with the text: ..."。
//
// 这里在 setupFiles 中显式注册，setupFiles 会按测试文件执行，
// 因此每个文件都能拿到属于自己的清理钩子，与隔离策略无关。
// ---------------------------------------------------------------------------
afterEach(() => {
  cleanup()
})

// jsdom 未实现 ResizeObserver，ECharts 与布局组件需要
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!('ResizeObserver' in globalThis)) {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub
}

// jsdom 未实现 matchMedia
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}
