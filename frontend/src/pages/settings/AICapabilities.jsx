// AI 能力总览 —— 这一页只回答一个问题：**平台上哪些地方会调 AI，各自用的哪个模型。**
//
// 原来它是一份手写的 Phase 路线图，说了好几件已经不成立的事：把摘掉的「AI 生成脚本」
// 按钮写成可用、把 MCP 工具写死成 8 个（实际 37）、指着一个不存在的「AI 诊断」按钮、
// 把已经能用的探索测试写成"规划中"。用户的原话是"我要求非常的清晰，目前有点太乱了"。
//
// 现在整页的数据只有一个来源：后端 CAPABILITY_REGISTRY + 档位绑定（GET /api/ai-capabilities）。
// 后端加一个 AI 调用点，这页自己就多一行；下线一个，这页自己就挪到"已下线"。手写清单
// 和真相分家这件事不会再发生。
import { useEffect, useState } from 'react'
import { Card, Tag, Space, Typography, Table, Spin, Alert, Tooltip, Select, message } from 'antd'
import { RobotOutlined, ApiOutlined } from '@ant-design/icons'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../utils/request'

const { Text } = Typography

// 谁在执行 —— 平台侧 LLM，还是外部 Claude Code。边界见 docs/cc-platform-loop-spec.md
const RUNNER = {
  platform: { label: '平台执行', color: 'cyan' },
  cc: { label: 'Claude Code 执行', color: 'blue' },
}

// 页头统计带里的一格：大数 + 单位 + 灰字标签。
// 这么摊开而不是用 antd Statistic —— 全站 48 个页面没有一处用 Statistic，
// 一张 109px 高的卡片只为了放一个个位数（国际化词典那页也是这么换掉的）。
function Stat({ n, unit, label, muted }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 5 }}>
      <b style={{ fontSize: 22, fontWeight: 700, color: muted ? '#4e5969' : '#0ea5a0' }}>{n}</b>
      <span style={{ fontSize: 13, color: '#8c919e' }}>{unit} {label}</span>
    </span>
  )
}

