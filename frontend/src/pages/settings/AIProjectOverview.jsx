import { Card, Table, Tag, Tooltip, Typography, Space, Button } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'

const { Text } = Typography

// 四种生效来源 → 颜色 + 人话标签。一眼看出谁在吃兜底、谁没配。
const KIND_META = {
  project_selected: { color: 'blue', label: '项目自选', tip: '项目在自己的设置里选了一个系统配置' },
  project_custom: { color: 'purple', label: '项目自建', tip: '项目自建的专属 AI 连接（此处只读，改动请去项目设置）' },
  system_default: { color: 'orange', label: '全局兜底', tip: '项目没单独配置，吃全局默认兜底' },
  env: { color: 'gold', label: '.env 兜底', tip: '没有系统默认配置，落到 .env 的 AI_BASE_URL/AI_MODEL' },
  none: { color: 'red', label: '未配置', tip: '解析不到可用配置：该项目调用 AI 会直接报「未配置」' },
}

const modelOf = (row, category) =>
  (row.models || []).find(m => m.category === category)?.model || null

export default function AIProjectOverview({ overview, loading, onReload }) {
  const rows = overview?.projects || []
  const customCount = overview?.customBindingCount || 0
  // 「单入口覆盖」和「自定义档位」是两件事：前者是"这一行换个模型"，
  // 说成档位会让人去找一张并不存在的卡片。
  const overrides = overview?.perCapabilityOverrides || []
  // 表头动态跟随后端返回的内置档位，避免前端写死 text/ui_script
  const categories = (overview?.fallback?.resolved || []).map(r => ({ key: r.category, label: r.label }))

  const columns = [
    {
      title: '项目',
      dataIndex: 'projectName',
      render: (v) => <span style={{ fontWeight: 500 }}>{v}</span>,
    },
    {
      title: '生效来源',
      dataIndex: 'configKind',
      width: 120,
      render: (kind) => {
        const meta = KIND_META[kind] || KIND_META.none
        return <Tooltip title={meta.tip}><Tag color={meta.color}>{meta.label}</Tag></Tooltip>
      },
    },
    {
      title: '连接',
      dataIndex: 'connectionName',
      render: (name, r) => {
        if (!name) return <Text type="secondary">—</Text>
        return (
          <Tooltip title={`${r.provider || '-'} · ${r.baseUrlMasked || '-'}`}>
            <span>{name}</span>
          </Tooltip>
        )
      },
    },
    ...categories.map(c => ({
      title: (
        <Tooltip title={c.key === 'ui_script'
          ? 'UI 脚本模型由全局档位统一覆盖 —— UI 生成必须强模型，项目级没有单独选择的概念'
          : '项目自选/自建时，文本模型尊重项目自己选的连接；否则吃全局默认'}>
          <span style={{ borderBottom: '1px dotted #c9cdd4' }}>{c.label}模型</span>
        </Tooltip>
      ),
      key: c.key,
      render: (_, r) => {
        const m = modelOf(r, c.key)
        return m ? <Tag>{m}</Tag> : <Text type="secondary">—</Text>
      },
    })),
  ]

  return (
    <Card
      size="small"
      style={{ marginTop: 20 }}
      title={<span style={{ fontWeight: 600 }}>📊 项目 → AI 使用总览</span>}
      extra={
        <Space>
          <Text type="secondary" style={{ fontSize: 12 }}>只读 · 数据来自实际解析结果</Text>
          <Button size="small" icon={<ReloadOutlined />} onClick={onReload}>刷新</Button>
        </Space>
      }
    >
      {/* 每一行都是后端真实解析出来的结果（与实际调用同一套逻辑），细节挪进
          表头 Tooltip —— 这里只留一句话，另外两条只在真有的时候才提一句。 */}
      <div style={{ fontSize: 12, color: '#86909c', marginBottom: 10 }}>
        每一行都是<b>真实解析</b>结果，不是配置项的复述。
        {customCount > 0 && ` 另有 ${customCount} 个自定义档位按能力覆盖，未在此表展开。`}
        {overrides.length > 0 && ` 另有 ${overrides.length} 处能力单独指定了模型（`
          + overrides.map(o => `${o.label.replace('·专用', '')}→${o.model}`).join('、') + '）。'}
      </div>
      <Table
        rowKey="projectId"
        size="small"
        columns={columns}
        dataSource={rows}
        loading={loading}
        // 一行一个项目，平台上建多少项目这里就有多少行 —— 摊开的话这张卡片
        // 会越用越长，把它下面的东西一直往下推。
        // defaultPageSize 而不是 pageSize：受控又没有 onShowSizeChange 时，
        // 「每页几条」是个点了不动的控件。
        pagination={{
          defaultPageSize: 10,
          size: 'small',
          hideOnSinglePage: true,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50],
          showTotal: t => `共 ${t} 个项目`,
        }}
      />
    </Card>
  )
}
