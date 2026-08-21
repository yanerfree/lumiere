import { useState, useEffect, useCallback } from 'react'
import { Modal, Tree, Table, Input, Select, Tag, Space, Pagination, Spin, Empty, message } from 'antd'
import { SearchOutlined, FolderOutlined } from '@ant-design/icons'
import { api } from '../utils/request'

const priorityColors = { P0: '#fff', P1: '#fff', P2: '#fff', P3: '#fff' }
const priorityBg = { P0: '#ff7875', P1: '#ffc069', P2: '#85a5ff', P3: 'rgba(0,0,0,0.15)' }

const buildTreeData = (nodes) => nodes.map(n => ({
  title: `${n.name} (${n.caseCount})`,
  key: n.id,
  icon: <FolderOutlined style={{ color: '#0ea5a0' }} />,
  children: n.children?.length > 0 ? buildTreeData(n.children) : undefined,
}))

export default function CasePicker({ open, projectId, selectedIds = [], onOk, onCancel }) {
  const [branchId, setBranchId] = useState(null)
  const [folderTree, setFolderTree] = useState([])
  const [selectedFolderId, setSelectedFolderId] = useState(null)
  const [cases, setCases] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [keyword, setKeyword] = useState('')
  const [priority, setPriority] = useState(null)
  const [caseType, setCaseType] = useState(null)
  const [picked, setPicked] = useState([])
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setPicked([...selectedIds])
      setPage(1)
      setKeyword('')
      setPriority(null)
      setCaseType(null)
      setSelectedFolderId(null)
    }
  }, [open])

  useEffect(() => {
    if (!open || !projectId) return
    ;(async () => {
      try {
        const res = await api.get(`/projects/${projectId}/branches`)
        const active = (res.data || []).find(b => b.isDefault) || res.data?.[0]
        if (active) {
          setBranchId(active.id)
          const fRes = await api.get(`/projects/${projectId}/branches/${active.id}/folders`)
          setFolderTree(fRes.data || [])
        }
      } catch { /* */ }
    })()
  }, [open, projectId])

  const fetchCases = useCallback(async () => {
    if (!projectId || !branchId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ page, pageSize })
      if (keyword) params.set('keyword', keyword)
      if (priority) params.set('priority', priority)
      if (caseType) params.set('type', caseType)
      if (selectedFolderId) params.set('folderId', selectedFolderId)
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/cases?${params}`)
      setCases(res.data || [])
      setTotal(res.pagination?.total || 0)
    } catch { /* */ } finally { setLoading(false) }
  }, [projectId, branchId, page, pageSize, keyword, priority, caseType, selectedFolderId])

  useEffect(() => {
    if (open && branchId) fetchCases()
  }, [open, branchId, fetchCases])

  const columns = [
    {
      title: '编号', dataIndex: 'caseCode', width: 140,
      render: v => <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#86909c' }}>{v}</span>,
    },
    {
      title: '标题', dataIndex: 'title', ellipsis: true,
      render: v => <span style={{ fontWeight: 500 }}>{v}</span>,
    },
    {
      title: '模块', dataIndex: 'module', width: 100,
      render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{v || '-'}</span>,
    },
    {
      title: '优先级', dataIndex: 'priority', width: 68, align: 'center',
      render: v => <Tag style={{ background: priorityBg[v], color: priorityColors[v], border: 'none' }}>{v}</Tag>,
    },
    {
      title: '类型', dataIndex: 'type', width: 65, align: 'center',
      render: v => <span style={{ fontSize: 12, color: '#86909c' }}>{v?.toUpperCase()}</span>,
    },
  ]

  const treeData = buildTreeData(folderTree)

  return (
    <Modal
      title={null}
      open={open}
      width={1200}
      onCancel={onCancel}
      onOk={() => {
        if (picked.length === 0) { message.warning('请至少选择 1 条用例'); return }
        onOk(picked)
      }}
      okText={`确定（已选 ${picked.length} 条）`}
      cancelText="取消"
      confirmLoading={saving}
      styles={{ body: { padding: 0 } }}
      style={{ top: 40 }}
    >
      {/* Header */}
      <div style={{ padding: '20px 24px 12px', borderBottom: '1px solid rgba(0,0,0,0.06)' }}>
        <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 4 }}>选择用例</div>
        <div style={{ fontSize: 13, color: '#86909c' }}>从用例库中选择要关联到计划的用例</div>
      </div>

      <div style={{ display: 'flex', height: 560 }}>
        {/* Left: Folder Tree */}
        <div style={{
          width: 220, borderRight: '1px solid rgba(0,0,0,0.06)', padding: '12px 0',
          overflow: 'auto', flexShrink: 0, background: 'rgba(0,0,0,0.02)',
        }}>
          <div style={{ padding: '0 12px 8px', fontSize: 13, fontWeight: 600, color: '#4e5969' }}>
            用例目录
          </div>
          <div
            onClick={() => { setSelectedFolderId(null); setPage(1) }}
            style={{
              padding: '6px 16px', cursor: 'pointer', fontSize: 13,
              background: selectedFolderId === null ? '#e0f7f6' : 'transparent',
              color: selectedFolderId === null ? '#0ea5a0' : '#4e5969',
              fontWeight: selectedFolderId === null ? 600 : 400,
            }}
          >
            全部用例
          </div>
          {treeData.length > 0 ? (
            <Tree
              treeData={treeData}
              defaultExpandAll
              showIcon
              blockNode
              selectedKeys={selectedFolderId ? [selectedFolderId] : []}
              onSelect={(keys) => { setSelectedFolderId(keys[0] || null); setPage(1) }}
              style={{ fontSize: 13, background: 'transparent' }}
            />
          ) : (
            <div style={{ padding: 20, textAlign: 'center', color: '#c9cdd4', fontSize: 12 }}>
              暂无目录
            </div>
          )}
        </div>

        {/* Right: Filter + Table */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
          {/* Filters */}
          <div style={{ padding: '12px 16px', borderBottom: '1px solid rgba(0,0,0,0.06)' }}>
            <Space size={10} wrap>
              <Input
                prefix={<SearchOutlined style={{ color: '#c9cdd4' }} />}
                placeholder="搜索编号或标题"
                value={keyword}
                onChange={e => { setKeyword(e.target.value); setPage(1) }}
                allowClear
                style={{ width: 220 }}
                size="small"
              />
              <Select
                placeholder="优先级"
                value={priority}
                onChange={v => { setPriority(v); setPage(1) }}
                allowClear
                size="small"
                style={{ width: 100 }}
                options={[
                  { label: 'P0', value: 'P0' },
                  { label: 'P1', value: 'P1' },
                  { label: 'P2', value: 'P2' },
                  { label: 'P3', value: 'P3' },
                ]}
              />
              <Select
                placeholder="类型"
                value={caseType}
                onChange={v => { setCaseType(v); setPage(1) }}
                allowClear
                size="small"
                style={{ width: 100 }}
                options={[
                  { label: 'API', value: 'api' },
                  { label: 'E2E', value: 'e2e' },
                ]}
              />
              <Tag style={{ background: 'var(--green-bg)', color: '#0ea5a0', border: 'none', fontWeight: 600 }}>
                已选 {picked.length} 条
              </Tag>
            </Space>
          </div>

          {/* Table */}
          <div style={{ flex: 1, overflow: 'auto', padding: '0 16px' }}>
            <Table
              dataSource={cases}
              columns={columns}
              rowKey="id"
              size="small"
              loading={loading}
              pagination={false}
              scroll={{ y: 400 }}
              rowSelection={{
                selectedRowKeys: picked,
                onChange: keys => setPicked(keys),
                preserveSelectedRowKeys: true,
              }}
            />
          </div>

          {/* Bottom: Stats + Pagination */}
          <div style={{
            padding: '8px 16px', borderTop: '1px solid rgba(0,0,0,0.06)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontSize: 13, color: '#86909c' }}>
              共 {total} 条用例
            </span>
            <Pagination
              current={page}
              total={total}
              pageSize={pageSize}
              size="small"
              showSizeChanger
              showQuickJumper
              pageSizeOptions={[20, 50, 100]}
              showTotal={t => `共 ${t} 条`}
              onChange={(p, ps) => { if (ps !== pageSize) { setPageSize(ps); setPage(1) } else { setPage(p) } }}
            />
          </div>
        </div>
      </div>
    </Modal>
  )
}
