interface Props {
  icon?: string
  title: string
  detail?: string
  action?: React.ReactNode
}

/**
 * 状态占位（第 5.18 / 8.16 章）。
 * 必须区分“数据未到 / 接口失败 / 历史缺失 / 缓存缺失 / 计算错误 / 数据源限制”，
 * 禁止统一显示“暂无数据”。
 */
export function StateBox({ icon = '⏳', title, detail, action }: Props) {
  return (
    <div className="state-box">
      <span className="icon">{icon}</span>
      <span className="title">{title}</span>
      {detail ? <span className="detail">{detail}</span> : null}
      {action}
    </div>
  )
}

export default StateBox
