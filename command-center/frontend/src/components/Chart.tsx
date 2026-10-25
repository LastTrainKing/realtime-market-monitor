import * as echarts from 'echarts'
import { useEffect, useRef } from 'react'

import styles from './Chart.module.css'

interface Props {
  option: echarts.EChartsOption
  height?: number | string
  /** 增量更新：禁止每次 destroy 重建（第 5.17 章） */
  notMerge?: boolean
  loading?: boolean
  overlay?: string
  onReady?: (chart: echarts.ECharts) => void
}

const THEME_TEXT = '#9aa9c4'
const THEME_GRID = 'rgba(120,160,230,0.12)'

export const chartBase = {
  textStyle: {
    color: THEME_TEXT,
    fontFamily: "'PingFang SC','Microsoft YaHei UI',system-ui,sans-serif",
  },
  grid: { left: 54, right: 22, top: 26, bottom: 30 },
  axisLine: { lineStyle: { color: THEME_GRID } },
  splitLine: { lineStyle: { color: THEME_GRID, type: 'dashed' as const } },
  axisLabel: { color: THEME_TEXT, fontSize: 11 },
}

/**
 * ECharts 封装。
 * 使用增量更新（setOption 合并模式），避免每次刷新销毁重建图表。
 */
export function Chart({ option, height = '100%', notMerge = false, loading, overlay, onReady }: Props) {
  const ref = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)
  const roRef = useRef<ResizeObserver | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    onReady?.(chart)

    roRef.current = new ResizeObserver(() => {
      chart.resize()
    })
    roRef.current.observe(ref.current)

    return () => {
      roRef.current?.disconnect()
      roRef.current = null
      chart.dispose()
      chartRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (loading) {
      chart.showLoading('default', {
        text: '',
        color: '#4fa3ff',
        textColor: THEME_TEXT,
        maskColor: 'rgba(6,10,20,0.35)',
        zlevel: 0,
      })
    } else {
      chart.hideLoading()
    }
  }, [loading])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.setOption(option, { notMerge, lazyUpdate: true })
  }, [option, notMerge])

  return (
    <div className={styles.wrap} style={{ height }}>
      <div ref={ref} className={styles.canvas} />
      {overlay ? <div className={styles.overlay}>{overlay}</div> : null}
    </div>
  )
}

export default Chart
