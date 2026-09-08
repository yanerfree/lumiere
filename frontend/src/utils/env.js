// 项目级环境选择 — localStorage 持久化 + 自定义事件跨组件同步
// 各页面通过 useEnv(projectId) 获取当前环境，切换时全站生效

import { useState, useEffect, useCallback } from 'react'

const EVENT_NAME = 'lum-env-change'

export function getEnvId(projectId) {
  return localStorage.getItem(`env_${projectId}`) || null
}

export function setEnvId(projectId, envId) {
  if (envId) localStorage.setItem(`env_${projectId}`, envId)
  else localStorage.removeItem(`env_${projectId}`)
  window.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: { projectId, envId } }))
}

export function buildEnvOptions(environments) {
  return (environments || []).map(e => {
    const parts = [e.name]
    const extra = [e.description, e.base_url || e.baseUrl].filter(Boolean).join(' | ')
    return {
      value: e.id,
      label: extra ? `${e.name} (${extra})` : e.name,
    }
  })
}

// 存「项目 + 环境」一对，理由和 utils/branch.js 里那段一模一样：
// 切项目时只靠 effect 收敛，中间会有一次渲染拿着**上一个项目的环境 id**，
// 而环境同样是项目级的（迁移 zzo0envproj），拿去请求就是 404「环境不存在」。
// 渲染时对账，不匹配就现取。
export function useEnv(projectId) {
  const [state, setState] = useState(() => ({
    projectId, envId: projectId ? getEnvId(projectId) : null,
  }))

  if (state.projectId !== projectId) {
    setState({ projectId, envId: projectId ? getEnvId(projectId) : null })
  }

  useEffect(() => {
    const handler = (e) => {
      if (e.detail.projectId === projectId) setState({ projectId, envId: e.detail.envId })
    }
    window.addEventListener(EVENT_NAME, handler)
    return () => window.removeEventListener(EVENT_NAME, handler)
  }, [projectId])

  const envId = state.projectId === projectId
    ? state.envId
    : (projectId ? getEnvId(projectId) : null)

  const switchEnv = useCallback((id) => setEnvId(projectId, id), [projectId])

  return [envId, switchEnv]
}
