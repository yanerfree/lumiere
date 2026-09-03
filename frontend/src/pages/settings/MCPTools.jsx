import { useState, useEffect, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { Card, Tag, Space, Typography, Button, message, Input, Modal, Popconfirm, Tabs, Badge, Checkbox, Tooltip, Alert, Collapse, Switch, Segmented } from 'antd'
import {
  ApiOutlined, CopyOutlined, ThunderboltOutlined,
  KeyOutlined, PlusOutlined, DeleteOutlined, CheckCircleOutlined,
  RobotOutlined, LinkOutlined, DownOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import { formatTime } from '../../utils/timeCol'
import { copyToClipboard } from '../../utils/clipboard'
import { useBranch } from '../../utils/branch'
import { PERM } from '../../utils/permissions'
import { usePermissions } from '../../utils/PermissionContext'

const { Text } = Typography

// 分类名跟后端 _section() 一一对应，改名要同步改。
// ⚠ 漏一个不会报错，只会静默变成灰色 —— 「失败归因」就这么灰了一整轮没人发现。
// 后端加了新分类时这里要跟着加，test_mcp_category_colors 会红。
const CAT_COLORS = {
  '定位项目/分支': 'green', '用例·手工步骤': 'blue', '接口库·只记怎么调': 'cyan',
  '接口场景·可执行': 'geekblue', '环境与变量': 'orange', '回推入库': 'red',
  '需求→用例流水线': 'magenta', 'UI 脚本': 'volcano', '执行报告': 'purple',
  '项目须知': 'magenta', 'Mock 与观测': 'cyan',
  '文档规范': 'gold', 'Skill 共享': 'lime', '失败归因': 'error',
  '版本升级·分支对账': 'gold', 'QA 仓对账': 'processing',
  // 平台反馈是**唯一一组「对象是 Lumiere 自己」的工具**，色相刻意跟其它组拉开：
  // 别的组都在说被测系统，只有这一组在说平台自己。
  '平台反馈': 'slate', '其它': 'default',
}

/*
 * 档位从后端取（app/mcp/profiles.py）——和工具注册表同一个进程，
 * 才不会重演"前端写死 20 条、后端实际 32 条"那种漂移。
 * 档位只是勾选的快捷方式，落库存的仍是展开后的显式工具名列表：
 * 语义可审计，日后改档位定义也不会让已有项目的范围悄悄变。
 */

const cardStyle = { borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)', boxShadow: 'none' }
const sectionTitle = { fontSize: 14, fontWeight: 600, color: '#1d2129', marginBottom: 4 }

// 分类色板对应的实色（Tag 的语义色拿不到色值，分组条要用真颜色）。
// 十几个分类需要十几种能分辨的色，站内色板只有 6 个主色不够用 —— 所以这里
// 保留同样的色相分布，但把饱和度/明度压到跟站内色板同一档：原来直接用 antd
// 默认色（#52c41a / #1677ff / #eb2f96…），比全站其他地方艳一截，扎眼。
const CAT_HEX = {
  green: '#2ec4b6', blue: '#4e8af0', cyan: '#0ea5a0', geekblue: '#5a6fd8',
  orange: '#ff7d00', red: '#e8453c', magenta: '#d9548f', volcano: '#f2734d',
  purple: '#7c5cbf', gold: '#faad14', lime: '#8fbf3f', error: '#e8453c',
  slate: '#6b7a99',
  default: '#a9b0ba',
}
const catHex = (cat) => CAT_HEX[CAT_COLORS[cat]] || CAT_HEX.default

/** 一行工具（明细里用）。说明夹两行，悬停看全文。 */
function ToolRow({ t, checked, disabled, onToggle }) {
  return (
    <label style={{
      display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 14px 8px 34px',
      borderTop: '1px solid rgba(0,0,0,0.04)', cursor: disabled ? 'default' : 'pointer',
      background: checked && !disabled ? 'rgba(14,165,160,0.035)' : 'transparent',
    }}>
      <Checkbox disabled={disabled} checked={checked}
        onChange={e => onToggle(t.name, e.target.checked)} style={{ marginTop: 1 }} />
      <Text code style={{ fontSize: 11, flex: '0 0 190px', lineHeight: '20px' }}>{t.name}</Text>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Tooltip title={<span style={{ fontSize: 12 }}>{t.description}</span>}
          styles={{ root: { maxWidth: 620 } }}>
          <div style={{
            fontSize: 12, color: '#4e5969', lineHeight: 1.65,
            display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
          }}>{t.description}</div>
        </Tooltip>
      </div>
    </label>
  )
}

/** 一张「活」卡片。多选，勾上的活所需的工具自动并起来。 */
function ActivityCard({ p, checked, disabled, recommended, includedBy, capped, total, onToggle, onCopyPrompt }) {
  const [hover, setHover] = useState(false)
  return (
    <div onClick={() => !disabled && onToggle(p, !checked)}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}
      style={{
      position: 'relative',
      cursor: disabled ? 'default' : 'pointer', borderRadius: 10, padding: '12px 14px',
      transition: 'all .15s', opacity: disabled ? 0.55 : 1,
      border: checked ? '1.5px solid #0ea5a0' : '1px solid rgba(0,0,0,0.09)',
      background: checked ? 'rgba(14,165,160,0.05)' : includedBy ? 'rgba(14,165,160,0.02)' : 'rgba(255,255,255,0.5)',
      // 被别的活包含的，左边加一条色带 —— 让它在视觉上跟父档聚成一组。
      // 只挂一个小标签的话人扫过去看不见（实测：用户说"没看到已包含标签"）。
      borderLeft: includedBy ? '3px solid rgba(14,165,160,0.45)' : undefined,
    }}>
      {onCopyPrompt && (
        // 整张卡片本身是勾选控件，所以这个按钮：①放右上角不跟标题抢位置
        // ②hover 才显形，不勾选时几乎不可见 ③必须 stopPropagation，
        // 否则点"复制"会顺手把这一档勾上或取消掉。
        <Tooltip title="复制这一档的接入指令">
          <Button type="text" size="small" icon={<CopyOutlined />}
            onClick={e => { e.stopPropagation(); onCopyPrompt(p.prompt) }}
            style={{
              position: 'absolute', top: 6, right: 6, opacity: hover ? 1 : 0.35,
              transition: 'opacity .15s', color: checked ? '#0ea5a0' : '#86909c',
            }} />
        </Tooltip>
      )}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 9 }}>
        <Checkbox checked={checked} disabled={disabled} style={{ marginTop: 1, pointerEvents: 'none' }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 600, fontSize: 13, color: checked ? '#0ea5a0' : '#1d2129' }}>
              {p.label}
            </span>
            {recommended && (
              <Tag color="green" style={{ fontSize: 11, lineHeight: '16px', margin: 0, padding: '0 5px' }}>常用</Tag>
            )}
            {/* 没勾但工具已被别的活全带进来了。**要说清被谁包含** ——
                只写「已包含」的话，人还得自己猜是哪一件（实测被问了：
                「第一个是包含后面的 4 个吗？」）。 */}
            {includedBy && (
              <Tag color="cyan" style={{ fontSize: 11, lineHeight: '18px', margin: 0, padding: '0 7px', fontWeight: 500 }}>
                ↳ 已包含在「{includedBy}」里
              </Tag>
            )}
            {/* 勾了也拿不到全部：项目范围（天花板）没给那几个。**得先说出来** ——
                否则勾完发现「生效」比卡片上写的少，人只会以为页面算错了。 */}
            {capped != null && (
              <Tag color="warning" style={{ fontSize: 11, lineHeight: '18px', margin: 0, padding: '0 7px', fontWeight: 500 }}>
                项目范围内只有 {capped} / {p.tools.length}
              </Tag>
            )}
          </div>
          <div style={{ fontSize: 12, color: '#4e5969', lineHeight: 1.65, marginTop: 3 }}>{p.task}</div>
          <div style={{ fontSize: 12, color: '#86909c', marginTop: 5 }}>
            要用到 {p.tools.length} / {total} 个工具
          </div>
        </div>
      </div>
    </div>
  )
}

