import { usePermissions } from '../utils/PermissionContext'

// 权限门：持有 perm（或 anyOf 里任一）才渲染 children，否则渲染 fallback（默认什么都不渲染）。
//   <Can perm="case.write"><Button>新建</Button></Can>
//   <Can anyOf={['env.write','project.settings']}>...</Can>
// 需要「显示但禁用」而非「隐藏」时，直接用 usePermissions().has(...) 驱动按钮 disabled 更合适。
export default function Can({ perm, anyOf, fallback = null, children }) {
  const { has, hasAny } = usePermissions()
  const ok = anyOf ? hasAny(...anyOf) : has(perm)
  return ok ? children : fallback
}
