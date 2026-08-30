/** 关联 bug 的显示口径 —— 列表页和详情页共用一份，免得两处各写各的。
 *
 * 单号是**原样存的自由文本**（后端 `bug_ref_service.normalize_bug_refs` 只做
 * 去空/去重/长度校验，不解析格式），所以库里同时存在 `#572` 和 `admin#464`。
 * 用户直接问过"有的显示 admin#xx，有的 #xxx，有什么区别" —— 区别不是平台给的，
 * 是登记的人写的：**前缀就是仓库**（`admin#464` 在 Infini/stoa/admin，
 * `#572` 在不带前缀的那个主仓）。光看单号看不出来，所以这里从 url 反推仓库路径，
 * 让「哪个仓的单子」在界面上真的可查，而不是靠人记住一条没写下来的约定。
 */

/** 从 issue 链接反推仓库路径：
 *  http://host/uag/unified-agent-gateway/-/issues/572 → uag/unified-agent-gateway
 *  认不出来返回 null —— 宁可不显示，也别瞎猜一个仓库名出来。 */
export function repoOfBugUrl(url) {
  if (!url) return null
  const gitlab = /^https?:\/\/[^/]+\/(.+?)\/-\/(?:issues|merge_requests)\/\d+/.exec(url)
  if (gitlab) return gitlab[1]
  const github = /^https?:\/\/[^/]+\/([^/]+\/[^/]+)\/(?:issues|pull)\/\d+/.exec(url)
  return github ? github[1] : null
}

export const isOpenBug = (r) => (r?.status || 'open') === 'open'

/** 一条用例的整体态：blocked 卡着 / fixed 抓到过已验回来 / none 没关联。
 *  三态各有各的含义（见 backend/app/services/bug_ref_service.py 开头那张图），
 *  别合并成布尔 —— 「抓到过 bug」是用例的价值证明，不是「没问题」。 */
export function bugRefState(refs) {
  const list = refs || []
  if (!list.length) return 'none'
  return list.some(isOpenBug) ? 'blocked' : 'fixed'
}
