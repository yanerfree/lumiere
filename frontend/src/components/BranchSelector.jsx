// 顶部栏分支选择器 — 全局分支切换 + 新建分支（支持从已有分支深拷贝）
import { useState, useEffect, useCallback } from 'react'
import { Select, Modal, Form, Input, Checkbox, Select as AntSelect, message, Tag, Button, Typography } from 'antd'
import { BranchesOutlined, PlusOutlined } from '@ant-design/icons'
import { api } from '../utils/request'
import { useBranch, setBranchId } from '../utils/branch'

// 复制窗口那句「然后呢」的答案。分支名自动填，**两个 git 版本号留占位**——
// 平台不知道 v1.0/v2.0 对应哪两个 tag，这是整条链上唯一需要人给的信息。
//
// 为什么把红线也写进提示语而不只写工具顺序：这三条错法的共同点是**结果都是绿的**，
// 靠事后检查抓不到，只能在动手之前就说清。
function buildDiffPrompt(branchName, stats) {
  const n = stats?.cases?.cases ?? 0
  const sc = stats?.apiTest?.scenarios ?? 0
  return `在 testBench 上给分支「${branchName}」做一次版本升级对账（刚从上一版复制了 ${n} 条用例${sc ? `、${sc} 条接口场景` : ''}）。

两个 git 版本号：从 <填旧版本 tag，如 v1.0.0> 到 <填新版本 tag，如 v2.0.0>。

按这个顺序：
1. tb_list_projects / tb_list_branches 定位到分支「${branchName}」
2. tb_list_project_notes(project_id) —— 先读坑，别重踩
3. tb_list_cases(branch_id, pending_only=true) —— 这批用例各欠哪几维
4. tb_list_branch_endpoints(branch_id) —— 拿平台这一半（用例依赖了哪些端点、哪些字段）
   ⚠ 必读返回里的「覆盖不到的」：手工步骤和 UI 脚本里没有结构化 method/url，
     这套反查探不到它们。纯 UI 改版在这份端点表上一个字都不会变。
5. 本机 git diff <旧>..<新>，读改动的 router / schema：
   改了哪些 url、哪些响应字段、新增了哪些状态值、**新增了哪些端点**
6. 求交集 → tb_apply_endpoint_diff(branch_id, changes=[...], from_ref=..., to_ref=...)
   kind: removed（没了）/ field_changed（字段变了，要写变成什么）/ new_state（新增状态值）
        / renamed（改名挪位置 → 要改不是要废）/ added（新端点 → 待补用例）
   **新端点也要报**：它不命中任何老用例，但那是「该补用例」堆 —— 不报就零覆盖，
   而且永远不会报错（没有任何信号说这里本来该有覆盖）。
7. tb_next_duty(branch_id) 一轮轮干到完，最后 tb_check_branch 交我验收。

三条红线：
· 预期按新版本的**需求**写，不是打开新版本跑一遍照着改 ——
  那是把实现抄了一遍，新版本引入的 bug 会被固化成「预期」，而且步骤/接口/UI
  三份产物同源，会一致地一起错，全绿，没人看得出来。判不出来就带着判断来问我。
· 「我在页面上找不到」≠「这个功能没了」：入口挪到二级菜单、改名、拆成两个页面，
  在 UI 上都长得像没了。别自己废 —— 走 tb_request_deprecate 交正反两面证据。
· 照抄堆内容没变也**必须在新版本上真跑一遍** ——
  「接口签名没变、底层行为变了」只有这一跑抓得到。`
}

export default function BranchSelector({ projectId }) {
  const [branches, setBranches] = useState([])
  const [branchId, switchBranch] = useBranch(projectId)
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  // 复制完弹出的对账提示语。**为什么要它**：分支复制完之后「然后呢」这一步
  // 此前没有答案 —— 人得自己记住要去 CC 里做一次对账、还得自己拼出那串工具调用。
  // 分支名平台自己填，两个 git 版本号留占位由人补：平台不知道 v1.0/v2.0 对应
  // 哪两个 tag，这是**唯一**需要人给的信息。
  const [diffPrompt, setDiffPrompt] = useState(null)
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
      // 只有真复制了用例才需要对账 —— 空分支没有上一版的成果要复用
      if (stats?.cases?.cases) setDiffPrompt(buildDiffPrompt(v.name, stats))
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

      <Modal
        title="分支复制完了 —— 下一步：做一次版本升级对账"
        open={!!diffPrompt}
        onCancel={() => setDiffPrompt(null)}
        footer={[
          <Button key="copy" type="primary" onClick={async () => {
            try {
              await navigator.clipboard.writeText(diffPrompt)
              message.success('已复制，粘到 Claude Code 终端里')
            } catch {
              message.warning('浏览器不给剪贴板权限，手动全选复制下面那段')
            }
          }}>复制提示语</Button>,
          <Button key="close" onClick={() => setDiffPrompt(null)}>知道了</Button>,
        ]}
        width={720}
      >
        <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
          复制过来的用例<b>在新版本上一次都没验过</b>，所以全部回到草稿、待提审 ——
          包括只有手工步骤的那些。哪些能照抄、哪些要改、哪些该废，
          需要拿<b>你本机的 git diff</b> 跟平台的端点表求交集才算得出来：
          平台只有一半数据，它不知道新版本改了什么。
          <br />
          把下面这段粘到 Claude Code 里，<b>两个 git 版本号自己补上</b>（占位已留好）。
        </Typography.Paragraph>
        <Input.TextArea
          value={diffPrompt || ''}
          readOnly
          autoSize={{ minRows: 12, maxRows: 22 }}
          style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
        />
      </Modal>
    </>
  )
}