/** 从落库的工具清单反推「当初选了哪几件活」。
 *
 * 库里存的是展开后的显式工具名（不存档位名 —— 存了的话日后改档位定义，
 * 已有项目的范围会悄悄变）。所以进页面要反推一次：取所有"工具被完全包含"的活，
 * 再去掉被别的活套住的（选了全链路就不用再标用例档），剩下的就是当初那几件。
 */
function deriveChosen(profiles, toolNames) {
  const has = new Set(toolNames)
  const acts = profiles.filter(p => p.tools)
  const fit = acts.filter(p => p.tools.every(n => has.has(n)))
  // 被别人套住的丢掉：只留"极大"的那几件
  return fit
    .filter(p => !fit.some(o => o.key !== p.key && p.tools.every(n => o.tools.includes(n))))
    .map(p => p.key)
}

/** 「当初勾过、后来这档长大了」——落库存的是展开后的工具名，平台新增的工具不会自动进来。
 *
 * ⚠ 判据不能用 `deriveChosen`：它要求**完全覆盖**才算勾选，档位一缺工具就掉出
 * chosen，那样过期永远算不出来（第一版就是这么写错的）。改成按覆盖率判：
 * 覆盖 ≥70% 却没覆盖满的，几乎一定是"当初勾过、后来这档长大了"。
 *
 * 项目范围和每把 Key 都会过期（Key 那边更隐蔽：项目补齐了、Key 没补，看起来像
 * "项目已经修好了"），所以这条规则只留这一份，两处共用。
 */
function staleFor(profiles, savedList) {
  if (!savedList) return { profiles: [], missing: [] }
  const stale = profiles.filter(p => p.tools).filter(p => {
    const miss = p.tools.filter(n => !savedList.includes(n))
    return miss.length > 0 && (p.tools.length - miss.length) / p.tools.length >= 0.7
  })
  return {
    profiles: stale,
    missing: [...new Set(stale.flatMap(p => p.tools.filter(n => !savedList.includes(n))))],
  }
}

/** 「全链路：从写用例到读报告」→「全链路」。卡片和提示里都只用短名。 */
const shortLabel = (p) => p.label.split(/[：:]/)[0]

const TAG_S = { fontSize: 11, lineHeight: '16px', padding: '0 6px', margin: 0 }

/** 工具明细（按分类折叠，可单独加减）。项目范围和 Key 范围两处共用同一份渲染。 */
function ToolDetail({ byCategory, selected, disabled, onToggle, onToggleCat, hint }) {
  const [openKeys, setOpenKeys] = useState([])
  const items = byCategory.map(([cat, list]) => {
    const names = list.map(t => t.name)
    const n = names.filter(x => selected.has(x)).length
    const full = n === names.length
    return {
      key: cat,
      label: (
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ width: 3, height: 15, borderRadius: 2, background: catHex(cat), flex: '0 0 auto' }} />
          <span onClick={e => e.stopPropagation()} style={{ display: 'inline-flex' }}>
            <Checkbox disabled={disabled} checked={full} indeterminate={n > 0 && !full}
              onChange={e => onToggleCat(list, e.target.checked)} />
          </span>
          <span style={{ fontWeight: 600, fontSize: 13, color: '#1d2129' }}>{cat}</span>
        </div>
      ),
      extra: (
        <span style={{
          fontSize: 12, fontWeight: 600, padding: '1px 9px', borderRadius: 10,
          color: full ? '#0ea5a0' : n ? '#fa8c16' : '#86909c',
          background: full ? 'rgba(14,165,160,0.1)' : n ? 'rgba(250,140,22,0.1)' : 'rgba(0,0,0,0.04)',
        }}>{n}/{list.length}</span>
      ),
      children: (
        <div>
          {list.map(t => (
            <ToolRow key={t.name} t={t} disabled={disabled}
              checked={selected.has(t.name)} onToggle={onToggle} />
          ))}
        </div>
      ),
      styles: { body: { padding: 0 } },
    }
  })
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
        <Text type="secondary" style={{ fontSize: 12 }}>{hint}</Text>
        <div style={{ flex: 1 }} />
        <Button size="small" type="text" onClick={() =>
          setOpenKeys(openKeys.length ? [] : byCategory.map(([c]) => c))}>
          {openKeys.length ? '全部收起' : '全部展开'}
        </Button>
      </div>
      <Collapse items={items} activeKey={openKeys} onChange={setOpenKeys}
        expandIconPosition="start" size="small" style={{ background: 'transparent' }} />
    </div>
  )
}

/**
 * 本项目的 MCP 工具范围。
 *
 * ## 打开这一页的人要决定的只有一件事：这个项目的 CC 允许干哪些活
 *
 * 他不认识那 42 个工具名，也不该认识。所以**「活」是主角，工具明细是结果** ——
 * 明细默认整块收起，是给想核对的人看的，不是让人从里面挑。
 *
 * 前面连改四版都是从数据模型出发（工具、集合、模式），每一版的毛病都记在这：
 * 1. 9 张档位卡**二选一** → 只能"全部"或"恰好某一档"，想在某档基础上多开一个做不到
 * 2. 勾选框藏在「只开放勾选的」模式后面 → 默认打开一个框都看不到，跟没改一样
 * 3. 42 条整段说明全铺开 → 一堵墙，滚四屏
 * 4. 「活」被塞进下拉菜单、只能单选，反而让人去下面 42 条里挑 —— 他不懂那些名字
 *
 * 另外：**没有一档覆盖"从头干到尾"**，想干整条链的人只能选「全量」，
 * 分档对他等于没发生。后端补了 `fullloop`（写用例→回填→跑→读报告→归因）。
 *
 * 多选的语义：勾上 = 这件活要能干（并上它的工具）；取消 = 去掉它的工具，
 * 于是别的活如果因此缺了工具，也会跟着自动取消 —— 这条自然成立，不用特判。
 */