export default function AICapabilities() {
  const { projectId } = useParams()
  const [data, setData] = useState(null)
  const [toolCount, setToolCount] = useState(null)
  const [loading, setLoading] = useState(true)

  const [usage, setUsage] = useState(null)
  const [models, setModels] = useState([])
  const [savingKey, setSavingKey] = useState(null)

  const reloadUsage = async () => {
    try { setUsage((await api.get('/ai-capabilities/usage')).data) } catch { /* */ }
  }

  // 给某一个入口单独指定模型（空 = 取消，回到跟着档位走）
  const setCapabilityModel = async (key, model) => {
    setSavingKey(key)
    try {
      await api.put('/ai-capabilities/capability-model', { key, model: model || null })
      message.success(model ? `已指定为 ${model}` : '已改回跟着档位')
      await reloadUsage()
    } catch { /* request.js 已提示 */ } finally { setSavingKey(null) }
  }

  useEffect(() => {
    Promise.all([
      api.get('/ai-capabilities').then(r => r.data).catch(() => null),
      api.get('/mcp-keys/tools').then(r => (r.data || []).length).catch(() => null),
      // 真实用量。**这一列是这一页最重要的东西**：用户看着旧版这一页得出的结论是
      // 「系统里用到 AI 的好像只有 AI 审核吧」，而库里场景生成有 111 条调用记录。
      // 只列"配了什么"回答不了"用了什么"，人就只能猜，猜完照着猜的结论砍功能。
      api.get('/ai-capabilities/usage').then(r => r.data).catch(() => null),
      api.get('/ai-capabilities/models').then(r => (r.data?.models || []).map(m => typeof m === 'string' ? m : m.id)).catch(() => []),
    ]).then(([caps, n, u, ms]) => {
      setData(caps)
      setToolCount(n)
      setUsage(u)
      setModels(ms || [])
    }).finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
  if (!data) return <Alert type="error" message="拿不到 AI 能力清单，检查后端是否在 8756 端口" />

  const bindings = data.bindings || []
  const registry = data.registry || []
  const modelOf = (category) => bindings.find(b => b.key === category)?.model || '—'
  const labelOf = (category) => bindings.find(b => b.key === category)?.label || category

  // 这一页只渲染**还点得到的入口**。已下线/已封存的那些不再列出来 ——
  // 它们不是这页要回答的问题（「平台哪些地方会调 AI，各用哪个模型」），
  // 混在同一页上就是十几行划掉的字，把八行真数据顶下去。
  //
  // 下线理由没有丢，也不该丢：它是 backend/app/services/ai_capabilities.py 里
  // CAPABILITY_REGISTRY 每条的 deprecated / deprecatedNote 字段（那个文件顶上
  // 也写了「不要删 key」的原因）。要看有哪些、为什么，跑：
  //   grep -n 'deprecatedNote' backend/app/services/ai_capabilities.py
  const live = registry.filter(c => !c.deprecated)

  const usageOf = (key) => (usage?.items || []).find(i => i.key === key)
  const items = usage?.items || []
  const usedCount = items.filter(i => i.calls > 0).length
  const totalCalls = items.reduce((a, i) => a + i.calls, 0)
  // 「这一行自己指定了模型」的个数 —— 表里每行都能改，但改过几行只有逐行看才知道
  const pinnedCount = items.filter(i => i.ownModel).length
  const fmtDay = (iso) => (iso ? String(iso).slice(5, 10) : '')

  const columns = [
    {
      title: '能力', dataIndex: 'label', width: 210,
      render: (v, r) => (
        <div>
          <div style={{ fontWeight: 500 }}>{v}</div>
          <Text code style={{ fontSize: 11 }}>{r.key}</Text>
        </div>
      ),
    },
    { title: '在哪用', dataIndex: 'where', width: 210, render: v => <span style={{ color: '#4e5969' }}>{v}</span> },
    {
      title: '走哪个档位', dataIndex: 'category', width: 140,
      render: v => <Tag>{labelOf(v)}</Tag>,
    },
    {
      // 每一行都能单独换模型。原来只能按"档位"配（文本 / UI 脚本两档），
      // 想让文档生成用便宜的、评审用强的，得去「新增自定义档位」建档再勾模块 ——
      // 三步操作、两个新概念，而人要的只是"这一行换个模型"。
      title: (
        <Tooltip title="每个入口都可以单独指定模型；不指定就跟着档位走（档位在 AI 服务配置页改）。">
          <span style={{ borderBottom: '1px dotted #c9cdd4' }}>用哪个模型（可改）</span>
        </Tooltip>
      ),
      dataIndex: 'key', key: 'model', width: 250,
      render: (key, r) => {
        const u = usageOf(key)
        const own = u?.ownModel || null
        return (
          <div>
            <Select
              size="small"
              style={{ width: 228 }}
              value={own || ''}
              loading={savingKey === key}
              disabled={savingKey === key}
              onChange={(v) => setCapabilityModel(key, v)}
              options={[
                { value: '', label: `跟着档位（${modelOf(r.category)}）` },
                ...models.map(m => ({ value: m, label: m })),
              ]}
            />
            {own && (
              <div style={{ fontSize: 11, color: '#0ea5a0', marginTop: 2 }}>这一项单独指定</div>
            )}
          </div>
        )
      },
    },
    {
      title: (
        <Tooltip title="按调用记录数的真实用量。「没记录」和「没用过」是两件事，这一列分开写 —— 混着说会误删还在用的功能。">
          <span style={{ borderBottom: '1px dotted #c9cdd4' }}>真实用量</span>
        </Tooltip>
      ),
      dataIndex: 'key', key: 'usage', width: 200,
      render: (key) => {
        const u = usageOf(key)
        if (!u) return <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
        if (u.calls > 0) return (
          <span style={{ fontSize: 12 }}>
            <b style={{ color: '#0ea5a0' }}>{u.calls}</b> 次
            <span style={{ color: '#86909c', marginLeft: 8 }}>最近 {fmtDay(u.lastUsedAt)}</span>
          </span>
        )
        // 0 次分两种，说法必须不一样
        return u.meteredSince ? (
          <Tooltip title={`这条链路从 ${u.meteredSince} 才开始记调用（之前压根没写记录），所以"0 次"只代表这之后没人用过，不代表功能没用过。`}>
            <span style={{ fontSize: 12, color: '#d48806', borderBottom: '1px dotted currentColor' }}>
              暂无记录（{u.meteredSince} 起记）
            </span>
          </Tooltip>
        ) : <span style={{ fontSize: 12, color: '#c9cdd4' }}>从未调用</span>
      },
    },
  ]

  return (
    <div>
      {/* 页头按全站一套：h2 18px + 一行 13px 灰字。原来这儿是一段五行的散文
          （「这 N 处入口现在都点得到…其中真的被调用过的有 M 处…另有 K 处已下线…
          已下线的那几处历史上跑过…」），四个数混在从句里，得读完整段才知道
          哪个数是哪个。数字挪进下面那条统计带，一眼一个。 */}
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>
          <RobotOutlined style={{ marginRight: 8 }} />
          AI 能力总览
        </h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>
          平台上会调 AI 的地方全在下面 —— 走哪个入口、用哪个模型、真跑过几次。
          每一行的模型都能单独改；档位本身去
          {' '}<Link to="/settings/ai-providers">AI 服务配置 → AI 能力→模型</Link>。
        </span>
      </div>

      {/* 统计带跟「国际化词典」那张卡同一个写法（大数 + 灰字，一行摊开）——
          全站只有那儿和这儿有这种"页头几个读数"，样式对齐了才不像两套系统。 */}
      <Card size="small" style={{ marginBottom: 16, background: 'rgba(14,165,160,0.035)' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 26, flexWrap: 'wrap' }}>
          <Stat n={live.length} unit="处" label="入口（都点得到）" />
          {usage ? (
            <>
              {/* 「配了什么」回答不了「用了什么」。这两个数是这页最该被先看到的 ——
                  少了它们，人只能凭印象猜哪个功能没人用，然后照着猜的结论砍。 */}
              <Stat n={usedCount} unit="处" label="真的被调用过" />
              <Stat n={totalCalls} unit="次" label="累计调用" />
              {pinnedCount > 0 && <Stat n={pinnedCount} unit="处" label="单独指定了模型" muted />}
            </>
          ) : (
            <span style={{ fontSize: 13, color: '#d48806' }}>
              用量取不到（/ai-capabilities/usage 没返回）—— 下面「真实用量」那列会是空的
            </span>
          )}
        </div>
      </Card>

      {/* 边界：哪些活是平台干的，哪些活平台不干。
          这张和最底下那张 MCP 卡都是**说明**，所以都走灰底；青底只留给上面那条读数带 ——
          原来它俩都是青底，前后一叠像同一块内容被切成了两半。 */}
      <Card size="small" style={{ marginBottom: 16, background: 'rgba(0,0,0,0.02)' }}>
        <div style={{ fontSize: 13, lineHeight: 2 }}>
          <Tag color={RUNNER.platform.color}>{RUNNER.platform.label}</Tag>
          从文档、用例这类<b>文本</b>产出文本：生成用例、生成接口场景、写文档、评审。
          <br />
          <Tag color={RUNNER.cc.color}>{RUNNER.cc.label}</Tag>
          需要<b>真的把系统跑一遍</b>才算数的活：写 UI 脚本、跑通它、分析失败原因。
          平台在这条链上只做两件事 —— 出证据（截图/请求/按规则算的失败现象）、存结论（人确认的原因）。
          <span style={{ color: '#86909c' }}>　详见 docs/cc-platform-loop-spec.md</span>
        </div>
      </Card>

      <Table
        rowKey="key"
        size="small"
        columns={columns}
        dataSource={live}
        pagination={false}
        style={{ marginBottom: 20 }}
      />

      <Card size="small" style={{ background: 'rgba(0,0,0,0.02)' }}>
        <div style={{ fontSize: 13, lineHeight: 2 }}>
          <Space><ApiOutlined /><b>MCP 工具</b></Space>
          <div>
            外部 Claude Code 通过 MCP 读写平台数据。地址{' '}
            <Text code>{`http://${window.location.hostname}:18800/mcp/`}</Text>
            {toolCount != null && <>，当前 <b>{toolCount}</b> 个工具。</>}
          </div>
          <div>
            完整目录、按活分类、Key 的工具范围，都在
            {' '}<Link to={`/projects/${projectId}/settings/mcp-tools`}>MCP 工具中心</Link>
            {' '}—— 这里不再抄一份，抄的那份迟早和真相不一样。
          </div>
        </div>
      </Card>
    </div>
  )
}
