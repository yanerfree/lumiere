import { createContext, useContext, useEffect, useMemo, useState, useCallback } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from './request'
import { mePermissionsPath } from './permissions'

// 当前用户的权限点集合（随所在项目语境变化）。
// 一处拉取、全应用共读，避免每个页面各自问一遍、各自拍一套规则。
const PermissionContext = createContext({
  permissions: new Set(),
  systemRole: null,
  projectRole: null,
  isSuperAdmin: false,
  loading: true,
  has: () => false,
  hasAny: () => false,
  reload: () => {},
})

// 从路径里抠出当前项目 id（与 App.jsx 顶栏取 projectId 的正则同款）。
// 不在项目页时为 null —— 此时只解析系统级权限点。
function projectIdFromPath(pathname) {
  const m = pathname.match(/\/projects\/([^/]+)/)
  return m ? m[1] : null
}

export function PermissionProvider({ children }) {
  const location = useLocation()
  const projectId = projectIdFromPath(location.pathname)

  const [state, setState] = useState({
    permissions: new Set(),
    systemRole: null,
    projectRole: null,
    isSuperAdmin: false,
    loading: true,
  })

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true }))
    try {
      const res = await api.get(mePermissionsPath(projectId))
      const d = res.data || {}
      setState({
        permissions: new Set(d.permissions || []),
        systemRole: d.systemRole ?? d.system_role ?? null,
        projectRole: d.projectRole ?? d.project_role ?? null,
        isSuperAdmin: !!(d.isSuperAdmin ?? d.is_super_admin),
        loading: false,
      })
    } catch {
      // 拉不到（未登录/网络）→ 空集合 + 收起 loading，页面按「无权限」渲染而非卡住
      setState({
        permissions: new Set(),
        systemRole: null,
        projectRole: null,
        isSuperAdmin: false,
        loading: false,
      })
    }
  }, [projectId])

  useEffect(() => {
    load()
  }, [load])

  const value = useMemo(() => {
    const has = (perm) => {
      if (!perm) return true
      if (state.isSuperAdmin) return true
      return state.permissions.has(perm)
    }
    const hasAny = (...perms) => perms.some((p) => has(p))
    return { ...state, has, hasAny, reload: load }
  }, [state, load])

  return <PermissionContext.Provider value={value}>{children}</PermissionContext.Provider>
}

export function usePermissions() {
  return useContext(PermissionContext)
}