function ScopePanel({ tools, byCategory, profiles, scope, keyCount, saving, onSave, onCopyPrompt, promptCards = [] }) {
  const { has } = usePermissions()
  const canManage = has(PERM.PROJECT_SETTINGS)
  const savedUnlimited = !scope?.allowedTools
  const savedList = scope?.allowedTools || []
  const allNames = tools.map(t => t.name)
  const [unlimited, setUnlimited] = useState(savedUnlimited)
  const [sel, setSel] = useState(savedUnlimited ? allNames : savedList)
  // 人**明确选了哪几件活**，单独记一份。
  // 一开始想省掉它、用"工具集合包含关系"反推，结果分不出「人自己勾的」和
  // 「被大档带进来的」：勾了「全链路」之后「用例」「UI 脚本」等自动显示成已勾，
  // 再去点「全链路」取消 —— 那几个子档还"勾着"，工具一个都减不掉，**按钮像坏了**。
  // 反推只用在初次加载（库里只存了工具清单，没存档位名）。
  const [chosen, setChosen] = useState(() => deriveChosen(profiles, savedUnlimited ? [] : savedList))
  const [showDetail, setShowDetail] = useState(false)
  // ⚠ 别用 useEffect 同步服务端那份（react-hooks/set-state-in-effect）。
  // 调用方给了 key（见 <ScopePanel key=...>），saved 变了整个组件重挂。

  const selSet = new Set(sel)
  const same = (a, b) => a.length === b.length && a.every(x => b.includes(x))
  const dirty = unlimited !== savedUnlimited || (!unlimited && !same(sel, savedList))
  const picked = unlimited ? tools.length : sel.length
  const acts = profiles.filter(p => p.tools)
  // 关掉「不限制」时落到哪儿：**不能原样留着 42 个全勾**。
  // 人关掉它就是想收窄，结果九件活全亮着、得先取消八件才够挑一件 —— 方向是反的。
  // 落到推荐的那条全链路（人再改），这是他多半想要的起点。
  const baseline = (profiles.find(p => p.key === 'fullloop') || acts[0])?.tools || allNames

  const chosenSet = new Set(chosen)
  const isOn = (p) => chosenSet.has(p.key)
  // 没被明确选、但工具已经被**某一件选中的活**全带进来了。
  // 返回那件活的短名（「全链路：从写用例到读报告」→「全链路」），标在卡片上 ——
  // 只说"已包含"不说被谁包含，人还得自己猜。
  const includedBy = (p) => {
    if (chosenSet.has(p.key)) return null
    if (!p.tools.every(n => selSet.has(n))) return null
    const parent = acts.find(o => chosenSet.has(o.key)
      && o.key !== p.key && p.tools.every(n => o.tools.includes(n)))
    return parent ? shortLabel(parent) : null
  }

  const toggleAct = (p, on) => {
    const next = on ? [...chosen, p.key] : chosen.filter(k => k !== p.key)
    setChosen(next)
    // 工具永远 = 选中那几件活的**并集**，重算而不是增量加减。
    // 增量减法会把公共工具（lum_list_projects 之类几乎每件活都要）顺手拿走，
    // 别的活悄悄就不完整了 —— 人只点了一下，坏的是别处。
    const keep = new Set(next)
    setSel([...new Set(acts.filter(o => keep.has(o.key)).flatMap(o => o.tools))])
  }

  // 在明细里单独加减工具之后，某件活可能就不完整了 —— 它得跟着取消勾选，
  // 否则页面会显示"这件活能干"而实际上缺工具。
  const syncChosen = (nextSel) => {
    const has = new Set(nextSel)
    setChosen(c => c.filter(k => {
      const p = acts.find(o => o.key === k)
      return p && p.tools.every(n => has.has(n))
    }))
  }
  const applySel = (fn) => setSel(v => { const n = fn(v); syncChosen(n); return n })
  const toggle = (name, on) =>
    applySel(v => on ? [...v, name] : v.filter(n => n !== name))
  const toggleCat = (items, on) => {
    const names = items.map(t => t.name)
    applySel(v => on ? [...new Set([...v, ...names])] : v.filter(n => !names.includes(n)))
  }


  // 名单过期（平台加了新工具、这份名单没跟上）判据见 staleFor —— Key 那边共用同一条
  const { profiles: staleProfiles, missing: staleMissing } = staleFor(
    profiles, savedUnlimited ? null : savedList)

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <div style={{ ...sectionTitle, fontSize: 15 }}>这个项目的 Claude Code 能干哪些活？</div>
        <Text type="secondary" style={{ fontSize: 12 }}>
          勾上的活所需要的工具会自动合并；范围外的工具 CC 看不到、也调不动。
          本项目 <b style={{ color: '#4e5969' }}>{keyCount}</b> 把 Key 都按它生效，改完立即生效，不用重新建 Key。
        </Text>
      </div>

      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
        gap: 10, marginBottom: 16, opacity: unlimited ? 0.5 : 1,
      }}>
        {acts.map(p => (
          <ActivityCard key={p.key} p={p} total={tools.length} disabled={unlimited}
            recommended={p.key === 'fullloop'} checked={isOn(p)} includedBy={includedBy(p)}
            onToggle={toggleAct}
            onCopyPrompt={p.prompt && onCopyPrompt && promptCards.includes(p.key) ? onCopyPrompt : null} />
        ))}
      </div>

      {staleMissing.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message={`平台新增了 ${staleMissing.length} 个工具，本项目的范围还没跟上`}
          description={<span style={{ fontSize: 12 }}>
            「{staleProfiles.map(shortLabel).join('」「')}」这{staleProfiles.length > 1 ? '几' : ''}档现在需要
            <b> {staleMissing.join('、')} </b>
            ，但它们不在已保存的名单里 —— <b>CC 现在看不到、也调不动</b>。
            落库存的是展开后的显式工具名，所以平台加了新工具不会自动进来。
            点下面的「保存」重新展开一次就好。
          </span>}
          action={canManage && <Button size="small" type="primary" loading={saving}
            onClick={() => onSave(unlimited ? null : [...new Set([...sel, ...staleMissing])])}>
            一键补齐并保存
          </Button>} />
      )}
      <Card size="small" style={{ ...cardStyle, background: 'rgba(14,165,160,0.035)', marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <div>
            <span style={{ fontSize: 22, fontWeight: 700, color: picked ? '#0ea5a0' : '#e8453c' }}>{picked}</span>
            <span style={{ fontSize: 13, color: '#8c919e' }}> / {tools.length} 个工具已开放</span>
          </div>
          <div style={{ width: 140, height: 4, borderRadius: 2, background: 'rgba(0,0,0,0.07)', overflow: 'hidden' }}>
            <div style={{
              height: '100%', width: `${(picked / Math.max(tools.length, 1)) * 100}%`,
              background: '#0ea5a0', transition: 'width .2s',
            }} />
          </div>
          <Button size="small" type="text" onClick={() => setShowDetail(v => !v)}>
            {showDetail ? '收起工具明细' : '查看工具明细'}
          </Button>
          <div style={{ flex: 1 }} />
          {/* 「不限制」不等于"把 42 个全勾上"：前者以后新增的工具自动包含，后者不会
              （落库 NULL vs 显式清单）。这个区别得写出来，别让人猜。 */}
          {canManage && (
            <Tooltip title="勾上之后，以后平台新增的工具也自动包含；关掉就按上面勾的活来">
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <Switch size="small" checked={unlimited}
                  onChange={v => {
                    setUnlimited(v)
                    setSel(v ? allNames : baseline)
                    setChosen(v ? [] : deriveChosen(profiles, baseline))
                  }} />
                <span style={{ fontSize: 12, color: unlimited ? '#0ea5a0' : '#8c919e' }}>不限制</span>
              </span>
            </Tooltip>
          )}
          {dirty && (
            <Space size={8}>
              <Button size="small" onClick={() => {
                setUnlimited(savedUnlimited)
                setSel(savedUnlimited ? allNames : savedList)
                setChosen(deriveChosen(profiles, savedUnlimited ? [] : savedList))
              }}>放弃修改</Button>
              {canManage && (
                <Button size="small" type="primary" loading={saving}
                  disabled={!unlimited && sel.length === 0}
                  onClick={() => onSave(unlimited ? null : sel)}>保存</Button>
              )}
            </Space>
          )}
        </div>
        {unlimited && (
          <div style={{ marginTop: 8, fontSize: 12, color: '#8c919e' }}>
            当前不限制，所以上面的活全部置灰 —— 关掉右边的「不限制」就能按活来选。
          </div>
        )}
        {!unlimited && sel.length === 0 && (
          <div style={{ marginTop: 8, fontSize: 12, color: '#e8453c' }}>
            一件活都没勾 —— 那样 CC 连不上任何工具，等于把这个项目的 MCP 关了。
          </div>
        )}
      </Card>

      {/* 明细是给想核对的人看的，默认整块收起。也能在这里单独加减某个工具。 */}
      {showDetail && (
        <ToolDetail byCategory={byCategory} selected={selSet} disabled={unlimited}
          onToggle={toggle} onToggleCat={toggleCat}
          hint="按上面的活自动勾好的。要单独加减某个工具，展开分类改就行。" />
      )}
    </div>
  )
}

