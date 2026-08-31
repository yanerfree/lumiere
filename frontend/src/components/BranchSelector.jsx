// 顶部栏分支选择器 — 全局分支切换 + 新建分支（支持从已有分支深拷贝）
import { useState, useEffect, useCallback } from 'react'
import { Select, Modal, Form, Input, Checkbox, Select as AntSelect, message, Tag, Button, Typography } from 'antd'
import { BranchesOutlined, PlusOutlined } from '@ant-design/icons'
import { api } from '../utils/request'
import { useBranch, setBranchId } from '../utils/branch'
import { copyToClipboard } from '../utils/clipboard'

// 复制窗口那句「然后呢」的答案。分支名自动填，**两个 git 版本号留占位**——
// 平台不知道 v1.0/v2.0 对应哪两个 tag，这是整条链上唯一需要人给的信息。
//
// 为什么把红线也写进提示语而不只写工具顺序：这三条错法的共同点是**结果都是绿的**，
// 靠事后检查抓不到，只能在动手之前就说清。
function buildDiffPrompt(branchName, stats) {
  const n = stats?.cases?.cases ?? 0
  const sc = stats?.apiTest?.scenarios ?? 0
  return `在 Lumiere 上给分支「${branchName}」做一次版本升级对账（刚从上一版复制了 ${n} 条用例${sc ? `、${sc} 条接口场景` : ''}）。

两个 git 版本号：从 <填旧版本 tag，如 v1.0.0> 到 <填新版本 tag，如 v2.0.0>。

按这个顺序：
1. lum_list_projects / lum_list_branches 定位到分支「${branchName}」
2. lum_list_project_notes(project_id) —— 先读坑，别重踩
3. lum_list_cases(branch_id, pending_only=true) —— 这批用例各欠哪几维
4. lum_list_branch_endpoints(branch_id) —— 拿平台这一半（用例依赖了哪些端点、哪些字段）
   ⚠ 必读返回里的「覆盖不到的」：手工步骤和 UI 脚本里没有结构化 method/url，
     这套反查探不到它们。纯 UI 改版在这份端点表上一个字都不会变。
5. 本机 git diff <旧>..<新>，读改动的 router / schema：
   改了哪些 url、哪些响应字段、新增了哪些状态值、**新增了哪些端点**
6. 求交集 → lum_apply_endpoint_diff(branch_id, changes=[...], from_ref=..., to_ref=...)
   kind: removed（没了）/ field_changed（字段变了，要写变成什么）/ new_state（新增状态值）
        / renamed（改名挪位置 → 要改不是要废）/ added（新端点 → 待补用例）
   **新端点也要报**：它不命中任何老用例，但那是「该补用例」堆 —— 不报就零覆盖，
   而且永远不会报错（没有任何信号说这里本来该有覆盖）。
7. lum_next_duty(branch_id) 一轮轮干到完，最后 lum_check_branch 交我验收。

三条红线：
· 预期按新版本的**需求**写，不是打开新版本跑一遍照着改 ——
  那是把实现抄了一遍，新版本引入的 bug 会被固化成「预期」，而且步骤/接口/UI
  三份产物同源，会一致地一起错，全绿，没人看得出来。判不出来就带着判断来问我。
· 「我在页面上找不到」≠「这个功能没了」：入口挪到二级菜单、改名、拆成两个页面，
  在 UI 上都长得像没了。别自己废 —— 走 lum_request_deprecate 交正反两面证据。
· 照抄堆内容没变也**必须在新版本上真跑一遍** ——
  「接口签名没变、底层行为变了」只有这一跑抓得到。`
}

// 第一次进这个项目该落在哪条分支上。**不要再用 list[0]**：
// 接口按 (status, created_at) 排，建项目时自动铺的 default 永远排第一，
// 而版本升级的活儿是在后来开的分支上干的 —— 于是新人进来落在一条空分支上，
// 页面只会说「暂无目录 / 暂无用例 / 共 0 条」，看着就是「这个项目没数据」。
// （2026-08-31 真被当成数据丢了报过来：UAG 的 41 条用例全在 v2.2.0，default 是 0。）
//
// 规则：有用例的分支优先，多条就取最新建的；全空才回到 list[0]（新项目就是这种）。
// 只在**没存过选择**时生效 —— 人手动切过就一直听人的，见 utils/branch.js。
// caseCount 是旧后端没有的字段，取不到时 (undefined > 0) 为 false，整个函数
// 自动退回 list[0] 的老行为 —— 不会因为字段缺失就选出一条"假的空分支"。
function pickInitialBranch(list) {
  if (list.length === 0) return null
  const withCases = list.filter(b => b.caseCount > 0)
  if (withCases.length === 0) return list[0]
  return withCases.reduce((a, b) => (
    Date.parse(b.createdAt || 0) > Date.parse(a.createdAt || 0) ? b : a
  ))
}

// 下拉里每条分支后面挂一个量。**空分支要在这里就看得见**，别等人盯着空列表猜。
// caseCount 缺失（旧后端）时两个分支都不成立 —— 什么都不显示，绝不显示成假的 0。
function branchLabel(b) {
  return (
    <span>
      {b.name}
      {b.caseCount === 0 && (
        <span style={{ color: '#c9cdd4', marginLeft: 6, fontSize: 11 }}>空</span>
      )}
      {b.caseCount > 0 && (
        <span style={{ color: '#86909c', marginLeft: 6, fontSize: 11 }}>{b.caseCount}</span>
      )}
    </span>
  )
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
      // 当前没选过、或选中的分支已经不存在了 —— 自动挑一条（挑法见 pickInitialBranch）
      const current = localStorage.getItem(`branch_${projectId}`)
      if (!current || !list.some(b => b.id === current)) {
        const pick = pickInitialBranch(list)
        if (pick) setBranchId(projectId, pick.id)
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
        if (stats.apis) parts.push(`接口库 ${stats.apis.nodes} 个节点`)
        if (stats.apiTest) parts.push(`接口场景 ${stats.apiTest.scenarios} 个`)
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
          ...branches.map(b => ({ value: b.id, label: branchLabel(b) })),
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
                  // 这两条挨着放，措辞必须把「文档」和「可执行」分开写清楚 ——
                  // 原来叫「API 接口」和「接口测试」，只差一个字，真被搞混过（2026-08-27）。
                  { label: '接口库（接口文档树·不可执行）', value: 'apis' },
                  { label: '接口场景（绑用例的编排链·可执行）', value: 'api_test' },
                ]} />
              </Form.Item>
            ) : null}
          </Form.Item>
          <div style={{ fontSize: 12, color: '#86909c' }}>
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
              // http + 局域网 IP 下 navigator.clipboard 不存在，走 utils/clipboard.js
              await copyToClipboard(diffPrompt)
              message.success('已复制，粘到 Claude Code 终端里')
            } catch (e) {
              if (!e?.reported) message.warning('复制不了，手动全选复制下面那段')
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
