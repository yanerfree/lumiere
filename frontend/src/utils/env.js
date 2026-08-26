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

export function useEnv(projectId) {
  const [envId, setEnvIdState] = useState(() => projectId ? getEnvId(projectId) : null)

  useEffect(() => {
    setEnvIdState(projectId ? getEnvId(projectId) : null)
    const handler = (e) => {
      if (e.detail.projectId === projectId) setEnvIdState(e.detail.envId)
    }
    window.addEventListener(EVENT_NAME, handler)
    return () => window.removeEventListener(EVENT_NAME, handler)
  }, [projectId])

  const switchEnv = useCallback((id) => setEnvId(projectId, id), [projectId])

  return [envId, switchEnv]
}
