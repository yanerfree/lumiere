import { useState } from 'react'
import { Button, Tag, Space, Input, Table, Popconfirm, Dropdown, Modal, TreeSelect, message } from 'antd'
import { PlusOutlined, RobotOutlined, DeleteOutlined, SearchOutlined, DownOutlined } from '@ant-design/icons'

const PRIORITY_COLORS = { P0: 'red', P1: 'orange', P2: 'blue', P3: 'default' }

export default function ScenarioList({
  scenarios, selectedFolderIds, loading,
  searchKeyword, onSearchChange,
  statusFilter, onStatusChange,
  kindFilter, onKindChange,
  onSelectScenario, onDelete,
  onGenerate, onCreate, onBatch,
  folderTree,
}) {
  const [selectedIds, setSelectedIds] = useState([])
  const [moveOpen, setMoveOpen] = useState(false)
  const [moveTarget, setMoveTarget] = useState(null)

  const buildFolderSelect = (nodes) => (nodes || []).map(n => ({
    value: n.id, title: n.name,
    children: n.children?.length > 0 ? buildFolderSelect(n.children) : undefined,
  }))

  const filtered = (() => {
    let data = selectedFolderIds?.length > 0
      ? scenarios.filter(s => selectedFolderIds.includes(s.folderId))
      : scenarios
    if (statusFilter !== 'all') data = data.filter(s => s.status === statusFilter)
    if (searchKeyword) {
      const kw = searchKeyword.toLowerCase()
      data = data.filter(s => (s.title || '').toLowerCase().includes(kw) || (s.code || '').toLowerCase().includes(kw))
    }
    return data
  })()

  const batchItems = [
    { key: 'publish', label: '批量发布（草稿→已发布）' },
    { key: 'deprecate', label: '批量废弃（已发布→已废弃）' },
    { key: 'move', label: '批量移动到文件夹' },
    { key: 'delete', label: '批量删除', danger: true },
  ]

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ padding: '10px 16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <Space size={8} wrap>
          <Input
            prefix={<SearchOutlined style={{ color: '#bfbfbf' }} />}
            placeholder="搜索场景ID或标题"
            value={searchKeyword}
            onChange={e => onSearchChange(e.target.value)}
            style={{ width: 200 }}
            size="small"
            allowClear
          />
          <Space size={0}>
            {[
              { key: 'all', label: '全部' },
              { key: 'draft', label: '草稿' },
              { key: 'published', label: '已发布' },
              { key: 'deprecated', label: '已废弃' },
            ].map(f => (
              <Button key={f.key} size="small" type={statusFilter === f.key ? 'primary' : 'default'}
                onClick={() => onStatusChange(f.key)} style={{ borderRadius: 0, ...(f.key === 'all' ? { borderRadius: '8px 0 0 8px' } : f.key === 'deprecated' ? { borderRadius: '0 8px 8px 0' } : {}) }}>
                {f.label}
              </Button>
            ))}
          </Space>
          {/* 归属切换：本模块管单接口，用例编排链在「用例详情 → 接口测试」里。
              默认只看单接口，但留个口子能翻出来，不做成凭空消失。 */}
          <Space size={0}>
            {[
              { key: 'single', label: '单接口' },
              { key: 'orchestrated', label: '用例编排' },
              { key: 'all', label: '不限' },
            ].map(f => (
              <Button key={f.key} size="small" type={kindFilter === f.key ? 'primary' : 'default'}
                onClick={() => onKindChange(f.key)}
                style={{ borderRadius: 0, ...(f.key === 'single' ? { borderRadius: '8px 0 0 8px' } : f.key === 'all' ? { borderRadius: '0 8px 8px 0' } : {}) }}>
                {f.label}
              </Button>
            ))}
          </Space>
          {selectedIds.length > 0 && (
            <Dropdown menu={{ items: batchItems, onClick: ({ key }) => {
              if (key === 'delete') {
                Modal.confirm({
                  title: '确认批量删除', content: `确定要删除选中的 ${selectedIds.length} 个场景吗？`, okText: '删除', cancelText: '取消', okButtonProps: { danger: true },
                  onOk: () => { onBatch(key, selectedIds); setSelectedIds([]) },
                })
                return
              }
              if (key === 'move') { setMoveOpen(true); setMoveTarget(null); return }
              onBatch(key, selectedIds)
              setSelectedIds([])
            }}} trigger={['click']}>
              <Button size="small">
                批量操作 ({selectedIds.length}) <DownOutlined />
              </Button>
            </Dropdown>
          )}
        </Space>
        <Space size={8}>
          <Button icon={<RobotOutlined />} type="primary" onClick={onGenerate}>
            AI 生成测试
          </Button>
          <Button icon={<PlusOutlined />} onClick={onCreate}>
            新建场景
          </Button>
        </Space>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '0 16px 16px' }}>
        <Table
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={filtered}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: setSelectedIds,
          }}
          pagination={{ pageSize: 20, size: 'small', showTotal: t => `共 ${t} 条` }}
          onRow={r => ({ onClick: () => onSelectScenario(r.id), style: { cursor: 'pointer' } })}
          columns={[
            {
              title: '场景ID', dataIndex: 'code', width: 100,
              render: v => <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#86909c' }}>{v}</span>,
            },
            {
              title: '标题', dataIndex: 'title', ellipsis: true,
              render: t => <span style={{ fontWeight: 500 }}>{t}</span>,
            },
            {
              title: '来源', dataIndex: 'source', width: 60,
              render: v => <Tag color={v === 'ai' ? 'blue' : 'default'} style={{ fontSize: 11 }}>{v === 'ai' ? 'AI' : '手动'}</Tag>,
            },
            {
              title: '优先级', dataIndex: 'priority', width: 70,
              render: v => <Tag color={PRIORITY_COLORS[v]}>{v}</Tag>,
            },
            {
              title: '状态', dataIndex: 'status', width: 70,
              render: v => <Tag color={v === 'published' ? '#0ea5a0' : v === 'deprecated' ? 'default' : undefined}>
                {v === 'published' ? '已发布' : v === 'deprecated' ? '已废弃' : '草稿'}
              </Tag>,
            },
            {
              title: '操作', width: 60,
              render: (_, r) => (
                <Space size={4} onClick={e => e.stopPropagation()}>
                  <Popconfirm title="确认删除？" onConfirm={() => onDelete(r.id)}>
                    <Button type="text" size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </div>

      <Modal title="批量移动到文件夹" open={moveOpen}
        onOk={() => { onBatch('move', selectedIds, moveTarget); setMoveOpen(false); setSelectedIds([]) }}
        onCancel={() => setMoveOpen(false)}
        okText="移动" cancelText="取消" width={400}>
        <TreeSelect value={moveTarget} onChange={setMoveTarget}
          treeData={buildFolderSelect(folderTree)}
          placeholder="选择目标文件夹" allowClear style={{ width: '100%' }} />
      </Modal>
    </div>
  )
}
