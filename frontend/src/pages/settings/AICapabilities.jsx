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
import { Card, Tag, Space, Typography, Table, Spin, Alert } from 'antd'
import { RobotOutlined, ApiOutlined } from '@ant-design/icons'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../utils/request'

const { Text } = Typography

// 谁在执行 —— 平台侧 LLM，还是外部 Claude Code。边界见 docs/cc-platform-loop-spec.md
const RUNNER = {
  platform: { label: '平台执行', color: 'cyan' },
  cc: { label: 'Claude Code 执行', color: 'blue' },
}

export default function AICapabilities() {
  const { projectId } = useParams()
  const [data, setData] = useState(null)
  const [toolCount, setToolCount] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      api.get('/ai-capabilities').then(r => r.data).catch(() => null),
      api.get('/mcp-keys/tools').then(r => (r.data || []).length).catch(() => null),
    ]).then(([caps, n]) => {
      setData(caps)
      setToolCount(n)
    }).finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
  if (!data) return <Alert type="error" message="拿不到 AI 能力清单，检查后端是否在 8756 端口" />

  const bindings = data.bindings || []
  const registry = data.registry || []
  const modelOf = (category) => bindings.find(b => b.key === category)?.model || '—'
  const labelOf = (category) => bindings.find(b => b.key === category)?.label || category

  const live = registry.filter(c => !c.deprecated)
  const gone = registry.filter(c => c.deprecated)

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
      title: '当前模型', dataIndex: 'category', key: 'model', width: 190,
      render: v => <Text code style={{ fontSize: 12 }}>{modelOf(v)}</Text>,
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>
          <RobotOutlined style={{ marginRight: 8 }} />
          AI 能力总览
        </h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>
          平台上会调 AI 的地方，一共 {live.length} 处，全在下面。改模型去
          {' '}<Link to="/settings/ai-providers">AI 服务配置 → AI 能力→模型</Link>。
        </span>
      </div>

      {/* 边界：哪些活是平台干的，哪些活平台不干 */}
      <Card size="small" style={{ marginBottom: 16, background: 'rgba(14,165,160,0.04)', border: '1px solid rgba(14,165,160,0.18)' }}>
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

      {gone.length > 0 && (
        <>
          <div style={{ fontSize: 13, color: '#86909c', margin: '0 0 8px' }}>
            已下线 / 已封存（留在这里是为了说清楚为什么不做了，免得过阵子又被加回来）
          </div>
          <Table
            rowKey="key"
            size="small"
            showHeader={false}
            pagination={false}
            style={{ marginBottom: 20, opacity: 0.75 }}
            columns={[
              // key 是不能断的标识符，和标题挤在一行会被拦腰折断，所以分两行放
              { dataIndex: 'label', width: 230, render: (v, r) => (
                <div>
                  <div><s>{v}</s></div>
                  <Text code style={{ fontSize: 11, whiteSpace: 'nowrap' }}>{r.key}</Text>
                </div>
              ) },
              { dataIndex: 'deprecatedNote', render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{v || '已下线'}</span> },
            ]}
            dataSource={gone}
          />
        </>
      )}

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
