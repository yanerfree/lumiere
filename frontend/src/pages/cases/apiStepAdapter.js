/**
 * api_test_steps（后端表结构） ↔ ApiStepList（apifox 式编辑器）的字段互转。
 *
 * 存在两套形状的历史原因：编排/回推写的是 api_test_steps 的固定列
 * （name/headers 对象/assertions[]/variables_extract{}），而手动编辑器用的是
 * 富节点结构（action/headers 数组/postOperations[]）。以前两边各存各的、各画各的，
 * 于是"同步过来一套、手动建一套"。现在统一成：**只存 api_test_steps，只用手动那套界面**，
 * 这个文件就是中间的翻译层。
 */

// 断言类型：后端 → 编辑器
const ASSERT_TYPE_IN = { status: 'status', body_field: 'jsonPath', body_contains: 'contains', header: 'header' }
const ASSERT_TYPE_OUT = { status: 'status', jsonPath: 'body_field', contains: 'body_contains', header: 'header' }

// 操作符：后端 → 编辑器
// in 必须原样保留：以前映射成 eq，往返一次就把 in [200,204] 改成 == "200,204"，
// 步骤明明返回 200 却判失败 —— 打开编辑器保存一下就把 CC 写对的断言改坏了。
// not_contains 同理：以前也映射成 contains，往返一次「删后列表里不再出现」就变成
// 「删后列表里还在」—— 断言反了、还判失败，人看半天以为是被测系统的问题。
// 编辑器只要动任何一步，saveNodes 会把**所有**步骤回写一遍，所以一次误映射
// 会波及整条链，不止你改的那一步。
// is_empty / length / >= / <= 同理：漏了映射，人在编辑器里存一次就被兜成 eq，
// 「列表应为空」变成「列表 == 空」，断言悄悄换了意思。
const OP_IN = { '==': 'eq', '!=': 'ne', '>': 'gt', '<': 'lt', '>=': 'gte', '<=': 'lte', contains: 'contains', not_contains: 'notContains', not_empty: 'notEmpty', is_empty: 'isEmpty', not_exists: 'notExists', length: 'length', in: 'in' }
const OP_OUT = { eq: '==', ne: '!=', gt: '>', lt: '<', gte: '>=', lte: '<=', contains: 'contains', notContains: 'not_contains', notEmpty: 'not_empty', isEmpty: 'is_empty', notExists: 'not_exists', length: 'length', in: 'in' }

// 断言没带 operator 时，按类型给默认值（编辑器侧的写法）
const DEFAULT_OP = { status: 'eq', body_field: 'eq', body_contains: 'contains', header: 'eq' }

const toText = (v) => {
  if (v == null) return ''
  return typeof v === 'string' ? v : JSON.stringify(v, null, 2)
}

/** 后端步骤 → 编辑器节点 */
export function stepToNode(st, i) {
  const headers = Object.entries(st.headers || {}).map(([key, value]) => ({ key, value: String(value ?? ''), enabled: true }))
  const postOperations = []
  for (const a of st.assertions || []) {
    postOperations.push({
      type: 'assertion',
      assertType: ASSERT_TYPE_IN[a.type] || a.type,
      path: a.field || '',
      // 兜底必须按断言类型分：body_contains 没写 operator 时是「包含」，
      // 一律兜成 eq 会在回写时变成 ==，而 body_contains 根本不认 == —— 直接假失败。
      operator: OP_IN[a.operator] || DEFAULT_OP[a.type] || 'eq',
      // status 用 value，body_field 用 expected —— 两种都收
      // in 的值是数组，输入框只能放字符串 → 逗号串展示，回写时再拆回数组
      expected: Array.isArray(a.value ?? a.expected)
        ? (a.value ?? a.expected).join(',')
        : String(a.expected ?? a.value ?? ''),
    })
  }
  for (const [variable, path] of Object.entries(st.variablesExtract || st.variables_extract || {})) {
    postOperations.push({ type: 'extractor', variable, path: String(path) })
  }
  return {
    id: st.id,
    nodeType: 'api',
    seq: i + 1,
    action: st.name || '',
    method: st.method || 'GET',
    url: st.url || '',
    params: [],            // 后端没有独立 params 列，query 都在 url 里
    headers,
    body: toText(st.body),
    bodyType: 'json',
    auth: { type: 'none' },
    preOperations: [],
    postOperations,
    groupName: st.groupName || st.group_name || null,
    enabled: st.enabled !== false,
    lastStatus: st.lastStatus || st.last_status || null,
    // 等待/重试：适配器漏一个字段，页面上就是"设了但看不见、一保存还被清零"。
    // 这三个是解决异步下发抢跑假红的，见 api_test_runner.run_step。
    waitMs: st.waitMs ?? st.wait_ms ?? 0,
    retryTimeoutMs: st.retryTimeoutMs ?? st.retry_timeout_ms ?? 0,
    retryIntervalMs: st.retryIntervalMs ?? st.retry_interval_ms ?? 300,
    lastResponse: st.lastResponse || st.last_response || null,
  }
}

