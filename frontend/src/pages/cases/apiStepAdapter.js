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
const OP_IN = { '==': 'eq', '!=': 'ne', '>': 'gt', '<': 'lt', contains: 'contains', not_contains: 'contains', not_empty: 'notEmpty', in: 'eq' }
const OP_OUT = { eq: '==', ne: '!=', gt: '>', lt: '<', contains: 'contains', notEmpty: 'not_empty' }

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
      operator: OP_IN[a.operator] || 'eq',
      // status 用 value，body_field 用 expected —— 两种都收
      expected: String(a.expected ?? a.value ?? ''),
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
      if (type === 'status') a.value = /^\d+$/.test(op.expected || '') ? Number(op.expected) : op.expected
      else if (type === 'body_contains') a.value = op.expected
      else { a.field = op.path || ''; a.expected = op.expected }
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
    name: node.action || '',
    method: node.method || 'GET',
    url: node.url || '',
    headers: Object.keys(headers).length ? headers : null,
    body,
    assertions,
    variablesExtract,
    enabled: node.enabled !== false,
  }
}

export const scenarioToNodes = (steps) => (steps || []).map(stepToNode)
