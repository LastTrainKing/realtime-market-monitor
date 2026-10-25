import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import './styles/global.css'

/** 启动占位层在 React 成功挂载后移除 */
function hideBootPlaceholder() {
  const boot = document.getElementById('boot')
  if (boot) {
    boot.style.transition = 'opacity 0.35s ease'
    boot.style.opacity = '0'
    window.setTimeout(() => boot.remove(), 380)
  }
}

/** 渲染失败时把原因展示给用户，绝不留下黑屏 */
function renderFatal(message: string, detail?: string) {
  const root = document.getElementById('root')
  const boot = document.getElementById('boot')
  if (boot) boot.remove()
  if (!root) return
  root.innerHTML = ''
  const box = document.createElement('div')
  box.setAttribute(
    'style',
    [
      'position:fixed',
      'inset:0',
      'display:flex',
      'flex-direction:column',
      'align-items:center',
      'justify-content:center',
      'gap:16px',
      'padding:40px',
      'background:#05070f',
      'color:#eaf1ff',
      'font-family:"PingFang SC","Microsoft YaHei UI",system-ui,sans-serif',
      'text-align:center',
    ].join(';'),
  )
  const title = document.createElement('div')
  title.setAttribute('style', 'font-size:22px;letter-spacing:0.08em;')
  title.textContent = '资金流向指挥中心 启动失败'
  const msg = document.createElement('div')
  msg.setAttribute('style', 'font-size:15px;color:#ffc4ca;line-height:1.9;max-width:760px')
  msg.textContent = message
  const hint = document.createElement('div')
  hint.setAttribute('style', 'font-size:13px;color:#66738c;line-height:1.8;max-width:760px')
  hint.textContent =
    '请截图反馈给技术支持。数据文件保存在 %APPDATA%\\CapitalFlowCenter，可安全重装覆盖。'
  box.appendChild(title)
  box.appendChild(msg)
  if (detail) {
    const pre = document.createElement('pre')
    pre.setAttribute(
      'style',
      'font-size:12px;color:#9aa9c4;white-space:pre-wrap;word-break:break-all;max-width:900px;text-align:left',
    )
    pre.textContent = detail
    box.appendChild(pre)
  }
  box.appendChild(hint)
  root.appendChild(box)
}

window.addEventListener('error', (e) => {
  if (e.message && !document.getElementById('root')?.childElementCount) {
    renderFatal(e.message, `${e.filename ?? ''}:${e.lineno ?? ''}`)
  }
})

window.addEventListener('unhandledrejection', (e) => {
  const reason = e.reason as Error | undefined
  if (!document.getElementById('root')?.childElementCount) {
    renderFatal(reason?.message ?? String(reason))
  }
})

try {
  const el = document.getElementById('root')
  if (!el) {
    throw new Error('页面结构缺失：未找到 #root 节点')
  }

  createRoot(el).render(
    <StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </StrictMode>,
  )

  window.requestAnimationFrame(() => {
    window.setTimeout(hideBootPlaceholder, 260)
  })
} catch (err) {
  renderFatal((err as Error).message, (err as Error).stack)
}