/** 编辑器节点 → 后端步骤更新体（只回传后端认识的字段） */
export function nodeToStepPatch(node) {
  const headers = {}
  for (const h of node.headers || []) {
    if (h?.key && h.enabled !== false) headers[h.key] = h.value ?? ''
  }
  const assertions = []
  const variablesExtract = {}
  for (const op of node.postOperations || []) {
    if (op.type === 'assertion') {
      const type = ASSERT_TYPE_OUT[op.assertType] || op.assertType
      const a = { type, operator: OP_OUT[op.operator] || op.operator }
      if (type === 'status') {
        const raw = op.expected || ''
        if (op.operator === 'in') {
          // 拆回数组，数字保持数字类型（执行器按 int 比对状态码）
          a.value = String(raw).split(',').map(v => v.trim()).filter(Boolean)
            .map(v => (/^\d+$/.test(v) ? Number(v) : v))
        } else {
          a.value = /^\d+$/.test(raw) ? Number(raw) : raw
        }
      }
      else if (type === 'body_contains') a.value = op.expected
      else {
        a.field = op.path || ''
        // 输入框里拿到的永远是字符串。**布尔必须还原成布尔** ——
        // 库里本来是 `expected: false`，在编辑器里打开再保存就变成 `"false"`，
        // 而平台故意不做布尔兜底（兜了「期望 true、实际 1」就会算相等，那是假绿），
        // 于是这条断言从此必挂，报错还长得像平台在说胡话（期望 false｜实际 False）。
        // 数字不用管：_scalar_eq 已经按数值比（插值出来必然是字符串）。
        const v = op.expected
        a.expected = v === 'true' ? true : v === 'false' ? false : v
        // 「非空 / 为空」不看期望值，输入框也是隐藏的 —— 别把上一次选别的操作符时
        // 留在 state 里的旧值带进库，报告里会印出「响应字段 data.x 为空 false」这种胡话
        if (['not_empty', 'is_empty', 'not_exists'].includes(a.operator)) delete a.expected
      }
      assertions.push(a)
    } else if (op.type === 'extractor' && op.variable) {
      variablesExtract[op.variable] = op.path || ''
    }
  }
  let body = node.body
  if (typeof body === 'string' && body.trim()) {
    try { body = JSON.parse(body) } catch { /* 保持字符串，后端 JSONB 也能存 */ }
  } else if (!body) body = null

  return {
    // 后端 name 有 min_length=1；编辑器新建的节点 action 是空的，这里兜个默认名，
    // 否则一点「添加」就弹 "name: String should have at least 1 character"
    name: (node.action || '').trim() || `请求${node.seq || ''}`.trim() || '新请求',
    method: node.method || 'GET',
    url: node.url || '',
    headers: Object.keys(headers).length ? headers : null,
    body,
    assertions,
    // 没有提取物就写 null，别写 {} —— 回推进来的是 null，保存一次全都变成 {}，
    // 18 步的场景里 12 步"改动"了，而实际什么都没变；再对比改动就全是噪音
    variablesExtract: Object.keys(variablesExtract).length ? variablesExtract : null,
    waitMs: node.waitMs ?? 0,
    retryTimeoutMs: node.retryTimeoutMs ?? 0,
    retryIntervalMs: node.retryIntervalMs ?? 300,
    enabled: node.enabled !== false,
  }
}

export const scenarioToNodes = (steps) => (steps || []).map(stepToNode)
