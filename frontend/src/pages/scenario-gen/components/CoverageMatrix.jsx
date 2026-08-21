import { useState, useEffect, useCallback } from 'react'
import { Table, Tag, Button, Tooltip, Typography, Space, Card } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api } from '../../../utils/request'

const { Text } = Typography

const DIM_COLORS = {
  positive: '#0ea5a0', negative: '#e8453c', boundary: '#faad14',
  permission: '#7c5cbf', data: '#0ea5a0', state: '#4e8af0',
}
const DIM_LABELS = {
  positive: '正向', negative: '异常', boundary: '边界',
  permission: '权限', data: '数据', state: '状态',
}
const DIMENSIONS = ['positive', 'negative', 'boundary', 'permission', 'data', 'state']

export default function CoverageMatrix({ projectId, branchId, taskId }) {
  const [matrix, setMatrix] = useState(null)
  const [loading, setLoading] = useState(false)

  const basePath = `/projects/${projectId}/branches/${branchId}/scenario-gen/tasks/${taskId}`

  const fetchMatrix = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get(`${basePath}/coverage-matrix`)
      setMatrix(res.data)
    } catch { /* */ }
    finally { setLoading(false) }
  }, [basePath])

  useEffect(() => { fetchMatrix() }, [fetchMatrix])

  if (!matrix) {
    return <div style={{ textAlign: 'center', padding: 60, color: '#bfc4cd' }}>加载覆盖矩阵...</div>
  }

  const { points, summary } = matrix

  const columns = [
    {
      title: '需求点', dataIndex: 'code', key: 'code', width: 80, fixed: 'left',
      render: (code, record) => (
        <Tooltip title={record.title}>
          <Text code style={{ fontSize: 12 }}>{code}</Text>
          {record.status === 'not_applicable' && <Tag color="default" style={{ marginLeft: 4, fontSize: 11 }}>N/A</Tag>}
        </Tooltip>
      ),
    },
    {
      title: '标题', dataIndex: 'title', key: 'title', width: 200, ellipsis: true,
    },
    ...DIMENSIONS.map(dim => ({
      title: DIM_LABELS[dim],
      key: dim,
      width: 70,
      align: 'center',
      render: (_, record) => {
        if (record.status === 'not_applicable') return <Text type="secondary">⊘</Text>
        const cell = record.cells?.[dim]
        const count = cell?.count || 0
        if (count === 0) {
          return (
            <Tooltip title="点击补充生成">
              <span style={{ color: '#faad14', cursor: 'pointer', fontWeight: 600 }}>○</span>
            </Tooltip>
          )
        }
        return (
          <Tooltip title={`${count} 条用例`}>
            <span style={{ color: DIM_COLORS[dim], fontWeight: 600, cursor: 'pointer' }}>●{count}</span>
          </Tooltip>
        )
      },
    })),
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Text strong style={{ fontSize: 15 }}>覆盖矩阵</Text>
        <Button size="small" onClick={fetchMatrix} loading={loading}>刷新</Button>
      </div>

      <Table
        dataSource={points}
        columns={columns}
        rowKey="code"
        loading={loading}
        pagination={false}
        size="small"
        scroll={{ x: 800 }}
        rowClassName={(record) => {
          if (record.status === 'not_applicable') return ''
          const total = DIMENSIONS.reduce((s, d) => s + (record.cells?.[d]?.count || 0), 0)
          if (total === 0) return 'zero-coverage-row'
          return ''
        }}
      />

      <style>{`.zero-coverage-row { background: rgba(250,173,20,0.04) !important; }`}</style>

      <Card size="small" style={{ marginTop: 12, background: 'rgba(124,172,248,0.04)' }}>
        <Space size="large">
          <Text type="secondary" style={{ fontSize: 12 }}>
            需求点 {summary.totalPoints}
          </Text>
          {summary.zeroCoverage > 0 && (
            <Text style={{ fontSize: 12, color: '#e8453c', fontWeight: 600 }}>
              ⚠ 零覆盖 {summary.zeroCoverage}
            </Text>
          )}
          {summary.weakCoverage > 0 && (
            <Text style={{ fontSize: 12, color: '#faad14' }}>
              弱覆盖(仅正向) {summary.weakCoverage}
            </Text>
          )}
          {summary.zeroCoverage === 0 && summary.weakCoverage === 0 && (
            <Text style={{ fontSize: 12, color: '#0ea5a0' }}>
              ✓ 全部覆盖
            </Text>
          )}
        </Space>
      </Card>
    </div>
  )
}
