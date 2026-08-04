/**
 * 服务清单的前端侧补全。
 *
 * 后端 /api/system/services 列不出「前端 Web 跑在哪个端口」—— 它只看得到自己收到的请求。
 * 但这恰恰是最容易搞混的一项（开发态 vite :5173 代理到 :8756，部署态 nginx :80），
 * 所以在前端用 window.location 补一行进「平台核心」组。
 *
 * 顶栏胶囊和详情页都走这个函数，否则两处的 "N/M" 会对不上（顶栏 15/16、页面 17，人会懵）。
 */

export function frontendRow() {
  return {
    key: 'frontend',
    name: '前端 Web',
    host: window.location.hostname,
    port: Number(window.location.port) || (window.location.protocol === 'https:' ? 443 : 80),
    url: window.location.origin,
    status: 'up',
    probe: 'self',
    kind: '内建',
    desc: '你正在看的这个页面。开发态 vite :5173 代理到后端 :8756，部署态由 nginx 托管',
    manageUrl: null,
    startHint: null,
  }
}

/**
 * 把前端那一行插进「平台核心」组（紧跟后端 API 之后），并按插入后的结果重算 summary。
 * @param {{summary: object, groups: Array}|null} data 后端原始响应
 */
export function withFrontendRow(data) {
  if (!data?.groups) return { summary: null, groups: [] }

  const groups = data.groups.map(g =>
    g.key === 'core'
      ? { ...g, items: [g.items[0], frontendRow(), ...g.items.slice(1)] }
      : g
  )
  const all = groups.flatMap(g => g.items)
  const count = (s) => all.filter(i => i.status === s).length

  return {
    groups,
    summary: {
      total: all.length,
      up: count('up'),
      down: count('down'),
      notConfigured: count('notConfigured'),
    },
  }
}
