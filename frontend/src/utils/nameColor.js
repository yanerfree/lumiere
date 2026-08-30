/**
 * 按名字取一个稳定的颜色 —— 用户头像、项目色点共用一套。
 *
 * 为什么不随机、也不按行号：颜色得是**这个人的**属性，不是这一屏的属性。
 * 按行号取色的话，翻页、排序、删掉一行，同一个人就换一种颜色 ——
 * 那就不再是「认人的抓手」，只是花花绿绿而已。所以用用户名做哈希：
 * 同一个人在任何页面、任何位置都是同一个色。
 *
 * 色板从 styles/global.css 那套主色里挑，都压到同一档明度/饱和度，
 * 免得一排头像里有的跳出来、有的糊在背景里。
 */

const PALETTE = [
  { color: '#0ea5a0', bg: 'rgba(14, 165, 160, 0.16)', border: 'rgba(14, 165, 160, 0.32)' },   // 主色青
  { color: '#7c5cbf', bg: 'rgba(124, 92, 191, 0.16)', border: 'rgba(124, 92, 191, 0.32)' },   // 紫
  { color: '#4e8af0', bg: 'rgba(78, 138, 240, 0.16)', border: 'rgba(78, 138, 240, 0.32)' },   // 蓝
  { color: '#e06c9f', bg: 'rgba(224, 108, 159, 0.16)', border: 'rgba(224, 108, 159, 0.32)' }, // 粉
  { color: '#d98324', bg: 'rgba(217, 131, 36, 0.16)', border: 'rgba(217, 131, 36, 0.32)' },   // 琥珀
  { color: '#3aa675', bg: 'rgba(58, 166, 117, 0.16)', border: 'rgba(58, 166, 117, 0.32)' },   // 绿
  { color: '#5b8def', bg: 'rgba(91, 141, 239, 0.16)', border: 'rgba(91, 141, 239, 0.32)' },   // 靛
  { color: '#c96a5b', bg: 'rgba(201, 106, 91, 0.16)', border: 'rgba(201, 106, 91, 0.32)' },   // 陶红
  { color: '#2f9fb5', bg: 'rgba(47, 159, 181, 0.16)', border: 'rgba(47, 159, 181, 0.32)' },   // 湖蓝
  { color: '#8a7ec8', bg: 'rgba(138, 126, 200, 0.16)', border: 'rgba(138, 126, 200, 0.32)' }, // 雾紫
]

/**
 * djb2 哈希。用它而不是 `name.length % N` 或首字母取模：
 * 首字母取模在这个库里会直接塌掉 —— testuser007/008/009/012/014 全是 't'，
 * 一整屏同色，等于没上色。
 */
function hashCode(str) {
  let h = 5381
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) + h + str.charCodeAt(i)) | 0  // h * 33 + c，| 0 保持 32 位
  }
  return Math.abs(h)
}

/** 取该名字对应的配色 { color, bg, border } —— 用户头像、项目色点都走它 */
export function nameColor(name) {
  if (!name) return PALETTE[0]
  return PALETTE[hashCode(String(name)) % PALETTE.length]
}

/** 头像里显示的字：取首个字符大写（中文名直接用那个字） */
export function avatarText(name) {
  const s = String(name || '?').trim()
  return s ? s[0].toUpperCase() : '?'
}
