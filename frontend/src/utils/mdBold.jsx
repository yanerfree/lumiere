/**
 * 把文本里的 `**加粗**` 渲染成真的加粗。
 *
 * 为什么不用 marked：这些文本是**模型产出的**（评审结论、缺口、打回理由），
 * 走 HTML 注入这条路等于把模型输出当可信 HTML。这里只做一件事 ——
 * 按 `**…**` 切段、返回 React 节点，不碰 innerHTML，天然不可注入。
 *
 * 起因：审核 Tab 和抽审报告上到处是裸星号（「脚本里**一个断言都没有**」），
 * 模型按 markdown 写、页面按纯文本渲染，两边对不上。
 */
export default function mdBold(text) {
  if (text == null) return null
  const s = String(text)
  if (!s.includes('**')) return s
  const parts = s.split(/\*\*(.+?)\*\*/gs)
  // split 带捕获组：偶数位是普通文本，奇数位是要加粗的内容
  return parts.map((seg, i) => (i % 2 ? <b key={i}>{seg}</b> : seg))
}