/**
 * 一把 Key 的工具范围。**默认「跟随项目范围」** —— 那是今天所有 Key 的状态，
 * 也是绝大多数时候正确的选择；收窄是为"这台 CC 只做归因、那台只做回推"准备的。
 *
 * ## 三个数字不是一回事，别合成一句
 *   · 勾了几个   —— 人自己挑的
 *   · 生效几个   —— **项目范围 ∩ 勾的**（判据在后端 `pick_scope`，这里只是同一条的呈现）
 *   · 被挡几个   —— 勾了但项目范围（天花板）没给。**必须单独说**：不说的话人看到的是
 *     "我勾了 12 个、页面显示生效 8 个"，而少工具在 CC 那边只表现成「平台没有这个工具」，
 *     然后它会挑一个别的工具凑，排查要绕一大圈才回到这里。
 *
 * ⚠ **「跟随项目」和「勾了全部工具」不是一回事**：前者以后项目范围放宽会自动跟上，
 * 后者落库是一份定死的清单（这也是名单会过期的原因，见 staleFor）。
 *
 * 交互照抄项目那一页（「活」是主角、工具明细默认收起）—— 四次走弯路的记录在
 * docs/cc-platform-loop-spec.md，别再另设一套。
 */
function KeyScopePicker({ tools, byCategory, profiles, projectScope, value, onChange }) {
  const allNames = tools.map(t => t.name)
  const acts = profiles.filter(p => p.tools)
  // value === null 就是「跟随项目」。**不能用 `!value`** —— `[]` 是"一个都不给"，
  // 那条路会把它滑成"跟随项目"，方向正好反（后端 pick_scope 的注释里同一个坑）。
  const narrow = value !== null && value !== undefined
  const sel = narrow ? value : []
  const selSet = new Set(sel)
  const [chosen, setChosen] = useState(() => deriveChosen(profiles, sel))
  const [showDetail, setShowDetail] = useState(false)

  const ceiling = projectScope ? new Set(projectScope) : null
  const inCeiling = (n) => !ceiling || ceiling.has(n)
  const eff = narrow ? sel.filter(inCeiling) : (projectScope || allNames)
  const blocked = narrow ? sel.filter(n => !inCeiling(n)) : []
  const stale = staleFor(profiles, narrow ? sel : null)

  const chosenSet = new Set(chosen)
  const includedBy = (p) => {
    if (chosenSet.has(p.key)) return null
    if (!p.tools.every(n => selSet.has(n))) return null
    const parent = acts.find(o => chosenSet.has(o.key)
      && o.key !== p.key && p.tools.every(n => o.tools.includes(n)))
    return parent ? shortLabel(parent) : null
  }

  const toggleAct = (p, on) => {
    const next = on ? [...chosen, p.key] : chosen.filter(k => k !== p.key)
    setChosen(next)
    // 工具永远 = 选中那几件活的并集，重算而不是增量加减（增量减法会把公共工具
    // 顺手拿走，别的活悄悄就不完整了 —— 人只点了一下，坏的是别处）。
    const keep = new Set(next)
    onChange([...new Set(acts.filter(o => keep.has(o.key)).flatMap(o => o.tools))])
  }

  // 明细里单独加减之后，某件活可能就不完整了 —— 它得跟着取消勾选，
  // 否则卡片上写着"这件活能干"而实际缺工具。
  const applySel = (fn) => {
    const next = fn(sel)
    const has = new Set(next)
    setChosen(c => c.filter(k => {
      const a = acts.find(o => o.key === k)
      return a && a.tools.every(n => has.has(n))
    }))
    onChange(next)
  }
  const toggle = (name, on) =>
    applySel(v => on ? [...v, name] : v.filter(n => n !== name))
  const toggleCat = (list, on) => {
    const names = list.map(t => t.name)
    applySel(v => on ? [...new Set([...v, ...names])] : v.filter(n => !names.includes(n)))
  }

  const setNarrow = (v) => {
    if (!v) { setChosen([]); onChange(null); return }
    // 切到「收窄」的起点：**不能原样留着项目范围全勾**（那等于没收窄，人还得先
    // 取消一大批，方向是反的）。落到能完整装进项目天花板的那件最大的活；
    // 天花板本身很窄、一件都装不下时退回天花板本身，至少不是空的。
    const fits = acts.filter(a => a.tools.every(inCeiling))
    const baseline = (fits.find(a => a.key === 'fullloop')
      || [...fits].sort((a, b) => b.tools.length - a.tools.length)[0])?.tools
      || projectScope || allNames
    setChosen(deriveChosen(profiles, baseline))
    onChange(baseline)
  }

  return (
    <div>
      <Segmented value={narrow ? 'narrow' : 'follow'} block
        onChange={v => setNarrow(v === 'narrow')}
        options={[
          { label: '跟随项目范围（推荐）', value: 'follow' },
          { label: '这把 Key 只干几件活', value: 'narrow' },
        ]} />

      {!narrow ? (
        <div style={{
          marginTop: 12, fontSize: 12, color: '#4e5969', background: 'rgba(14,165,160,0.06)',
          border: '1px solid rgba(14,165,160,0.18)', borderRadius: 10, padding: '8px 12px', lineHeight: 1.8,
        }}>
          它能用本项目范围内的全部工具
          {projectScope ? `（当前 ${projectScope.length} / ${tools.length} 个）` : '（本项目当前不限制）'}
          。以后项目范围放宽，这把 Key <b>自动跟上</b>。
          只有"这台 CC 专做一件事、不想让它在几十个工具里挑"时才需要收窄。
        </div>
      ) : (
        <div style={{ marginTop: 14 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            勾上的活所需要的工具会自动合并。收窄只是<b>少露出几个工具</b>（让它少挑错、少占上下文），
            <b>不是降权</b> —— 能读写哪个项目的数据仍然只由归属项目决定。
          </Text>
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))',
            gap: 10, margin: '12px 0',
          }}>
            {acts.map(a => {
              const inside = a.tools.filter(inCeiling).length
              return (
                <ActivityCard key={a.key} p={a} total={tools.length}
                  recommended={a.key === 'fullloop'} checked={chosenSet.has(a.key)}
                  includedBy={includedBy(a)}
                  capped={inside < a.tools.length ? inside : null}
                  onToggle={toggleAct} />
              )
            })}
          </div>

          {stale.missing.length > 0 && (
            <Alert type="warning" showIcon style={{ marginBottom: 12 }}
              message={`平台新增了 ${stale.missing.length} 个工具，这把 Key 的名单还没跟上`}
              description={<span style={{ fontSize: 12 }}>
                「{stale.profiles.map(shortLabel).join('」「')}」现在还需要
                <b> {stale.missing.join('、')} </b>
                。落库存的是展开后的工具名，所以平台加了新工具不会自动进来 ——
                <b>项目范围补齐了也不算</b>，生效是两层的交集。
              </span>}
              action={<Button size="small" type="primary"
                onClick={() => applySel(v => [...new Set([...v, ...stale.missing])])}>
                补齐这几个
              </Button>} />
          )}

          <Card size="small" style={{ ...cardStyle, background: 'rgba(14,165,160,0.035)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
              <div>
                <span style={{ fontSize: 20, fontWeight: 700, color: eff.length ? '#0ea5a0' : '#e8453c' }}>
                  {eff.length}
                </span>
                <span style={{ fontSize: 13, color: '#8c919e' }}> / {tools.length} 个工具生效</span>
              </div>
              {sel.length !== eff.length && (
                <Text type="secondary" style={{ fontSize: 12 }}>（勾了 {sel.length} 个）</Text>
              )}
              <div style={{ flex: 1 }} />
              <Button size="small" type="text" onClick={() => setShowDetail(v => !v)}>
                {showDetail ? '收起工具明细' : '查看工具明细'}
              </Button>
            </div>
            {blocked.length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: '#fa8c16', lineHeight: 1.8 }}>
                有 <b>{blocked.length}</b> 个被项目范围挡住了：<b>{blocked.join('、')}</b> ——
                生效范围是「项目范围 ∩ 这一份」，Key 只能更窄、不能反过来扩出项目天花板。
                真要用它们，先去「工具范围」页签把<b>项目</b>范围放宽。
              </div>
            )}
            {eff.length === 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: '#e8453c', lineHeight: 1.8 }}>
                一个工具都不生效 —— 这把 Key 连上来什么都干不了。
                {sel.length > 0 && '（勾的那几个全在项目范围外）'}
              </div>
            )}
          </Card>

          {showDetail && (
            <div style={{ marginTop: 12 }}>
              <ToolDetail byCategory={byCategory} selected={selSet}
                onToggle={toggle} onToggleCat={toggleCat}
                hint="按上面的活自动勾好的。要单独加减某个工具，展开分类改就行。" />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Key 那一行的范围标签。**四件事分四个标签，不合成一句** ——
 * 合起来就没法扫一眼判断"这把要不要动它"。 */
function KeyScopeTags({ sc, stale }) {
  // 旧后端没有这个字段（后端故意不带 --reload，改完必须重启）。这里静默不显示，
  // 别渲染成「0 / 0 工具」—— 那和"这把 Key 什么都干不了"长得一模一样。
  if (!sc) return null
  const unlimited = sc.effectiveTools === null
  return (
    <>
      <Tooltip title={unlimited
        ? '两层范围都没设，所以不限制：平台以后新增的工具它也自动能用。'
        : `实际能看到 ${sc.effectiveCount} 个工具（生效 = 项目范围 ∩ 本 Key 那份）。`
          + (sc.followsProject
            ? '这把 Key 自己没收窄，跟着项目范围走 —— 改「工具范围」页签即可。'
            : '这把 Key 自己另挑了一份，改它点右边的「改范围」。')}>
        <Tag color={unlimited ? 'default' : sc.effectiveCount === 0 ? 'error' : 'processing'} style={TAG_S}>
          {unlimited ? '全部工具' : `${sc.effectiveCount}/${sc.totalTools}`}
          {' · '}{sc.followsProject ? '跟随项目' : '本 Key 收窄'}
        </Tag>
      </Tooltip>
      {sc.blockedByProject?.length > 0 && (
        <Tooltip title={`这把 Key 勾了 ${sc.blockedByProject.join('、')}，但项目范围没给 ——`
          + '生效是两层的交集，所以它们连不上。要放开得先改项目范围。'}>
          <Tag color="warning" style={TAG_S}>{sc.blockedByProject.length} 个被项目挡住</Tag>
        </Tooltip>
      )}
      {stale?.missing.length > 0 && (
        <Tooltip title={`平台新增的 ${stale.missing.join('、')} 不在这把 Key 的名单里 ——`
          + '落库存的是展开后的工具名，项目范围补齐了也不会自动进来。点「改范围」里的「补齐这几个」。'}>
          <Tag color="warning" style={TAG_S}>名单缺 {stale.missing.length} 个新工具</Tag>
        </Tooltip>
      )}
      {sc.staleTools?.length > 0 && (
        <Tooltip title={`${sc.staleTools.join('、')} 已经不在平台的工具清单里（改名或下线），存着不生效。`}>
          <Tag style={TAG_S}>{sc.staleTools.length} 个已下线</Tag>
        </Tooltip>
      )}
    </>
  )
}

export default function MCPTools() {
  const { projectId } = useParams()
  const { has } = usePermissions()
  const canManage = has(PERM.PROJECT_SETTINGS)
  const [branchId] = useBranch(projectId)
  const mcpUrl = `http://${window.location.hostname}:18800/mcp/`
  const [apiKeys, setApiKeys] = useState([])
  const [tools, setTools] = useState([])
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [newKeyName, setNewKeyName] = useState('')
  const [newKeyResult, setNewKeyResult] = useState(null)
  // null = 跟随项目（默认档）。**不能用 `[]` 当默认** —— 空数组是"一个工具都不给"，
  // 那样默认建出来的 Key 连上来什么都干不了，而页面上看不出区别。
  const [newKeyTools, setNewKeyTools] = useState(null)
  const [creating, setCreating] = useState(false)
  const [editKey, setEditKey] = useState(null)      // 正在改范围的那把 Key
  const [editTools, setEditTools] = useState(null)
  const [savingKeyScope, setSavingKeyScope] = useState(false)
  const [profiles, setProfiles] = useState([])
  const [scope, setScope] = useState(null)          // 本项目的工具范围
  const [savingScope, setSavingScope] = useState(false)

  useEffect(() => {
    if (!projectId) return
    fetchKeys(); fetchTools(); fetchProfiles(); fetchScope()
  }, [projectId, branchId])
  const fetchKeys = async () => { try { setApiKeys((await api.get('/mcp-keys')).data || []) } catch { /* 拦截器已弹错，这里不重复报 */ } }
  // 工具目录来自后端注册表，不再前端硬编码（曾经写死 20 条、后端实际 32 条）
  const fetchTools = async () => { try { setTools((await api.get('/mcp-keys/tools')).data || []) } catch { /* 同上 */ } }
  const fetchProfiles = async () => {
    try {
      // 带上项目/分支/地址：后端据此把接入指令里的上下文填好。
      // **指令正文一律后端拼**——前端自己拼一份的话，改了档位说明忘了改模板，
      // 页面上写的和复制出去的就成了两回事，而没人会把两处对着看。
      const q = new URLSearchParams({ mcpUrl })
      if (projectId) q.set('projectId', projectId)
      if (branchId) q.set('branchId', branchId)
      const d = (await api.get(`/mcp-keys/profiles?${q}`)).data || {}
      setProfiles(d.profiles || [])
    } catch { /* 拉不到就只剩工具列表，不至于开天窗 */ }
  }
  const fetchScope = async () => {
    try { setScope((await api.get(`/projects/${projectId}/mcp-scope`)).data) } catch { /* 同上 */ }
  }

  const byCategory = useMemo(() => {
    const m = new Map()
    tools.forEach(t => { if (!m.has(t.category)) m.set(t.category, []); m.get(t.category).push(t) })
    return [...m.entries()]
  }, [tools])

  // 本项目的 Key 和「还没归属项目」的旧 Key 分开列 —— 后者不受项目范围管，
  // 混在一起会让人以为改了范围它也跟着变了。
  const projectKeys = apiKeys.filter(k => k.projectId === projectId)
  const orphanKeys = apiKeys.filter(k => !k.projectId)

  // 生效 = 项目范围 ∩ 这一份（判据在后端 `pick_scope`，这里只是同一条的呈现）。
  // 两层都不限时回**全量**而不是 0 —— 回 0 的话「生效 0」和"什么都干不了"分不开。
  const effCount = (list) => {
    const ceil = scope?.allowedTools || null
    if (list === null || list === undefined) return ceil ? ceil.length : tools.length
    return ceil ? list.filter(n => ceil.includes(n)).length : list.length
  }
  const createEff = effCount(newKeyTools)

  const saveScope = async (toolNames) => {
    setSavingScope(true)
    try {
      // 档位只是快捷方式，落库存展开后的显式工具名（不存档位名，
      // 否则日后改了档位定义，已有项目的范围会悄悄变）。全量档存 null。
      await api.put(`/projects/${projectId}/mcp-scope`,
        toolNames ? { allowedTools: toolNames } : { resetTools: true })
      message.success(`已保存，本项目 ${scope?.keyCount ?? 0} 把 Key 立即生效`)
      await fetchScope()
    } catch (e) { message.error(e.message || '保存失败') } finally { setSavingScope(false) }
  }

  const createKey = async () => {
    setCreating(true)
    try {
      // 不带 allowedTools = 跟随项目（默认档）。判据写成 `!== null`，
      // 因为要区分的是"没设"和"设成空"这两件事，而不是真假 —— 空清单是
      // "一个工具都不给"，得原样发上去。后端那边同一个坑：曾经
      // `[...] if raw else None` 把 `[]` 当成"不限制"，方向整个反过来。
      const body = { name: newKeyName || 'default', projectId }
      if (newKeyTools !== null) body.allowedTools = newKeyTools
      setNewKeyResult((await api.post('/mcp-keys', body)).data)
      setNewKeyName(''); fetchKeys(); fetchScope()
    }
    catch (e) { message.error(e.message || '创建失败') } finally { setCreating(false) }
  }
  const saveKeyScope = async () => {
    setSavingKeyScope(true)
    try {
      // `resetTools` 是唯一一条"改回跟随项目"的路：JSON 里的 null 分不出
      // "这个字段不改"和"改成跟随"，所以必须有个显式开关。
      await api.patch(`/mcp-keys/${editKey.id}`,
        editTools === null ? { resetTools: true } : { allowedTools: editTools })
      message.success('已保存，这把 Key 立即生效')
      setEditKey(null); fetchKeys()
    } catch (e) { message.error(e.message || '保存失败') } finally { setSavingKeyScope(false) }
  }

  const revokeKey = async (id) => { try { await api.delete(`/mcp-keys/${id}`); message.success('已吊销'); fetchKeys(); fetchScope() } catch { message.error('吊销失败') } }

  const adoptKey = async (id) => {
    try {
      await api.patch(`/mcp-keys/${id}`, { projectId })
      message.success('已归到本项目，生效范围 = 项目范围 ∩ 这把 Key 那份')
      fetchKeys(); fetchScope()
    } catch (e) { message.error(e.message || '操作失败') }
  }

  const copy = (text) => copyToClipboard(text).then(() => message.success('已复制'))

  // 复制接入指令。提示语要回答用户拿到这段话之后的那个问题：「然后呢，粘哪儿？」
  // —— 这是新用户唯一真会卡住的地方，一句话就够。
  const copyPrompt = (text) => copyToClipboard(text).then(
    () => message.success({ content: '已复制，粘到你项目里的 Claude Code 就行', duration: 4 })
  )
  const fullloopPrompt = (profiles.find(p => p.key === 'fullloop') || {}).prompt
  // 只有这三档给卡片级入口：它们是"能直接开干"的三种活。10 张卡片全给按钮的话，
  // 10 个同名按钮长得一样、复制内容却不一样，等于给用户出了道原本不存在的选择题。
  const PROMPT_CARDS = ['fullloop', 'live', 'triage']

  const onlineCount = projectKeys.filter(k => k.lastUsedAt && Date.now() - new Date(k.lastUsedAt).getTime() < 30 * 60 * 1000).length
  const mcpConfig = JSON.stringify({ mcpServers: { lumiere: { type: "streamable-http", url: mcpUrl, headers: { Authorization: "Bearer <你的API Key>" } } } }, null, 2)

  return (
    <div style={{ maxWidth: 960 }}>
      {/* ── 页头 ── */}
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: '0 0 6px', color: '#1d2129' }}>
          <LinkOutlined style={{ marginRight: 8, color: '#0ea5a0' }} />MCP 工具中心
        </h2>
        <Text type="secondary" style={{ fontSize: 13 }}>管理 Claude Code 与平台的连接，查看可用的 AI 工具</Text>
      </div>

      {/* ── 服务地址（独立突出） ── */}
      <Card size="small" style={{ ...cardStyle, marginBottom: 16, borderLeft: '3px solid #0ea5a0' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 12, color: '#8c919e', marginBottom: 2 }}>MCP 服务地址</div>
            <span style={{ fontSize: 16, fontFamily: 'var(--font-mono)', fontWeight: 500, color: '#1d2129', letterSpacing: 0.3 }}>
              {mcpUrl}
            </span>
          </div>
          <Space size={16}>
            {/* 页面到这儿本来是断的：用户看完 10 张卡片知道了"开放了哪些工具"，
                然后合上页面、回到自己的终端，面对空白 prompt 还得自己想怎么说。
                指令解决的就是这一句开头 —— 地址、项目分支、这档要干的活，四行。
                **纪律不在里面**（原来抄了三组八条，1075 字）：它们在 MCP
                instructions 里，CC 一连上就读，不用人记得粘贴；细流程在各工具的
                描述和返回值里（lum_next_duty 直接告诉它下一步调谁）。 */}
            <Tooltip title="复制一段可以直接粘给 Claude Code 的话：连接地址 + 项目分支 + 这次要干的活">
              <Button size="small" type="primary" icon={<CopyOutlined />}
                disabled={!fullloopPrompt}
                onClick={() => copyPrompt(fullloopPrompt)}>复制接入指令</Button>
            </Tooltip>
            <Button size="small" icon={<CopyOutlined />} onClick={() => copy(mcpUrl)}>复制地址</Button>
            <Space split={<span style={{ color: '#e0e0e3' }}>|</span>}>
              <Text type="secondary" style={{ fontSize: 12 }}>{onlineCount}/{projectKeys.length} 在线</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>{tools.length} 个工具</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>StreamableHTTP</Text>
            </Space>
          </Space>
        </div>
      </Card>

      {/* ── 主体 Tab ── */}
      <Tabs defaultActiveKey="connections" items={[
        {
          key: 'connections',
          label: <span><KeyOutlined /> 连接管理 {onlineCount > 0 && <Badge count={onlineCount} size="small" style={{ marginLeft: 4 }} />}</span>,
          children: (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                <Text type="secondary" style={{ fontSize: 13 }}>
                  每个 Claude Code 用独立 API Key 连接。生效范围 = <b>项目范围 ∩ 这把 Key 那份</b>：
                  项目那份是天花板（在「工具范围」页签改，本项目所有 Key 跟着变），
                  单把 Key 可以再收窄成"只干几件活"。默认<b>跟随项目</b>，不必逐把去设。
                </Text>
                {canManage && (
                  <Button type="primary" icon={<PlusOutlined />} onClick={() => { setCreateModalOpen(true); setNewKeyResult(null); setNewKeyName(''); setNewKeyTools(null) }}>创建 Key</Button>
                )}
              </div>

              {projectKeys.length > 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {projectKeys.map(k => {
                    const lastUsed = k.lastUsedAt ? new Date(k.lastUsedAt) : null
                    const isOnline = lastUsed && (Date.now() - lastUsed.getTime() < 30 * 60 * 1000)
                    const isRecent = lastUsed && (Date.now() - lastUsed.getTime() < 24 * 60 * 60 * 1000)
                    return (
                      <Card key={k.id} size="small" style={{
                        ...cardStyle,
                        borderLeft: `3px solid ${isOnline ? '#0ea5a0' : isRecent ? '#faad14' : '#e8e8e8'}`,
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                            <div style={{
                              width: 36, height: 36, borderRadius: 12,
                              background: isOnline ? 'rgba(14,165,160,0.08)' : isRecent ? 'rgba(250,173,20,0.08)' : 'rgba(0,0,0,0.03)',
                              display: 'flex', alignItems: 'center', justifyContent: 'center',
                            }}>
                              <RobotOutlined style={{ fontSize: 18, color: isOnline ? '#0ea5a0' : isRecent ? '#faad14' : '#bfc4cd' }} />
                            </div>
                            <div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span style={{ fontSize: 14, fontWeight: 600, color: '#1d2129' }}>{k.name}</span>
                                <Text code style={{ fontSize: 11, color: '#8c919e' }}>{k.prefix}...</Text>
                                {isOnline && <Tag color="cyan" style={{ fontSize: 11, lineHeight: '16px', padding: '0 6px', margin: 0 }}>在线</Tag>}
                                {!isOnline && isRecent && <Tag color="warning" style={{ fontSize: 11, lineHeight: '16px', padding: '0 6px', margin: 0 }}>最近活跃</Tag>}
                                {/* 范围由后端算好回来（生效 / 被项目挡掉 / 名单过期 分开说）——
                                    前端自己拿 scope 和 k.allowedTools 再算一遍的话，两处口径迟早分叉。 */}
                                <KeyScopeTags sc={k.scope} stale={staleFor(profiles, k.allowedTools)} />
                              </div>
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                {/* 时间格式走全站统一的 formatTime，别再各页一套 toLocaleString */}
                                {lastUsed ? `最近调用 ${formatTime(lastUsed)}` : '尚未使用'}
                              </Text>
                            </div>
                          </div>
                          <Space size={4}>
                            {canManage && (
                              <Button size="small" type="text"
                                onClick={() => { setEditKey(k); setEditTools(k.allowedTools ?? null) }}>
                                改范围
                              </Button>
                            )}
                            {canManage && (
                              <Popconfirm title="吊销后该连接立即失效" onConfirm={() => revokeKey(k.id)} okText="吊销" cancelText="取消" okButtonProps={{ danger: true }}>
                                <Button size="small" danger type="text" icon={<DeleteOutlined />}>吊销</Button>
                              </Popconfirm>
                            )}
                          </Space>
                        </div>
                      </Card>
                    )
                  })}
                </div>
              ) : (
                <div style={{ textAlign: 'center', padding: '60px 0', color: '#bfc4cd' }}>
                  <RobotOutlined style={{ fontSize: 36, marginBottom: 12 }} />
                  <div style={{ fontSize: 14 }}>本项目还没有连接</div>
                  <div style={{ fontSize: 12, marginTop: 4 }}>点击「创建 Key」添加 Claude Code 连接</div>
                </div>
              )}

              {/* 范围挪到项目级之前建的 Key 没有归属项目。它们**不受本项目范围管**，
                  单独列出来说清楚 —— 混进上面那一堆里，人会以为改了范围它们也跟着变。
                  不自动认领：猜错项目等于静默改权限。 */}
              {orphanKeys.length > 0 && (
                <div style={{ marginTop: 26 }}>
                  <div style={{ ...sectionTitle }}>未归属项目的 Key（{orphanKeys.length}）</div>
                  <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 10 }}>
                    这些是范围改成项目级之前建的，<b>头上没有项目天花板</b> —— 它自己那份清单就是最终范围。
                    归到本项目后生效范围变成「本项目范围 ∩ 它自己那份」，<b>可能比现在窄</b>。
                  </Text>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {orphanKeys.map(k => (
                      <Card key={k.id} size="small" style={{ ...cardStyle, borderLeft: '3px solid #e8e8e8' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                          <Space size={10}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: '#1d2129' }}>{k.name}</span>
                            <Text code style={{ fontSize: 11, color: '#8c919e' }}>{k.prefix}...</Text>
                            <KeyScopeTags sc={k.scope} stale={staleFor(profiles, k.allowedTools)} />
                          </Space>
                          <Space size={4}>
                            {canManage && (
                              <Popconfirm title="归到本项目后，生效范围 = 本项目范围 ∩ 它自己那份，可能比现在窄" onConfirm={() => adoptKey(k.id)} okText="归属" cancelText="取消">
                                <Button size="small" type="text">归到本项目</Button>
                              </Popconfirm>
                            )}
                            {canManage && (
                              <Popconfirm title="吊销后该连接立即失效" onConfirm={() => revokeKey(k.id)} okText="吊销" cancelText="取消" okButtonProps={{ danger: true }}>
                                <Button size="small" danger type="text" icon={<DeleteOutlined />}>吊销</Button>
                              </Popconfirm>
                            )}
                          </Space>
                        </div>
                      </Card>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ),
        },
        {
          key: 'tools',
          label: <span><ThunderboltOutlined /> 工具范围 ({scope?.allowedTools ? `${scope.allowedTools.length}/${tools.length}` : tools.length})</span>,
          children: (
            <ScopePanel
              key={scope ? (scope.allowedTools ? scope.allowedTools.join(',') : 'unlimited') : 'loading'}
              tools={tools} byCategory={byCategory} profiles={profiles}
              scope={scope} keyCount={scope?.keyCount ?? projectKeys.length}
              saving={savingScope} onSave={saveScope}
              onCopyPrompt={copyPrompt} promptCards={PROMPT_CARDS} />
          ),
        },
        {
          key: 'guide',
          label: <span><ApiOutlined /> 配置指南</span>,
          children: (
            <div style={{ maxWidth: 680 }}>
              {[
                { num: '1', title: '创建 API Key', desc: '在「连接管理」Tab 点击「创建 Key」，复制保存密钥。' },
                { num: '2', title: '添加 .mcp.json 配置', desc: '将以下内容合并到项目根目录的 .mcp.json 文件：', code: mcpConfig },
                { num: '3', title: '在 Claude Code 中使用', desc: '重启 Claude Code，然后直接用自然语言：', examples: [
                  { hint: '接着上次没干完的活（工具会回 owes，说每条还欠哪一维）', cmd: '订阅管理这个模块还欠哪些用例？挑核心的补上，接口场景亲手跑通再回推' },
                  { hint: '这一轮该干什么（归因 / 复跑 / 补场景 / 自证四个队列）', cmd: '看看这个分支现在该干什么' },
                  { hint: '交付前自己过一遍门禁', cmd: '这个模块能交付了吗？有阻塞的话说清卡在哪一步' },
                ] },
              ].map((step) => (
                <div key={step.num} style={{ marginBottom: 28 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                    <div style={{
                      width: 28, height: 28, borderRadius: '50%', flexShrink: 0,
                      background: 'linear-gradient(135deg, #0ea5a0, #7cacf8)', color: '#fff',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 13, fontWeight: 600,
                    }}>{step.num}</div>
                    <span style={sectionTitle}>{step.title}</span>
                  </div>
                  <div style={{ marginLeft: 38 }}>
                    <Text type="secondary" style={{ fontSize: 13 }}>{step.desc}</Text>
                    {step.code && (
                      <div style={{ position: 'relative', marginTop: 8 }}>
                        <pre style={{
                          background: 'rgba(0,0,0,0.02)', border: '1px solid rgba(0,0,0,0.06)',
                          borderRadius: 12, padding: '14px 18px', fontSize: 12,
                          fontFamily: 'var(--font-mono)', overflow: 'auto', lineHeight: 1.6,
                        }}>{step.code}</pre>
                        <Button size="small" icon={<CopyOutlined />} style={{ position: 'absolute', top: 10, right: 10 }}
                          onClick={() => copy(step.code)}>复制</Button>
                      </div>
                    )}
                    {step.examples && (
                      <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {step.examples.map((ex, i) => (
                          <Card key={i} size="small" style={{ ...cardStyle, borderLeft: '3px solid #7cacf8' }}>
                            <div style={{ fontSize: 11, color: '#8c919e', marginBottom: 2 }}>{ex.hint}</div>
                            <div style={{ fontSize: 13, fontFamily: 'var(--font-mono)' }}>{ex.cmd}</div>
                          </Card>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ),
        },
      ]} />

      {/* 创建 Key 弹窗 */}
      <Modal title="创建连接" open={createModalOpen} onCancel={() => setCreateModalOpen(false)} width={760}
        footer={newKeyResult ? [
          <Button key="close" type="primary" onClick={() => setCreateModalOpen(false)}>我已保存，关闭</Button>
        ] : [
          <Button key="cancel" onClick={() => setCreateModalOpen(false)}>取消</Button>,
          canManage ? (
            // 生效 0 个就别让它建出来：这种 Key 连得上、但一个工具都调不到，
            // 在 CC 那边表现成"平台什么都没开放"，排查要绕一大圈才回到这里。
            <Tooltip key="create" title={createEff === 0
              ? '这样建出来的 Key 一个工具都拿不到，连上来什么也干不了'
              : ''}>
              <span>
                <Button type="primary" icon={<PlusOutlined />} onClick={createKey}
                  loading={creating} disabled={createEff === 0}>创建</Button>
              </span>
            </Tooltip>
          ) : null,
        ]}>
        {!newKeyResult ? (
          <div>
            <Text type="secondary" style={{ fontSize: 13, display: 'block', marginBottom: 16 }}>
              给这个连接取个名字。这个名字会<b>直接显示在「操作日志」的操作人一列</b>
              （形如 <code>admin · CC · 小李的开发机</code>）—— Key 只能给自己建，
              所以归属人永远是你，<b>认得出是哪台 Claude Code 全靠这个名字</b>。
            </Text>
            <Input placeholder="如：小李的开发机、CI 流水线" value={newKeyName} onChange={e => setNewKeyName(e.target.value)} size="large" />

            {/* 范围仍然**默认跟随项目** —— 收窄是给"这台 CC 只做一件事"准备的选项，
                不是建 Key 的必答题。原来把"设权限"和"发钥匙"绑成必选，于是每换
                一次范围就多出一把 Key；现在默认档一眼可见，不想管的人直接建。 */}
            <div style={{ marginTop: 18 }}>
              <div style={{ ...sectionTitle, marginBottom: 10 }}>这把 Key 的工具范围</div>
              <KeyScopePicker tools={tools} byCategory={byCategory} profiles={profiles}
                projectScope={scope?.allowedTools || null}
                value={newKeyTools} onChange={setNewKeyTools} />
            </div>
          </div>
        ) : (
          <div>
            <div style={{ textAlign: 'center', padding: '20px 0 16px', marginBottom: 16, background: 'rgba(14,165,160,0.04)', borderRadius: 12 }}>
              <CheckCircleOutlined style={{ fontSize: 28, color: '#0ea5a0', marginBottom: 8 }} />
              <div style={{ fontWeight: 600, fontSize: 15 }}>创建成功</div>
              <Text type="secondary" style={{ fontSize: 12 }}>请立即复制密钥，关闭后不再显示</Text>
            </div>
            <Card size="small" style={cardStyle}>
              <Text code copyable style={{ fontSize: 13, wordBreak: 'break-all' }}>{newKeyResult.key}</Text>
            </Card>
            {/* 数字取**后端回的生效范围**，不是前端刚才算的那个 —— 落库时不存在的
                工具名会被丢掉（`_validate_tools`），前端那个数会偏大。 */}
            {newKeyResult.scope?.effectiveTools && (
              <Alert style={{ marginTop: 12 }} type="info" showIcon
                message={`该连接能用 ${newKeyResult.scope.effectiveCount} / ${newKeyResult.scope.totalTools} 个工具`}
                description={<span style={{ fontSize: 12 }}>
                  {newKeyResult.scope.followsProject
                    ? '它跟随本项目的工具范围，以后项目范围放宽会自动跟上（改「工具范围」页签）。'
                    : '这是这把 Key 自己那份，和项目范围取交集后的结果。改它去「连接管理」里点「改范围」。'}
                  范围外的工具不会出现在它的工具列表里，直接调用也会被拒绝。
                </span>} />
            )}
          </div>
        )}
      </Modal>

      {/* 改一把 Key 的范围。**每次都是新挂载**（editKey 为 null 时整块不渲染），
          所以 KeyScopePicker 内部那份「选了哪几件活」不会从上一把漏过来。 */}
      {editKey && (
        <Modal title={`「${editKey.name}」的工具范围`} open width={760}
          onCancel={() => setEditKey(null)}
          footer={[
            <Button key="cancel" onClick={() => setEditKey(null)}>取消</Button>,
            <Tooltip key="save" title={effCount(editTools) === 0
              ? '保存后这把 Key 一个工具都拿不到，连上来什么也干不了'
              : ''}>
              <span>
                <Button type="primary" onClick={saveKeyScope} loading={savingKeyScope}
                  disabled={effCount(editTools) === 0}>保存</Button>
              </span>
            </Tooltip>,
          ]}>
          <KeyScopePicker tools={tools} byCategory={byCategory} profiles={profiles}
            projectScope={scope?.allowedTools || null}
            value={editTools} onChange={setEditTools} />
        </Modal>
      )}

    </div>
  )
}
