// 一次失败的三层判断：平台现象（机器算的）/ CC 归因（建议）/ 人工确认（唯一算数的结论）。
// 三层分开显示而不是并排两栏让人挑 —— 那是把矛盾转嫁给用户，两栏一旦不一致
// 用户对两边都会失去信任。
//
// 用例详情「执行历史」和报告详情「失败场景」共用这一份：QA 看失败主要在报告页，
// 只做在用例详情里等于没做；两份实现则一定会各改各的。
import { useState, useEffect } from 'react'
import { Tag, Select, Input, Button, message } from 'antd'
import { api } from '../utils/request'

export default function FailureTriagePanel({ projectId, branchId, caseId, run, onConfirmed }) {
  const [data, setData] = useState(null)
  const [cause, setCause] = useState(null)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)

  const runId = run?.id
  const base = `/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/runs/${runId}`
  useEffect(() => {
    if (!projectId || !branchId || !caseId || !runId) return
    api.get(`${base}/analysis`).then(res => {
      setData(res.data)
      // 预填 CC 的判断 —— 人多数时候是认可的，改一下就是覆盖
      setCause(res.data.confirmedCause || res.data.ccAnalysis?.cause || null)
      setNote(res.data.confirmedNote || '')
    }).catch(() => {})
  }, [runId, projectId, branchId, caseId])

  if (!data) return null
  const cc = data.ccAnalysis
  const confirmed = !!data.confirmedCause

  const submit = async () => {
    if (!cause) { message.warning('先选一个原因'); return }
    if (!note.trim()) { message.warning('写一句理由 —— 确认是唯一算数的结论，不是走形式'); return }
    setSaving(true)
    try {
      await api.post(`${base}/confirm`, { cause, note })
      message.success('已确认')
      const res = await api.get(`${base}/analysis`)
      setData(res.data)
      onConfirmed?.()
    } catch (e) {
      message.error(e?.response?.data?.error?.message || '确认失败')
    } finally { setSaving(false) }
  }

  return (
    <div style={{ marginBottom: 12, padding: 12, borderRadius: 10, background: 'rgba(250,140,22,0.04)', border: '1px solid rgba(250,140,22,0.18)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: '#86909c' }}>平台现象</span>
        <Tag color="orange" style={{ margin: 0 }}>{data.phenomenon || 'unknown'}</Tag>
        <span style={{ fontSize: 11, color: '#c9cdd4' }}>规则算的，只说"是什么"，不说"为什么"</span>
      </div>

      {cc ? (
        <div style={{ marginBottom: 10, paddingLeft: 10, borderLeft: '2px solid rgba(78,138,240,0.35)' }}>
          <div style={{ fontSize: 12, marginBottom: 3 }}>
            <span style={{ color: '#86909c' }}>Claude Code 归因　</span>
            <Tag color="blue" style={{ margin: 0 }}>{cc.cause}</Tag>
            <span style={{ marginLeft: 6, color: '#86909c' }}>置信 {cc.confidence}</span>
            <span style={{ marginLeft: 6, color: '#c9cdd4' }}>by {cc.author} · {(cc.submittedAt || '').slice(0, 16).replace('T', ' ')}</span>
          </div>
          <div style={{ fontSize: 12, color: '#4e5969', lineHeight: 1.6 }}>{cc.reasoning}</div>
          <div style={{ marginTop: 4, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {(cc.evidence || []).map((e, i) => (
              <Tag key={i} style={{ fontSize: 11, margin: 0 }}>{e.type}: {String(e.ref).slice(0, 46)}</Tag>
            ))}
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 12, color: '#c9cdd4', marginBottom: 10 }}>
          还没有归因。在 Claude Code 里说：分析用例 {caseId} 最近一次失败并回推（它会先调 tb_get_ui_script_result 拿证据包）
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: confirmed ? '#0ea5a0' : '#1d2129', fontWeight: 600 }}>
          {confirmed ? '已确认' : '人工确认'}
        </span>
        <Select size="small" style={{ width: 210 }} value={cause} onChange={setCause}
          placeholder="确认原因" options={(data.causeOptions || []).map(o => ({ value: o.value, label: o.label }))} />
        <Input size="small" style={{ flex: 1, minWidth: 200 }} value={note} onChange={e => setNote(e.target.value)}
          placeholder="为什么是这个原因（必填）" />
        <Button size="small" type="primary" loading={saving} onClick={submit}>
          {confirmed ? '更新确认' : '确认'}
        </Button>
      </div>
      {confirmed && (
        <div style={{ fontSize: 11, color: '#86909c', marginTop: 4 }}>
          确认于 {(data.confirmedAt || '').slice(0, 16).replace('T', ' ')}
          {cc && cc.cause !== data.confirmedCause && <span style={{ color: '#ff7d00', marginLeft: 8 }}>（推翻了 CC 的判断）</span>}
        </div>
      )}
    </div>
  )
}
