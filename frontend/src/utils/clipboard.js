import { message } from 'antd'

/**
 * 复制文本到剪贴板 — 兼容 HTTP（非安全上下文）
 *
 * 平台平时是用局域网 IP 走 http 访问的，那是**非安全上下文**：`navigator.clipboard`
 * 整个对象都不存在（实测 `window.isSecureContext === false` / `typeof
 * navigator.clipboard === 'undefined'`）。所以降级路不是备胎，是日常主路。
 *
 * 降级路踩过一个比报错更坏的坑：**报成功，剪贴板是空的。**
 * 老写法把 textarea 挂到 `document.body` 上再 `focus()`。可 antd 的 Drawer/Modal 有
 * 焦点陷阱，会把跑到对话框外面的焦点立刻抢回去 —— 于是 `focus()` 白调，
 * `execCommand('copy')` 在空选区上照样返回 **true**，调用方看到 resolve 就弹「已复制」，
 * 人贴出来是空的。2026-08-31 实测（QA 对账的 AI 评审抽屉，局域网 IP + http）：
 *   挂 document.body → execCommand 返回 true、activeElement 是 BUTTON、粘出 0 字节
 *   挂当前对话框内   → 返回 true、activeElement 是 TEXTAREA、粘出全文
 * 全站 90 来处复制按钮里有相当一批就在抽屉/弹窗里（ApiMock 日志、McpMock、LlmMock、
 * ApiStepList…），所以这条修在这一个函数里，不在各个调用点。
 *
 * 现在两道机制并用，各自能盖住对方失效的情形：
 *   1. 临时接一个 copy 事件监听，自己往 clipboardData 里塞内容 —— 内容对不对
 *      **不再取决于焦点和选区落在哪**。
 *   2. textarea 挂到「当前焦点所在的那个 dialog」里 —— 靠的是 `[role="dialog"]`
 *      这个 ARIA 契约，不是 antd 的类名（antd 6 里 `.ant-drawer-content` 已经没了，
 *      换成了 `.ant-drawer-section`，拿类名做判据下次升级还得再踩一遍）。
 *
 * 还有一条口径：**成功要拿得出证据，拿不出就报失败。** `execCommand` 的返回值不算
 * 证据（上面那组实测就是 true + 空）。判成功只认两种：copy 事件真的进了我们的监听
 * （内容是我们塞的），或者 execCommand 之前焦点确实还在我们那个 textarea 上（选区
 * 是我们的）。都不成立就算失败 —— 这比弹一句「已复制」然后让人贴出空白好得多。
 *
 * 失败提示由**这个函数自己**弹，不指望调用方。理由是数出来的：全站 73 处调用点里
 * 48 处压根不接返回值（`{ copyToClipboard(x); message.success('已复制') }`）、
 * 23 处只写了 `.then`、只有 2 处写了 `.catch`。所以「reject 了让调用方去说」这条
 * 契约在这个代码库里是**不成立的**：真按那样写，71 处在失败时是一句提示都没有，
 * 外加一条没人接的 unhandled rejection。
 *
 * 同时这个 reject 是**预先接掉**的（见下方 `p.catch(() => {})`）：
 *   - 不接返回值的调用点：不会冒 unhandled rejection；
 *   - 写了 `.then(成功提示)` 的：`.then` 不执行，于是**假的「已复制」被压掉了**，
 *     用户看到的是这里弹的真话 —— 这正是要的效果；
 *   - 写了 `.catch` 的（5 处）：错误上带 `reported: true`，它们据此跳过自己那句，
 *     所以一次失败**只弹一条**。别去掉这个标记 —— 少了它，QA 对账那处会把
 *     `e.message` 原样回显给用户，实测弹出来是一句生的英文 `copy failed`。
 *     那几处的 catch 不能整个删掉：它们的 try 里还包着取全文之类会真失败的一步。
 */
export function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text)
  }

  let handled = false
  const onCopy = (e) => {
    handled = true
    e.clipboardData.setData('text/plain', text)
    e.preventDefault()
  }
  document.addEventListener('copy', onCopy)

  const active = document.activeElement
  const host = (active && active.closest && active.closest('[role="dialog"]')) || document.body

  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.cssText = 'position:fixed;left:-9999px;top:-9999px;opacity:0'
  host.appendChild(ta)
  ta.focus()
  ta.setSelectionRange(0, ta.value.length)
  // 焦点被抢是**同步**发生的（对话框的 focusin 处理器），所以这里读到的就是真相
  const keptFocus = document.activeElement === ta
  let ok = false
  try { ok = document.execCommand('copy') } catch { /* */ }
  if (ta.parentNode) ta.parentNode.removeChild(ta)
  document.removeEventListener('copy', onCopy)

  if (handled || (ok && keptFocus)) return Promise.resolve()

  message.error('复制失败，请手动选中内容后按 Ctrl+C')
  const err = new Error('复制失败')
  err.reported = true  // 已经跟用户说过了 —— 处理方别再说一遍（见文件头注释最后一段）
  const p = Promise.reject(err)
  p.catch(() => {})  // 标记为已处理：调用方不接也不会冒 unhandled rejection
  return p
}
