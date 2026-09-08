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

// state 里存的是「哪个项目的哪条分支」**一对**，不是光一条分支 id。
//
// 只存 branchId 那种写法有个静默的坑，2026-09-04 在用例管理页炸出来过：
// 切项目时 URL 上的 projectId 先变，而 state 要等 effect 才跟得上 ——
// 中间那一次渲染就是 (新项目, 旧项目的分支) 这对不匹配的组合，
// 而下游那几个 `[projectId, globalBranchId]` 的 effect **在这一次就发请求了**
// （effect 里的 setState 不会倒回去改同一批 effect 闭包里的旧值）。
// 后端的归属校验（app/deps/scope.py）对这种组合一律 404「分支不存在」，
// 于是一次切项目弹四五条红 toast，看着像整个页面坏了。
//
// 所以在**渲染时**就地对账：state 里记着的项目跟当前 projectId 不一致，
// 当场从 localStorage 现取，不等 effect。effect 仍然要留 —— 它负责订阅
// 切换事件，也负责把 state 收敛到当前项目。
export function useBranch(projectId) {
  const [state, setState] = useState(() => ({
    projectId, branchId: projectId ? getBranchId(projectId) : null,
  }))

  // 项目变了就**在渲染里**改回来（React 官方那条 "adjusting state when a prop changes"）。
  // 放进 effect 就晚一拍，而这一拍正好够下游发一轮请求 —— 那是这个 bug 的成因本身。
  if (state.projectId !== projectId) {
    setState({ projectId, branchId: projectId ? getBranchId(projectId) : null })
  }

  // 只订阅切换事件，effect 里不再 setState 收敛（那件事上面已经做完了）
  useEffect(() => {
    const handler = (e) => {
      if (e.detail.projectId === projectId) setState({ projectId, branchId: e.detail.branchId })
    }
    window.addEventListener(EVENT_NAME, handler)
    return () => window.removeEventListener(EVENT_NAME, handler)
  }, [projectId])

  // 上面那次 setState 会让 React 立刻重渲染、丢掉这一轮的结果，但这一轮的函数体
  // 还是会跑完（下面还有 hook 要调）。所以返回值也现算一遍，别把旧分支交出去。
  const branchId = state.projectId === projectId
    ? state.branchId
    : (projectId ? getBranchId(projectId) : null)

  const switchBranch = useCallback((id) => setBranchId(projectId, id), [projectId])

  return [branchId, switchBranch]
}
