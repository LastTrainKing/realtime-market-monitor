import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
  info: string
}

/**
 * 全局错误边界（生产必需）。
 *
 * 任何渲染异常都必须以可读的方式呈现给用户，绝不允许出现黑屏。
 * 同时把错误上报到本地后端日志，便于技术支持定位。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const detail = `${error.message}\n${error.stack ?? ''}\n${info.componentStack ?? ''}`
    this.setState({ info: detail })
    void reportToBackend(detail)
  }

  render() {
    const { error, info } = this.state
    if (!error) return this.props.children

    return (
      <div
        style={{
          position: 'fixed',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 16,
          padding: 40,
          background: '#05070f',
          color: '#eaf1ff',
          fontFamily: '"PingFang SC","Microsoft YaHei UI",system-ui,sans-serif',
          textAlign: 'center',
          overflow: 'auto',
        }}
      >
        <div style={{ fontSize: 22, letterSpacing: '0.08em' }}>界面加载遇到问题</div>
        <div style={{ fontSize: 15, color: '#ffc4ca', lineHeight: 1.9, maxWidth: 760 }}>
          {error.message || '未知错误'}
        </div>
        <pre
          style={{
            fontSize: 12,
            color: '#9aa9c4',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
            maxWidth: 900,
            textAlign: 'left',
            maxHeight: '45vh',
            overflow: 'auto',
          }}
        >
          {info}
        </pre>
        <button
          onClick={() => window.location.reload()}
          style={{
            padding: '10px 26px',
            borderRadius: 11,
            border: '1px solid rgba(140,190,255,0.45)',
            background: 'rgba(79,163,255,0.16)',
            color: '#d8e9ff',
            fontSize: 15,
          }}
        >
          重新加载
        </button>
        <div style={{ fontSize: 12.5, color: '#66738c' }}>
          错误详情已写入本机日志（%APPDATA%\CapitalFlowCenter\logs），可截图反馈
        </div>
      </div>
    )
  }
}

/** 把前端异常写入后端日志，便于离线排查 */
async function reportToBackend(detail: string) {
  try {
    const { api } = await import('../api/client')
    await api.reportClientError(detail)
  } catch {
    /* 后端不可用时忽略 */
  }
}

export default ErrorBoundary
