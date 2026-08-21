import { Tooltip } from 'antd'

/**
 * 列表里的「时间」列 —— 全站一套写法。
 *
 * 为什么单独抽出来：走查时量过一遍，时间在各个列表里长得完全不一样 ——
 * 测试报告塞在「报告名称」后面当灰色小字、用例管理默认压根不显示、操作日志用
 * toLocaleString() 带秒占 150px、自动化数据又是另一种格式。列表之间对不上，
 * 而且时间抢了主信息的位置。
 *
 * 统一规则：
 *   - 独立成一列，固定放在「操作」列前面
 *   - 今年的省掉年份（`08-21 14:44`），跨年的显示 `2025-12-30`，鼠标悬浮看完整值
 *   - 等宽字体 + 次要色，宽度 112px（实测 `08-21 14:44` 在 12px JetBrains Mono
 *     下约 82px，加单元格 padding 刚好不换行）
 */

const pad = n => String(n).padStart(2, '0')

export function formatTime(v) {
  if (!v) return '-'
  const d = new Date(v)
  if (isNaN(d.getTime())) return '-'
  const now = new Date()
  if (d.getFullYear() === now.getFullYear()) {
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  }
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function formatTimeFull(v) {
  if (!v) return '-'
  const d = new Date(v)
  if (isNaN(d.getTime())) return '-'
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** 单元格本体，手写表格（测试报告 / 测试计划那种 flex 行）也能直接用 */
export function TimeCell({ value }) {
  if (!value) return <span className="tb-time-cell">-</span>
  return (
    <Tooltip title={formatTimeFull(value)} mouseEnterDelay={0.4}>
      <span className="tb-time-cell">{formatTime(value)}</span>
    </Tooltip>
  )
}

/** antd Table 的列定义。放在 columns 数组里「操作」列的前一个位置。 */
export function timeColumn({
  key = 'updatedAt',
  dataIndex,
  title = '更新时间',
  width = 112,
  ...rest
} = {}) {
  return {
    key,
    title,
    dataIndex: dataIndex || key,
    width,
    ...rest,
    render: (v, row) => <TimeCell value={v ?? row?.[dataIndex || key]} />,
  }
}

/** 表头单元格的公共样式（手写表格用），跟 antd 表头对齐 */
export const TIME_COL_WIDTH = 112
