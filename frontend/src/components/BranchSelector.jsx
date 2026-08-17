// 顶部栏分支选择器 — 全局分支切换 + 新建分支（支持从已有分支深拷贝）
import { useState, useEffect, useCallback } from 'react'
import { Select, Modal, Form, Input, Checkbox, Select as AntSelect, message, Tag } from 'antd'
import { BranchesOutlined, PlusOutlined } from '@ant-design/icons'
import { api } from '../utils/request'
import { useBranch, setBranchId } from '../utils/branch'

export default function BranchSelector({ projectId }) {
  const [branches, setBranches] = useState([])
  const [branchId, switchBranch] = useBranch(projectId)
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm()

  const fetchBranches = useCallback(async () => {
    if (!projectId) return
    try {
      const res = await api.get(`/projects/${projectId}/branches`)
      const list = (res.data || []).filter(b => b.status === 'active')
      setBranches(list)
      // 如果当前没有选中分支或选中的分支不存在，自动选第一个
      const current = localStorage.getItem(`branch_${projectId}`)
      if (!current || !list.some(b => b.id === current)) {
        if (list.length > 0) setBranchId(projectId, list[0].id)
      }
    } catch { /* */ }
  }, [projectId])

  useEffect(() => { fetchBranches() }, [fetchBranches])

  const handleCreate = async () => {
    try {
      const v = await form.validateFields()
      setCreating(true)
      const body = { name: v.name, description: v.description }
      if (v.sourceBranchId && v.copyModules?.length > 0) {
        body.sourceBranchId = v.sourceBranchId
        body.copyModules = v.copyModules
      }
      const res = await api.post(`/projects/${projectId}/branches`, body)
      const stats = res.data?.copyStats
      if (stats) {
        const parts = []
        if (stats.cases) parts.push(`用例 ${stats.cases.cases} 条`)
        if (stats.apis) parts.push(`API 接口 ${stats.apis.nodes} 个`)
        if (stats.apiTest) parts.push(`接口测试 ${stats.apiTest.scenarios} 个场景`)
        message.success(`分支已创建${parts.length ? '，已复制：' + parts.join('、') : ''}`)
      } else {
        message.success('分支已创建')
      }
      setCreateOpen(false)
      form.resetFields()
      await fetchBranches()
      if (res.data?.id) setBranchId(projectId, res.data.id)
    } catch (e) {
      if (e?.errorFields) return
      message.error(e.message || '创建失败')
    } finally { setCreating(false) }
  }

  if (!projectId || branches.length === 0) return null

  return (
    <>
      <Select
        size="small"
        value={branchId}
        onChange={(v) => {
          if (v === '__create__') {
            form.resetFields()
            setCreateOpen(true)
            return
          }
          switchBranch(v)
        }}
        style={{ minWidth: 120 }}
        variant="borderless"
        prefix={<BranchesOutlined style={{ color: '#7cacf8' }} />}
        options={[
          ...branches.map(b => ({ value: b.id, label: b.name })),
          { value: '__create__', label: <span style={{ color: '#0ea5a0' }}><PlusOutlined /> 新建分支</span> },
        ]}
      />

      <Modal
        title="新建分支"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => setCreateOpen(false)}
        okText="创建"
        cancelText="取消"
        confirmLoading={creating}
        width={480}
      >
        <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="name" label="分支名称" rules={[
            { required: true, message: '请输入分支名称' },
            { pattern: /^[a-zA-Z0-9_-]+(\.[a-zA-Z0-9_-]+)*$/, message: '仅支持字母、数字、下划线、连字符、点号（点号不能开头、结尾或连用）' },
          ]}>
            <Input placeholder="如：v2.0、release-2026Q3" />
          </Form.Item>
          <Form.Item name="description" label="描述（可选）">
            <Input placeholder="分支用途说明" />
          </Form.Item>
          <Form.Item name="sourceBranchId" label="基于分支复制（可选）">
            <AntSelect
              placeholder="不选则创建空分支"
              allowClear
              options={branches.map(b => ({ value: b.id, label: b.name }))}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.sourceBranchId !== cur.sourceBranchId}>
            {({ getFieldValue }) => getFieldValue('sourceBranchId') ? (
              <Form.Item name="copyModules" label="复制模块" initialValue={['cases', 'apis', 'api_test']}>
                <Checkbox.Group options={[
                  { label: '用例管理（文件夹+用例）', value: 'cases' },
                  { label: 'API 接口（接口树）', value: 'apis' },
                  { label: '接口测试（文件夹+场景+步骤）', value: 'api_test' },
                ]} />
              </Form.Item>
            ) : null}
          </Form.Item>
          <div style={{ fontSize: 12, color: '#8c8c8c' }}>
            复制后所有数据独立（新 ID），场景状态重置为草稿，执行历史不带入。测试报告和测试计划不复制。
          </div>
        </Form>
      </Modal>
    </>
  )
}
