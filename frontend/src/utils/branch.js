// 全局分支状态 — localStorage 持久化 + 自定义事件跨组件同步
// 各页面通过 useBranch(projectId) 获取当前分支，切换时全站生效

import { useState, useEffect, useCallback } from 'react'

const EVENT_NAME = 'lum-branch-change'

export function getBranchId(projectId) {
  return localStorage.getItem(`branch_${projectId}`) || null
}

export function setBranchId(projectId, branchId) {
  if (branchId) localStorage.setItem(`branch_${projectId}`, branchId)
  else localStorage.removeItem(`branch_${projectId}`)
  window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { projectId, branchId } }))
}

export function useBranch(projectId) {
  const [branchId, setBranchIdState] = useState(() => projectId ? getBranchId(projectId) : null)

  useEffect(() => {
    setBranchIdState(projectId ? getBranchId(projectId) : null)
    const handler = (e) => {
      if (e.detail.projectId === projectId) setBranchIdState(e.detail.branchId)
    }
    window.addEventListener(EVENT_NAME, handler)
    return () => window.removeEventListener(EVENT_NAME, handler)
  }, [projectId])

  const switchBranch = useCallback((id) => setBranchId(projectId, id), [projectId])

  return [branchId, switchBranch]
}
