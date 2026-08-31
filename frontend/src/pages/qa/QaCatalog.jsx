import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Card, Table, Tag, Space, Button, Input, Select, Alert, message, Tooltip,
  Progress, Modal, Form, Collapse, Popconfirm, Popover, Checkbox, Drawer, Empty, Spin, Tabs,
} from 'antd'
import {
  ReloadOutlined, SearchOutlined, BugOutlined, FileTextOutlined, SettingOutlined,
  InfoCircleOutlined, CheckCircleFilled, WarningFilled, CloseCircleOutlined,
  RobotOutlined, LoadingOutlined, CopyOutlined, DownloadOutlined,
} from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { api } from '../../utils/request'
import { PERM } from '../../utils/permissions'
import { usePermissions } from '../../utils/PermissionContext'

// 文字色按**能不能读**分档，不按"想让它多淡"分。括号里是白底上量出来的对比度，
// 不是挑出来的手感 —— WCAG AA 对正文要求 4.5:1，而这一页的说明字是 11–13px，
// 比"正文"还小，所以只会更难读。
//
// 这里原来只有 gray(3.2:1) 和 faint(1.6:1) 两档，**两档都不到线**，
// 而整句整句的说明文字用的恰恰是 faint —— 1.6:1 基本等于没显示。
// 后果不是"有点淡"：这一页三个维度旁边那列小字是**唯一**解释"这个数怎么来的"的地方，
// 它读不到，剩下的就只有三个光秃秃的数字，人又回到自己猜 —— 那正是这一页要消灭的东西。
//
// 规则：**faint 只许当装饰**（分隔点、占位破折号、0 值、图标、虚线边框）。
// 只要渲染的是一句话，就必须用 hint 或更深的，一个例外都不留。
// 三个语义色本来是给图形挑的（进度条、数字、边框），但页面上它们**同时在渲染句子** ——
// 徽标那个结论词本身就是 orange 的 2.6:1，而它恰恰是整个抽屉里最该读清的一句。
// 所以按"文字"的线重挑了一遍，色相不变、压暗到过 AA。这一页的图形跟着变深，认了。
//
// ⚠ 定色**不许心算**。第一版 hint 我按公式算出 4.56:1 就定了，真跑起来量到的是
// **4.48:1** —— 差在页面背景不是纯白（全站有淡渐变），而公式默认它是。差 0.08，
// 正好压在线下。所以这里全部按"白底 ≥ 4.8"挑，把那点差额当余量留出来，
// 最后以 scripts/selftest 里那个真量抽屉的脚本为准。
const C = {
  red: '#cf3128',    //  5.1:1  （原 #e8453c = 3.9:1）
  orange: '#b35800', //  4.9:1  （原 #ff7d00 = 2.6:1）
  teal: '#0b807c',   //  4.8:1  （原 #0ea5a0 = 3.0:1）
  blue: '#1868c7',   //  5.5:1  冷热档里的「今天」。跟 teal 只差色相（亮度差 1.14:1），
                     //         所以它**不能单独承担分档** —— 见 ACTIVITY_TIERS 上面那段
  ink: '#1d2129',    // 16.1:1  正文
  gray: '#5f6b7a',   //  5.4:1  次要文字、标签
  hint: '#69737f',   //  4.8:1  说明小字（跟 gray 只差一点点，是**故意**的：
                     //         它常常就嵌在 gray 那段里当下一层，差太多会变成两段并列的正文）
  faint: '#86909c',  //  3.2:1  **只许装饰**，不许承载句子
  line: '#e5e6eb',
}

const STATE_TAG = {
  covered: { text: '✅ 已覆盖', color: C.teal, bg: 'rgba(14,165,160,0.1)' },
  gap: { text: '⬜ 待补', color: C.orange, bg: 'rgba(255,125,0,0.1)' },
  deprecated: { text: '❌ 已废弃', color: C.gray, bg: 'rgba(0,0,0,0.03)' },
}

const PRIORITY_COLOR = { P0: C.red, P1: C.orange, P2: C.teal, P3: C.gray }

// 解析器认出来的列角色 → 页面上的说法。用的是这一页表头本来就用的词，
// 别让人在"认列结果"和"表格列名"之间再翻译一次。
const COLUMN_ROLE_CN = {
  title: '场景描述', priority: '优先级', risk: '风险分', tier: '层', state: '状态',
}

// 口径全部抄自 QA 清单自己的「列的含义」一节 —— 平台不另立一套说法，
// 否则同一个词在两边意思不一样，比不解释更坏。
const TIER = {
  smoke: { text: '冒烟', desc: '闸门 1。这一层红了，后面所有闸门的红都是噪音' },
  api: { text: '单点契约', desc: '单个接口的请求/响应契约' },
  scenario: { text: '跨面全链', desc: '跨多个接口的完整业务链路' },
  ui: { text: '浏览器旅程', desc: '真浏览器里的用户旅程' },
}
const tierText = t => TIER[t]?.text || t || '—'

const HIGH_RISK = 6      // 与后端 qa_catalog.HIGH_RISK 同口径
const URGENT_RISK = 9

const riskColor = r => (r >= URGENT_RISK ? C.red : r >= HIGH_RISK ? C.orange : C.gray)

// 清单里一半的场景描述带 `反引号` 和 **加粗**，原样打出来是满屏符号
const RICH_RE = /(`[^`]+`|\*\*[^*]+\*\*)/g
function Rich({ text }) {
  if (!text) return null
  return String(text).split(RICH_RE).filter(Boolean).map((p, i) => {
    if (p.length > 2 && p.startsWith('`') && p.endsWith('`')) {
      return (
        <code key={i} style={{
          fontFamily: 'var(--font-mono)', fontSize: 12, padding: '0 4px',
          background: 'rgba(0,0,0,0.04)', borderRadius: 3, color: '#476582',
        }}>{p.slice(1, -1)}</code>
      )
    }
    if (p.length > 4 && p.startsWith('**') && p.endsWith('**')) {
      return <strong key={i}>{p.slice(2, -2)}</strong>
    }
    return <span key={i}>{p}</span>
  })
}

// 截到 n 行，多的省略。行高必须固定 —— 这一页的场景说明能有 500 字，
// 一行铺开就是 200px+，20 行等于几千像素，翻页和对照全废了。
// 一行文字 21px，两行 42px —— 场景列固定占这么高，行高才齐得住
const CELL_H = 42

const clampTo = n => ({
  display: '-webkit-box', WebkitLineClamp: n, WebkitBoxOrient: 'vertical',
  overflow: 'hidden', wordBreak: 'break-word',
})

function Panel({ title, extra, children, tone }) {
  const border = tone === 'bad' ? 'rgba(232,69,60,0.35)' : tone === 'warn' ? 'rgba(255,125,0,0.35)' : undefined
  return (
    <Card
      size="small" style={{ flex: 1, minWidth: 300, borderColor: border }}
      styles={{ body: { padding: '12px 16px' } }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: C.ink }}>{title}</span>
        {extra}
      </div>
      {children}
    </Card>
  )
}

// 看板上的每一行都能点 —— 看到一个数字，下一步动作永远是"给我看这些条"
// ── 「这个域最近有人动吗」──────────────────────────────────────────
// 时间一律在渲染时算相对值，**不让后端把「3 天前」算好存进缓存** —— 那份缓存按
// commit 命中，QA 那边不提交就一直不失效，页面会一直说「3 天前」直到有人推代码。

function relWhen(iso, now) {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return '—'
  const mins = Math.round((now - t) / 60000)
  if (mins < 60) return mins < 1 ? '刚刚' : `${mins} 分钟前`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days} 天前`
  const months = Math.round(days / 30)
  return months < 12 ? `${months} 个月前` : `${Math.round(months / 12)} 年前`
}

const absWhen = iso => (iso ? new Date(iso).toLocaleString('zh-CN', { hour12: false }) : '—')

// 「最近更新」的判据：**跟仓库里最新的那次动静比，不跟今天比。**
//
// 拿自然日窗口（近 7 天）当判据在这份数据上直接失效：uag-qa 的 2026-08-27 20:42
// 是一次批量恢复，24 个域全被扫到，"近 7 天"于是把 24 个域全标亮 —— 标记恒真，
// 等于没标。反过来，锚在"最新那次动静"上：仓库搁置半年，最后动过的那几个域照样
// 标得出来（那本来就是「最近在做的」的正确答案），而它们旁边写着「6 个月前」，
// 不会有人误以为是今天干的。
//
// 分四档，不是两档 —— 两档只能回答"是不是最新那一批"，回答不了
// "上周还有人碰、还是一直没人管"，而后者才是"这个域要不要补人"的判据。
//
// 四档现在**一档一个颜色**（青 → 蓝 → 灰 → 浅灰）。原来 live 和 today 共用一支
// teal、只靠字重分，屏幕上就是"两档看着一样"，所以按要求把颜色摊开到四支。
//
// 但颜色只是**第三条**通道，不是唯一那条 —— 圆点(●/○/无) 和字重 (600/400) 都得留着。
// 原因是量出来的，不是保守：teal #0b807c 4.78:1、blue #1868c7 5.46:1、
// gray #5f6b7a 5.43:1、faint #86909c 3.24:1，四支对白底都够看；可是
// **blue 和 gray 两两之间的亮度比只有 1.01:1** —— 转成灰度图、或者红绿色弱的人看，
// 这两档是同一个颜色。真按"颜色标识出来"一条腿走，这两档在那些场景下直接归并。
// 所以：颜色负责一眼扫（谁都能看出四种），圆点+字重负责扛住颜色失效的那些场景。
// 想再加档先量亮度差，别照着色板挑手感。
//
// ⚠ 档位边界是**照着这份数据实测出来的**，不是"24小时/一周/一月"这么顺口排下来的。
// 顺口的那套在这份数据上会退化回两档 —— 2026-08-30 实测 24 个域：
//   15 个挤在 0~15.1 小时（都是"今天"），9 个**全部**卡在 66.2 小时（2.8 天）同一个点。
// 那 9 个的时间根本不是"谁动了它"，是 b39fb2831「一次性恢复被移出 git 索引的 186 个
// 文件」那一笔把没脚本的域整整齐齐盖了同一个戳（它们 covered=0、scriptUpdatedAt=null，
// 只能退回清单时间）。所以 24h/7d/30d 切下去 = 15 + 9 + 0 + 0，
// **多调的那两档一个都落不到，屏幕上还是两级。**
// 真正有分辨力的切口在前 24 小时里面：0~5.4h 是同一轮干活，14~15h 是上一轮。
// 于是第一刀落在 6 小时（≈一个工作时段），当前落成 10 + 5 + 9 + 0，三档同时可见。
// 改档位之前先把分布拉出来看一眼，别照着"合理的时间单位"拍。
const H = 3600 * 1000
const D = 24 * H
const ACTIVITY_TIERS = [
  { key: 'live',  within: 6 * H,    label: '刚动过', note: '离本仓最后一次动静 6 小时内（同一轮）', dot: '●', color: C.teal,  weight: 600 },
  { key: 'today', within: D,        label: '今天',   note: '24 小时内',        dot: '●', color: C.blue,  weight: 400 },
  { key: 'week',  within: 7 * D,    label: '本周',   note: '一周内 —— 别人今天动了，它没有', dot: '○', color: C.gray,  weight: 400 },
  { key: 'cold',  within: Infinity, label: '搁置',   note: '一周以上没动过',   dot: '',  color: C.faint, weight: 400 },
]

// 锚点 = 本仓最后一次动静。四档量的都是"离它多远"，不是"离今天多远"。
function activityAnchorOf(domains) {
  const times = (domains || [])
    .map(d => (d.updatedAt ? new Date(d.updatedAt).getTime() : NaN))
    .filter(Number.isFinite)
  return times.length ? Math.max(...times) : null
}

function activityTierOf(iso, anchor) {
  if (!iso || anchor == null) return null
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return null
  const age = anchor - t
  return ACTIVITY_TIERS.find(x => age <= x.within) || ACTIVITY_TIERS[ACTIVITY_TIERS.length - 1]
}

// 域行右侧那格「最近更新」。两侧时间都塞进 tooltip，格子里只显示一个 ——
// 24 行 × 两个时间平铺出来没人看得完，而"谁在动"这件事一眼就得能扫出来。
function DomainWhen({ d, now, anchor }) {
  const act = activityTierOf(d.updatedAt, anchor)
  const onlyCatalog = d.updatedFrom === 'catalog'
  const line = (label, at, commit, empty) => (
    <div style={{ marginBottom: 4 }}>
      <span style={{ opacity: 0.7 }}>{label}</span>{' '}
      {at ? absWhen(at) : empty}
      {commit?.sha && (
        <div style={{ opacity: 0.7, paddingLeft: 42 }}>
          <code>{commit.sha}</code>{commit.subject ? ` ${commit.subject}` : ''}
        </div>
      )}
    </div>
  )
  return (
    <Tooltip
      title={
        <div style={{ fontSize: 12, lineHeight: 1.7, maxWidth: 420 }}>
          {line('脚本侧', d.scriptUpdatedAt, d.scriptCommit, '这个域一个脚本都没有')}
          {line('清单侧', d.catalogUpdatedAt, d.catalogCommit, '清单里读不到这个域的行')}
          <div style={{ opacity: 0.7, borderTop: '1px solid rgba(255,255,255,0.2)', paddingTop: 4 }}>
            格子里显示的是{onlyCatalog ? '清单侧' : '脚本侧'}。
            {/* 这句是这一列最容易被误读的地方：整仓批量提交（重命名、一次性恢复）
                会把所有域的清单侧刷成同一时间，那不是"这个域在推进" */}
            清单侧常常是一次批量提交扫的（看提交标题就知道），所以有脚本时以脚本侧为准。
            <div style={{ marginTop: 6, marginBottom: 2 }}>
              冷热四档量的是<b>离本仓最后一次动静多远</b>，不是离今天多远
              {/* 为什么不用自然日：整仓批量提交会把所有域刷成同一天，"近 7 天"于是
                  全标亮 = 恒真；反过来锚在最后一次动静上，仓库搁半年也还能指出
                  "最后在做的是这几个域" */}
            </div>
            {/* 悬浮层是深底，档位色是照白底调的，直接拿来标会糊掉 ——
                所以这儿只用圆点+字重复述结构，颜色留给格子本身 */}
            {ACTIVITY_TIERS.map(t => (
              <div key={t.key} style={{ paddingLeft: 6, fontWeight: t.weight }}>
                <span style={{ display: 'inline-block', width: 14 }}>{t.dot}</span>
                <b>{t.label}</b> · {t.note}
              </div>
            ))}
          </div>
        </div>
      }
    >
      <span
        style={{
          width: 86, textAlign: 'right', whiteSpace: 'nowrap',
          color: act ? act.color : C.faint,
          fontWeight: act ? act.weight : 400,
        }}
        onClick={e => e.stopPropagation()}
      >
        {act?.dot && <span style={{ marginRight: 3 }}>{act.dot}</span>}
        {onlyCatalog && d.updatedAt && <span style={{ color: C.hint, marginRight: 3 }}>清单</span>}
        {relWhen(d.updatedAt, now)}
      </span>
    </Tooltip>
  )
}

// 域行那条覆盖率进度条的颜色。原来 24 个域全是一支 C.teal —— 长短已经把覆盖率
// 说过一遍了，颜色再说同一件事等于白占一条通道，而这一页真正要先跳出来的是
// **哪几个域卡着门禁**。所以颜色改成「缺的是什么」，跟条长各说一件事。
// 红/橙/青跟 PRIORITY_COLOR 同源（P0 红、P1 橙），整页颜色语义是一套。
const COVER_STROKE = [
  { key: 'p0', color: C.red, label: '缺 P0', note: 'P0 有缺口 —— check-coverage.sh 直接 BLOCK' },
  { key: 'gap', color: C.orange, label: '有缺口', note: '缺的都不是 P0，不阻断门禁' },
  { key: 'full', color: C.teal, label: '全认领', note: '清单里每条都有脚本认领 —— 认领不等于跑绿' },
  { key: 'none', color: C.line, label: '清单没行', note: '清单里读不到这个域的行，条是空的' },
]
const coverStrokeOf = (d) => {
  if (!d.total) return COVER_STROKE[3]
  if (d.p0Gap) return COVER_STROKE[0]
  if (d.gap) return COVER_STROKE[1]
  return COVER_STROKE[2]
}

// 评审徽标。三件事：
//   1. 把**评审时间**摆进悬浮 —— 原来这儿一个时间都没有（只有环境和 commit），
//      而"这结论是什么时候下的"恰恰是要不要信它的前提；
//   2. 评审时间早于这个域最后一次改动 = 过期，格子里直接挂个橙色警告。
//      不能只写在悬浮里：24 行里哪几行的结论已经不作数，得不悬浮就看得见；
//   3. 说清是哪一侧动的 —— 清单侧常是整仓批量提交扫到的，那种"过期"轻一些。
// 过期时**不动徽标本身的颜色**：结论当时是什么结论，它还是什么结论，
// 过期是另一件事，交给旁边那个警告标去说。
function ReviewBadge({ d, rv, now, onOpen }) {
  const v = VERDICT[rv.result?.verdict]
  // finishedAt 优先：createdAt 是排队时刻，一个域拆几批读能差出十几分钟
  const at = rv.finishedAt || rv.createdAt
  const rt = at ? Date.parse(at) : NaN
  const dt = d.updatedAt ? Date.parse(d.updatedAt) : NaN
  const stale = Number.isFinite(rt) && Number.isFinite(dt) && rt < dt
  const onlyCatalog = d.updatedFrom === 'catalog'
  return (
    <Tooltip title={
      <div style={{ fontSize: 12, lineHeight: 1.8, maxWidth: 360 }}>
        <div><b>{v?.text || '已评'}</b></div>
        <div style={{ marginTop: 4 }}>
          评审于 <b>{relWhen(at, now)}</b>
          <div style={{ opacity: 0.75 }}>{absWhen(at)}</div>
        </div>
        {stale && (
          <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid rgba(255,255,255,0.25)' }}>
            <div><b>⚠ 这次评审已经过期</b></div>
            <div>
              评审跑完之后，这个域的{onlyCatalog ? '清单侧' : '脚本侧'}在
              {' '}{absWhen(d.updatedAt)}{' '}又动过。
            </div>
            <div style={{ opacity: 0.85 }}>
              上面那句结论是改动<b>之前</b>得出的，现在不一定还成立 ——
              要拿它当验收依据，得重评一次。
            </div>
            {onlyCatalog && (
              <div style={{ opacity: 0.75, marginTop: 4 }}>
                不过清单侧常常是整仓批量提交扫到的（重命名、一次性恢复），
                那种情况下这个域的内容未必真变了 —— 点开看 commit 标题就知道。
              </div>
            )}
          </div>
        )}
        <div style={{ opacity: 0.75, marginTop: 6 }}>
          {rv.environmentName || '—'} · {rv.commitSha} · 点开看结论
        </div>
      </div>
    }>
      <span style={{ cursor: 'pointer' }} onClick={() => onOpen(rv)}>
        {stale && <WarningFilled style={{ color: C.orange, marginRight: 4, fontSize: 12 }} />}
        <Tag color={v?.color || 'default'} style={{ margin: 0, cursor: 'pointer' }}>
          {v?.short || '已评'}
        </Tag>
      </span>
    </Tooltip>
  )
}

function Hit({ onClick, active, children, style }) {
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 8, cursor: onClick ? 'pointer' : 'default',
        padding: '2px 6px', margin: '0 -6px', borderRadius: 4, fontSize: 12,
        background: active ? 'rgba(14,165,160,0.1)' : 'transparent', ...style,
      }}
      onMouseEnter={e => { if (onClick && !active) e.currentTarget.style.background = 'rgba(0,0,0,0.03)' }}
      onMouseLeave={e => { if (onClick && !active) e.currentTarget.style.background = 'transparent' }}
    >
      {children}
    </div>
  )
}

// AI 评审的结论。措辞对着「这个域的脚本撑不撑得起这个域的清单」说，
// 不用「通过/不通过」—— 这里没有门禁，说"不通过"会被当成拦了谁的活
// 词换过三轮，前两轮同一个毛病（**用一个词概括，读的人就得猜**），第三轮是另一个：
//   第一版「靠得住 / 有水分 / 撑不住」—— "水"在哪？"撑"的是什么？
//   第二版「能信 / 信一半 / 不能信」—— 信什么？信一半是哪一半？
//   第三版「都验到了 / 部分没验到 / 多数没验到」—— 意思清楚了，但**没主语**：
//     同一个抽屉里还写着「一份都没真跑」「第 3 批没读成」，读的人完全有理由把
//     「部分没验到」读成"你自己只看了一部分"。**结论词除了要不用再解释一遍，
//     还得让人一眼看出「这句话在说谁」。**
//   第四版「认领都算数 / 部分认领不算数 / 多数认领不算数」—— 主语有了，
//     但「认领」是**我们这边发明的词**，QA 的清单上根本没有它，写的是 ✅「已覆盖」。
//     读的人得先在脑子里把「认领」翻回「已覆盖」才看得懂，等于把词典塞给了读者。
//     （2026-08-29 实测：拿这一版问人，第一反应就是"这是什么意思"。）
// 第五版的规矩：**只许用 QA 清单上原本就有的词**，再加一个能判真假的动词。
//   「已覆盖」是他们自己写在清单上的，加引号原样引；「成立 / 不成立」谁都不用学。
//   这一栏答的就是一句话：*清单上那个「已覆盖」，成不成立？*
// 一个结论配两种长度，**不是嫌长随手砍的**：
//   text  给抽屉标题和「这次判…」那句用 —— 那儿有整行的地方，主语「已覆盖」写全。
//   short 给「按域看缺口」那张表的徽标用 —— 那一格一百来 px，11 个字的 text 横着
//         压到右边一列的域码上去了（2026-08-29 截图为证：SYS 那行盖住了 TEM）。
// 为什么这回敢砍到两个字（前四版砍不动，见上面那段）：**以前徽标是唯一的出口**，
//   一个词没说清就真没地方说了；现在悬停补全句、点开是整屉细节，这一格只用回答
//   「要不要点进去」。于是留下的只有判真假那两个字，程度交给颜色（绿 / 橙 / 红）——
//   「部分 / 多数」还留着，那是橙红两档唯一的区别，省了两档就并成一档。
//   主语（「已覆盖」）不写进徽标，但**必须在悬停第一行**，别让人猜这栏在说谁。
// 不用「9 处不实」这种数字：徽标是模型对整个域下的一句总评，跟底下 scriptGaps 数出来
//   的条数不是一个来源（见 VERDICT_SOURCE），并排放数字会被读成同一个数，然后打架。
const VERDICT = {
  ok: { short: '属实', text: '「已覆盖」都成立', color: 'success',
        why: '清单标了「已覆盖」的场景，脚本读下来都真在验那件事' },
  risky: { short: '部分不实', text: '「已覆盖」部分不成立', color: 'warning',
           why: '有一部分标了「已覆盖」，脚本其实没验到 —— 断言太松，或在这个环境里整条跳过了' },
  bad: { short: '多数不实', text: '「已覆盖」多数不成立', color: 'error',
         why: '标了「已覆盖」的主要场景多数没真验到 —— 这个域的「已覆盖」当不了验收依据' },
}
const VERDICT_SUBJECT = '说的是 QA 的清单和脚本，不是说我读了多少 —— 我这趟读了多少、漏没漏，在「怎么看的」里单独写。'
// 这一句非写不可：**徽标和底下那些数不是一个来源**，而它们并排放着，
// 不说就会被读成"徽标 = 把下面的数加起来得出的"。实际上徽标是模型对整个域下的一句总评，
// 底下那些数是从 scriptGaps 一行行数出来的。于是完全可能「多数不成立」配一个很小的数
// —— 那不是打架，是两件事。多批取最坏也得说：一个域拆 8 批读，**一批判 bad 整个域就是 bad**。
const VERDICT_SOURCE = '这句总评是模型对整个域下的，不是把下面的数加起来算的；一个域拆几批读，取最坏的那批。'

// 「谁动手」。人第一个要知道的不是严重度，是"这条要不要我处理"。
// 上一版三类混在一张表里：MCP 那 6 条里有 2 条根子是我们自己的环境记录没铺 apikey，
// 跟人家脚本一点关系没有 —— 看的人先当成"脚本写得不行"，理解半天才反应过来。
// **别让人来分，分好了给他。**
const BLAME = {
  script: { title: 'QA 的脚本要改', color: C.red,
            why: '断言写得站不住：跑绿了也证明不了它认领的那件事' },
  env: { title: '不是脚本的问题：环境没铺东西', color: C.orange,
         why: '脚本可能写得很对，只是在这个环境里自己跳过了。我们只看得到自己这侧的环境记录，QA 跑的时候有没有，这儿判不了' },
  catalog: { title: '清单口径要商量', color: C.teal,
             why: '脚本和环境都没错，是清单认领的口径对不上，或者这件事清单里压根没列' },
}
// 人在这一页只做一个决定：这个域要不要停下来处理。
// 上一版给他的是四十几条一句话（分三栏、每栏露 3 条）—— 24 个域这么看，一天看不完两个。
// 维度固定，横着比也是这几块，明细留在隔壁「给 AI / 整改」那页（那页给动手的人看）。
//
// ⚠ 某一格 0 条 ≠ 这一块没问题，只等于**这一趟没抓到**。这句话必须摆在表旁边：
//   漏判是看不见的，让 0 自己去暗示"这块过了"，等于替结论吹牛。
// 域名已经带了代号就别再拼一遍 —— 抽屉标题真出现过「AI 评审 · MCP MCP 能力」。
function domainLabel(code, name) {
  const c = (code || '').trim(); const n = (name || '').trim()
  if (!c) return n
  if (!n) return c
  return n.startsWith(c) ? n : `${c} ${n}`
}

const BLAME_ORDER = ['script', 'env', 'catalog']
const blameOf = g => (BLAME[g.blame] ? g.blame : 'script')
const REVIEW_RUNNING = s => s === 'queued' || s === 'running'

const LEGEND = (
  <div style={{ maxWidth: 460, fontSize: 12, lineHeight: 1.9 }}>
    <div><b>优先级 P</b> — 先做哪个。P0 最高，按业务影响 → 核心旅程 → 使用频率判定。</div>
    <div><b>风险 R</b> — 要不要缓解。<b>概率(1–3) × 影响(1–3)，取值 1–9</b>。</div>
    <div style={{ color: C.gray, paddingLeft: 12 }}>
      P 和 R 是两条独立的轴，不许互相推导。P2 的场景评出 R≥6，
      是「回去重新审优先级」的信号，不是自动升 P0。
    </div>
    <div><b>执行层</b> — {Object.entries(TIER).map(([k, v]) => `${k} ${v.text}`).join(' · ')}</div>
    <div><b>状态</b> — ✅ 清单标了已有用例 · ⬜ 待补 · ❌ 已废弃（ID 保留不复用）</div>
    <div style={{ marginTop: 6, color: C.gray }}>
      「已覆盖」只代表<b>有脚本声明了这个场景 ID</b>，不代表这条跑过、更不代表跑绿了；
      挂着 @known-bug 的就是明知道红的。口径来自 QA 清单的「列的含义」一节。
    </div>
  </div>
)

export default function QaCatalog() {
  const { projectId } = useParams()
  const { has } = usePermissions()
  const canConfig = has(PERM.PROJECT_SETTINGS)
  const canGenerate = has(PERM.CASE_GENERATE)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [domain, setDomain] = useState()
  const [priority, setPriority] = useState()
  const [tier, setTier] = useState()
  const [state, setState] = useState()
  const [quick, setQuick] = useState()          // 看板点出来的那一类：urgent/bugs/lying/mismatch
  const [showDeprecated, setShowDeprecated] = useState(false)
  // 默认按**更新时间倒序**。清单里 300+ 条，按 ID 排等于按域码字母序 ——
  // 一进来看到的永远是 A 开头那个域的老场景，而人来这一页多半是想知道
  // 「最近在动的是哪几条」。这一列的排序函数按 Date.parse 比，不是字符串比
  // （`%cI` 带时区，字典序会把先后排反），所以默认排序也走它。
  const [sorter, setSorter] = useState({ columnKey: 'updatedAt', order: 'descend' })
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [cfgOpen, setCfgOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  // 看脚本内容：点开是 git show 出来的原文，只读
  const [file, setFile] = useState(null)
  const [fileLoading, setFileLoading] = useState(false)

  // 域级 AI 评审
  const [envs, setEnvs] = useState([])
  const [reviews, setReviews] = useState({})      // 域码 → 最近一次评审
  const [reviewFor, setReviewFor] = useState(null)   // 正在弹「选环境」框的那个域
  const [envId, setEnvId] = useState()
  const [starting, setStarting] = useState(false)
  const [openReview, setOpenReview] = useState(null)  // 抽屉里展示的那一条

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.get(`/projects/${projectId}/qa-catalog`)
      setData(res.data)
    } catch { /* request.js 已展示错误 */ } finally { setLoading(false) }
  }, [projectId])

  useEffect(() => { fetchData() }, [fetchData])

  const fetchReviews = useCallback(async () => {
    try {
      const res = await api.get(`/projects/${projectId}/qa-catalog/reviews`)
      const map = {}
      for (const r of res.data?.reviews || []) map[r.domain] = r
      setReviews(map)
      return map
    } catch { return {} }
  }, [projectId])

  useEffect(() => {
    fetchReviews()
    api.get(`/projects/${projectId}/environments`).then(r => setEnvs(r.data || [])).catch(() => {})
  }, [projectId, fetchReviews])

  // 有域在评就接着轮询。**不轮询的话页面永远停在「排队中」** —— 后台跑完了没人告诉它
  const pending = useMemo(
    () => Object.values(reviews).filter(r => r.status === 'queued' || r.status === 'running'),
    [reviews])
  useEffect(() => {
    if (!pending.length) return undefined
    const t = setInterval(async () => {
      const map = await fetchReviews()
      // 抽屉开着的那条也要跟着变，否则人盯着一个「评审中」看到天荒地老。
      // ⚠ `dims` 得**留住**：列表接口不发它，直接覆盖就会把刚取回来的详情打回原形，
      // 于是抽屉每 3 秒在「分好组的表」和「一列裸键」之间闪一次
      // （同一个域的同一条评审，id 没变，dims 不会过期）。
      setOpenReview(prev => {
        const next = prev && map[prev.domain]?.id === prev.id ? map[prev.domain] : prev
        return next && next !== prev ? { ...next, dims: next.dims ?? prev.dims } : next
      })
    }, 3000)
    return () => clearInterval(t)
  }, [pending.length, fetchReviews])

  // 抽屉里那张**按维度分组的表**要的是 `dims`，而列表接口**故意不发**它
  // （一次几十行，每行挂一份同样的口径常量）。抽屉一直是拿列表行直接渲染的，
  // 于是那张表从来没画出来过 —— 走的全是降级分支：把 9 个原始维度键
  // （`skip` `assert` `coverage` …）平铺成一列数字，没有中文名、没有分组、没有解释。
  // 2026-08-29 用户问「你能用人能听得懂的来归类吗」问的就是这堆裸键。
  // **归类一直是有的（后端 `AXES` 三块中文），只是没送到页面上。**
  //
  // 更坏的是降级那段自己给的诊断：它写「最常见的原因是后端还跑着旧代码」。
  // 这里根本不是旧代码，是这条路径压根没取过详情 —— 一句猜错的诊断会把人
  // 支去重启后端，重启完照旧，然后开始怀疑别的地方。
  useEffect(() => {
    const r = openReview
    if (!r?.id || r.status !== 'done' || r.dims) return
    let dead = false
    api.get(`/projects/${projectId}/qa-catalog/reviews/${r.id}`)
      .then(res => {
        // 期间人可能已经切到别的域或关掉了，别把详情盖到另一条上
        if (!dead && res.data?.id === r.id) setOpenReview(res.data)
      })
      .catch(() => { /* 取不到就维持降级渲染，那段已经把话说清楚了 */ })
    return () => { dead = true }
  }, [projectId, openReview])

  const openFile = async (path) => {
    setFile({ path, content: '' })
    setFileLoading(true)
    try {
      const res = await api.get(
        `/projects/${projectId}/qa-catalog/file?path=${encodeURIComponent(path)}`)
      setFile(res.data)
    } catch { setFile(null) } finally { setFileLoading(false) }
  }

  const startReview = async () => {
    setStarting(true)
    try {
      const res = await api.post(`/projects/${projectId}/qa-catalog/reviews`,
        { domain: reviewFor.code, envId })
      setReviews(prev => ({ ...prev, [reviewFor.code]: res.data }))
      setReviewFor(null)
      setOpenReview(res.data)
      message.success(`已开始评审 ${reviewFor.code}，几十秒后出结论`)
    } catch { /* request.js 已展示错误 */ } finally { setStarting(false) }
  }

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      const res = await api.post(`/projects/${projectId}/qa-catalog/refresh`)
      setData(res.data)
      if (res.data?.error) message.warning(res.data.error)
      else message.success('已从 QA 仓拉取最新清单')
    } catch { /* request.js 已展示错误 */ } finally { setRefreshing(false) }
  }

  const openConfig = () => {
    const c = data?.config || {}
    form.setFieldsValue({
      url: c.url || '',
      branch: c.branch || '',
      catalogPath: c.catalogPath || '',
      caseGlobs: (c.caseGlobs || []).join(', '),
    })
    setCfgOpen(true)
  }

  // 保存后端会顺手按新配置读一遍（自动识别认没认出来，当场就能看见）
  const saveConfig = async (payload) => {
    setSaving(true)
    try {
      const res = await api.put(`/projects/${projectId}/qa-catalog/config`, payload)
      setData(res.data)
      setCfgOpen(false)
      if (!payload.url) message.success('已取消 QA 仓配置')
      else if (res.data?.error) message.warning(res.data.error)
      else message.success('已保存并读取 QA 仓')
    } catch { /* request.js 已展示错误 */ } finally { setSaving(false) }
  }

  const handleSaveConfig = async () => {
    let v
    try { v = await form.validateFields() } catch { return }
    await saveConfig({
      url: (v.url || '').trim(),
      branch: (v.branch || '').trim(),
      catalogPath: (v.catalogPath || '').trim(),
      caseGlobs: (v.caseGlobs || '').split(',').map(x => x.trim()).filter(Boolean),
    })
  }

  const configured = data?.configured
  const scenarios = useMemo(() => data?.scenarios || [], [data])
  const summary = data?.summary
  const repo = data?.repo
  const bugRefs = useMemo(() => data?.knownBugRefList || [], [data])
  const catalogIssues = data?.catalogIssues

  const tiers = useMemo(
    () => [...new Set(scenarios.map(s => s.tier).filter(Boolean))].sort(),
    [scenarios],
  )

  const QUICK = useMemo(() => ({
    urgent: { label: 'P0 待补 · 风险 9', test: s => s.state === 'gap' && s.priority === 'P0' && (s.risk || 0) >= URGENT_RISK },
    bugs: { label: '挂着已知缺陷', test: s => (s.knownBugs || []).length > 0 },
    lying: { label: '标了 ✅ 却没有脚本', test: s => s.claimedButUncovered },
    mismatch: { label: `风险 ≥${HIGH_RISK} 但优先级 P2/P3`, test: s => s.state !== 'deprecated' && (s.risk || 0) >= HIGH_RISK && ['P2', 'P3'].includes(s.priority) },
  }), [])

  const hasFilter = keyword || domain || priority || tier || state || quick
  const clearFilters = () => {
    setKeyword(''); setDomain(); setPriority(); setTier(); setState(); setQuick()
  }
  // 从看板跳过来时，别让上一次的筛选残留在里面把结果减成空的
  const jump = (patch) => {
    clearFilters()
    setDomain(patch.domain); setPriority(patch.priority); setState(patch.state); setQuick(patch.quick)
    if (patch.sortRisk) setSorter({ columnKey: 'risk', order: 'descend' })
    if (patch.showDeprecated) setShowDeprecated(true)
  }

  useEffect(() => { setPage(1) }, [keyword, domain, priority, tier, state, quick, showDeprecated])

  const filtered = useMemo(() => scenarios.filter(s => {
    if (!showDeprecated && s.state === 'deprecated' && state !== 'deprecated') return false
    if (domain && s.domain !== domain) return false
    if (priority && s.priority !== priority) return false
    if (tier && s.tier !== tier) return false
    if (state && s.state !== state) return false
    if (quick && !QUICK[quick].test(s)) return false
    if (keyword) {
      const k = keyword.toLowerCase()
      const hit = s.id.toLowerCase().includes(k)
        || (s.title || '').toLowerCase().includes(k)
        || (s.scripts || []).some(x => x.path.toLowerCase().includes(k))
      if (!hit) return false
    }
    return true
  }), [scenarios, domain, priority, tier, state, quick, keyword, showDeprecated, QUICK])

  const urgentCount = useMemo(
    () => scenarios.filter(QUICK.urgent.test).length, [scenarios, QUICK])

  // **按域码固定排序，不按缺口。** 原来是缺口多的排前面（「黑洞域」自己浮上来），
  // 代价是这一格的位置跟着覆盖进度走：补了两条 SEC，它就从第 17 位挪到第 20 位，
  // 每次「拉取最新」整格重排一遍 —— 想再看一眼刚才那个域，得从 24 个里重新找。
  // 排序本来是为了省下这次找，结果反而让人每次都找。
  // 缺口大小照旧看得见（缺 N 是橙的、P0 是红的），只是不再决定它站在哪。
  const domainRows = useMemo(
    () => [...(data?.domains || [])].sort((a, b) => a.code.localeCompare(b.code)),
    [data],
  )
  // 冷热四档的锚点，和算相对时间用的"现在"。两个都跟着 data 走：
  // 同一次渲染里所有域必须用同一个 now，否则 24 行会各自取一次时间，
  // 边界上偶发地这行标亮、那行不标
  const activityAnchor = useMemo(() => activityAnchorOf(domainRows), [domainRows])
  // data 就是这里唯一想依赖的：每拉一次数据重新取一次「现在」，而不是每次重渲染都跳一下
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const renderedAt = useMemo(() => Date.now(), [data])

  const sortOrderOf = key => (sorter.columnKey === key ? sorter.order : null)

  const columns = [
    {
      title: 'ID', dataIndex: 'id', width: 92, fixed: 'left',
      render: (v, r) => (
        <Tooltip title={r.domainName ? `${r.domain} — ${r.domainName}` : r.domain}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 12, color: C.gray, whiteSpace: 'nowrap',
          }}>{v}</span>
        </Tooltip>
      ),
    },
    {
      // 行高必须**固定**，不是"最多两行"。备注最长 889 字，不截的话单行能到 200px+，
      // 一屏放不下两条，「哪些域缺得多」这种对照着看的事就做不了了。
      // 只封顶还不够：一行的标题 39px、两行的 59px，列表照样是锯齿状的，
      // 眼睛要跨行横着对「状态」和「更新时间」时，每行错开 20px 就得重新找一次。
      // 所以这里给死 CELL_H（正好两行），有备注就让备注占掉其中一行。
      // 全文走悬浮 —— 不是把信息删掉，是把它挪到需要时再看。
      // 整张表唯一「要读」的内容，其余都是标签和时间 —— 它得最宽。
      // **不给它写 width**：fixed 布局下没写宽度的列吃掉剩余宽度，所以加宽它
      // 的办法是把旁边几列调窄（下面那几列各瘦了一点，一共让出 ~132px），
      // 而不是给它写个数字（写死了就不跟着窗口伸缩了）。
      title: '场景（这条要证明什么）', dataIndex: 'title',
      render: (v, r) => {
        // 「已废弃」「@known-bug GL#530」这类备注在别的列已经写着了，别重复占地方
        const note = r.stateNote && !/^@known-bug/.test(r.stateNote) && r.stateNote !== '已废弃'
          ? r.stateNote : null
        const body = (
          <div style={{ height: CELL_H, overflow: 'hidden' }}>
            <div style={{ lineHeight: 1.6, ...clampTo(note ? 1 : 2) }}>
              <Rich text={v} />
              {r.claimedButUncovered && (
                <Tooltip title="清单标了 ✅ 但仓库里没有任何脚本声明这个 ID —— QA 自己的 check-coverage.sh 管这叫「抓清单说谎」，会 BLOCK">
                  <Tag style={{ marginLeft: 6 }} color="warning">清单未对上</Tag>
                </Tooltip>
              )}
            </div>
            {note && (
              <div style={{ fontSize: 11, color: C.gray, marginTop: 2, lineHeight: 1.5, ...clampTo(1) }}>
                <Rich text={note} />
              </div>
            )}
          </div>
        )
        // 短到没截断的行没必要挂浮层：悬浮弹一个和原文一模一样的框只是噪音
        const long = (v || '').length > 46 || note
        if (!long) return body
        return (
          <Popover
            placement="topLeft" title={`${r.id} 全文`}
            content={
              <div style={{ maxWidth: 660, maxHeight: 460, overflow: 'auto', lineHeight: 1.7, fontSize: 13 }}>
                <Rich text={v} />
                {note && (
                  <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${C.line}`, fontSize: 12, color: C.gray }}>
                    <Rich text={note} />
                  </div>
                )}
              </div>
            }
          >
            <div style={{ cursor: 'help' }}>{body}</div>
          </Popover>
        )
      },
    },
    {
      title: <Tooltip title="先做哪个。P0 最高">优先级</Tooltip>,
      dataIndex: 'priority', width: 74, align: 'center',
      sorter: (a, b) => (a.priority || 'P9').localeCompare(b.priority || 'P9'),
      sortOrder: sortOrderOf('priority'), key: 'priority',
      render: v => v
        ? <Tag style={{ margin: 0, color: PRIORITY_COLOR[v] || C.gray, background: 'transparent', borderColor: PRIORITY_COLOR[v] || C.line }}>{v}</Tag>
        : '—',
    },
    {
      title: <Tooltip title="风险分 = 概率(1–3) × 影响(1–3)，取值 1–9。决定要不要缓解，和优先级是两条独立的轴">
        <span>风险 <InfoCircleOutlined style={{ fontSize: 11, color: C.faint }} /></span>
      </Tooltip>,
      dataIndex: 'risk', width: 74, align: 'center',
      sorter: (a, b) => (a.risk || 0) - (b.risk || 0),
      sortOrder: sortOrderOf('risk'), key: 'risk',
      render: v => v == null ? '—' : (
        <span style={{
          display: 'inline-block', minWidth: 22, padding: '1px 6px', borderRadius: 4, fontSize: 12,
          color: riskColor(v), background: v >= HIGH_RISK ? `${riskColor(v)}14` : 'transparent',
          fontWeight: v >= HIGH_RISK ? 600 : 400,
        }}>{v}</span>
      ),
    },
    {
      title: <Tooltip title={Object.entries(TIER).map(([k, t]) => `${k}=${t.text}`).join(' · ')}>执行层</Tooltip>,
      dataIndex: 'tier', width: 92,
      render: v => v ? <Tooltip title={`${v} — ${TIER[v]?.desc || ''}`}><Tag style={{ margin: 0 }}>{tierText(v)}</Tag></Tooltip> : '—',
    },
    {
      title: '状态', dataIndex: 'state', width: 150,
      render: (v, r) => {
        const t = STATE_TAG[v] || STATE_TAG.gap
        return (
          <Space size={4} wrap={false}>
            <span style={{ color: t.color, background: t.bg, padding: '2px 8px', borderRadius: 10, fontSize: 12, whiteSpace: 'nowrap' }}>{t.text}</span>
            {r.knownBugs?.length > 0 && (
              <Tooltip title="有脚本，但脚本头上挂着 @known-bug —— 跑得通，结论是红的">
                <Tag color="error" style={{ margin: 0 }}>带缺陷</Tag>
              </Tooltip>
            )}
          </Space>
        )
      },
    },
    {
      title: '覆盖脚本', dataIndex: 'scripts', width: 212,
      // 平铺最多 2 条 —— 再多就把这一行撑得比场景列还高，行高又不齐了。
      // 现网最多的一条有 3 个脚本，第 3 条收进「+N」的悬浮里，不是丢掉。
      render: (list) => {
        if (!list?.length) return <span style={{ color: C.faint }}>—</span>
        const link = s => (
          <span
            onClick={() => openFile(s.path)}
            style={{
              fontSize: 12, fontFamily: 'var(--font-mono)', cursor: 'pointer', display: 'block',
              color: s.primary ? C.teal : C.gray, textDecoration: 'underline dotted',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}
          >
            <FileTextOutlined style={{ marginRight: 4, color: C.faint }} />
            {s.path.split('/').pop()}
          </span>
        )
        // 两行是硬预算：2 条正好铺满；超过 2 条就只铺 1 条，第二行让给「+N 个」，
        // 否则「+N」自己被 overflow 切掉 —— 那就成了「悄悄少显示几个脚本」，比截断更坏
        const shown = list.length > 2 ? list.slice(0, 1) : list
        const rest = list.slice(shown.length)
        return (
          <div style={{ maxHeight: CELL_H, overflow: 'hidden' }}>
            {shown.map(s => (
              <Tooltip key={s.path} title={`${s.path}（点开看内容）`}>{link(s)}</Tooltip>
            ))}
            {rest.length > 0 && (
              <Popover
                placement="topRight" title="还有这些脚本覆盖了它"
                content={<div style={{ minWidth: 200 }}>{rest.map(s => (
                  <div key={s.path} style={{ marginBottom: 2 }}>{link(s)}</div>
                ))}</div>}
              >
                <span style={{ fontSize: 11, color: C.faint, cursor: 'pointer' }}>+{rest.length} 个</span>
              </Popover>
            )}
          </div>
        )
      },
    },
    {
      title: (
        <Tooltip title="清单行和覆盖脚本，两边取更晚的那次改动。待补的场景没有脚本，看的就是这条需求是什么时候写进清单的">
          <span>更新时间 <InfoCircleOutlined style={{ fontSize: 11, color: C.faint }} /></span>
        </Tooltip>
      ),
      dataIndex: 'updatedAt', width: 116, key: 'updatedAt',
      // 按真实时刻排，**不能用字符串比** —— `%cI` 是按提交者时区渲染的，同一个仓库里
      // `2026-08-29T09:00:00Z`（北京 17:00）字典序小于 `2026-08-29T10:00:00+08:00`
      // （北京 10:00），拿 localeCompare 排这一列会把先后排反，而排反了不报错
      sorter: (a, b) => (a.updatedAt ? Date.parse(a.updatedAt) : -Infinity)
                      - (b.updatedAt ? Date.parse(b.updatedAt) : -Infinity),
      sortOrder: sortOrderOf('updatedAt'),
      render: (v, r) => {
        if (!v) return <span style={{ color: C.faint }}>—</span>
        // 两个分量分开显示：「脚本三个月没动、清单昨天刚改」和「两边一起改的」
        // 是两回事，只给一个合成值就分不出来了
        const rows = [
          ['清单行', r.rowUpdatedAt],
          ['覆盖脚本', r.scriptUpdatedAt],
        ].filter(([, t]) => t)
        // 别叫 tier：这个组件里已经有个 tier state（「执行层」筛选），
        // 在 render 里同名遮蔽早晚读错。跟 DomainWhen 里保持一致，叫 act。
        const act = activityTierOf(v, activityAnchor)
        return (
          <Tooltip title={
            <div style={{ fontSize: 12 }}>
              {rows.map(([k, t]) => (
                <div key={k}>{k}：{absWhen(t)}</div>
              ))}
              {!r.scriptUpdatedAt && <div style={{ opacity: 0.75, marginTop: 4 }}>还没有脚本</div>}
              {act && (
                <div style={{ opacity: 0.75, marginTop: 4 }}>
                  {act.dot} <b>{act.label}</b> —— {act.note}
                </div>
              )}
            </div>
          }>
            {/* 跟上面「按域看缺口」用同一套冷热档：同一个页面里"时间的颜色"
                只能有一个意思。锚点也是同一个（本仓最后一次动静），所以
                场景行和域行的同一个颜色代表同一件事。
                原来整列一支 C.gray —— 一屏几十行，哪条是刚改的看不出来。 */}
            <span style={{
              fontSize: 12, whiteSpace: 'nowrap', cursor: 'help',
              color: act ? act.color : C.faint,
              fontWeight: act ? act.weight : 400,
            }}>
              {act?.dot && <span style={{ marginRight: 3 }}>{act.dot}</span>}
              {relWhen(v, renderedAt)}
            </span>
          </Tooltip>
        )
      },
    },
    {
      // 210 → 132：这一列只显示单号（`b.split(/\s+/)[0]`），全文本来就在悬浮里 ——
      // 一个 `GL#530` 用不了 210px，多出来的宽度直接压着旁边的「场景」列。
      title: '已知缺陷', dataIndex: 'knownBugs', width: 132,
      render: (list) => !list?.length ? <span style={{ color: C.faint }}>—</span> : (
        <Space direction="vertical" size={2}>
          {list.map((b, i) => (
            <Tooltip key={i} title={b}>
              <Tag icon={<BugOutlined />} color="error" style={{ maxWidth: 112, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {b.split(/\s+/)[0]}
              </Tag>
            </Tooltip>
          ))}
        </Space>
      ),
    },
  ]

  const coverRate = summary?.total ? Math.round((summary.covered / summary.total) * 100) : 0
  // 「落后 N 个提交」算不出来（要每次开页面打一次网络），但「多久没拉过」是本地就有的，
  // 而且过期的页恰恰是没人点过「拉取最新」的那种 —— 超过 6 小时标黄，别让人拿着旧数字做判断。
  const fetchAge = useMemo(() => {
    if (!repo?.fetchedAt) return null
    const mins = Math.round((Date.now() - new Date(repo.fetchedAt).getTime()) / 60000)
    if (mins < 1) return { stale: false, text: '刚刚' }
    if (mins < 60) return { stale: false, text: `${mins} 分钟前` }
    const hours = Math.round(mins / 60)
    if (hours < 24) return { stale: hours >= 6, text: `${hours} 小时前` }
    return { stale: true, text: `${Math.round(hours / 24)} 天前` }
  }, [repo?.fetchedAt])
  // 「读不进来的行」也算不可信：那不是"对不上"，是我们根本没读到，比对不上更该先看。
  // 「读串了」比「读掉了」还该先看：行一条不少、值全是错的，所有指标照样算得出来。
  const healthy = summary && !summary.claimedButUncovered && !summary.orphanScripts
    && !summary.unparsedRows && !summary.duplicateIds
    && !summary.unresolvedColumns && !summary.unknownStateTokens
  const parseLoss = (summary?.unparsedRows || 0) + (summary?.duplicateIds || 0)
  const parseConfusion = (summary?.unresolvedColumns || 0) + (summary?.unknownStateTokens || 0)

  const sourceDetail = repo && (
    <div style={{ maxWidth: 480, fontSize: 12, lineHeight: 2 }}>
      <div>仓库 <code>{repo.url}</code></div>
      <div>分支 <code>{repo.branch}</code>{repo.branchAuto && <Tag color="blue" style={{ marginLeft: 4 }}>跟默认分支</Tag>}</div>
      <div>清单 <code>{repo.catalogPath}</code>
        <Tag color={repo.catalogAuto ? 'blue' : 'default'} style={{ marginLeft: 4 }}>{repo.catalogAuto ? '自动识别' : '配置指定'}</Tag>
      </div>
      <div>脚本 {summary?.scripts ?? 0} 个
        <Tag color={repo.caseDiscovery === 'grep' ? 'blue' : 'default'} style={{ marginLeft: 4 }}>
          {repo.caseDiscovery === 'grep' ? '按 @scenario 自动捞' : '按配置的 glob'}
        </Tag>
      </div>
      <div>commit <code>{repo.commitShort}</code> {repo.commitSubject}</div>
      <div>提交于 {repo.commitDate ? new Date(repo.commitDate).toLocaleString('zh-CN') : '—'}</div>
      <div>拉取于 {repo.fetchedAt ? new Date(repo.fetchedAt).toLocaleString('zh-CN') : '—'}
        {fetchAge && <Tag color={fetchAge.stale ? 'orange' : 'green'} style={{ marginLeft: 4 }}>{fetchAge.text}</Tag>}
      </div>
      <div style={{ color: C.gray, marginTop: 6 }}>
        平台对这个仓库只读：clone --bare / fetch / git show，不写一个字。
      </div>
    </div>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 600, color: C.ink, margin: 0 }}>QA 对账</h2>
          <div style={{ fontSize: 12, color: C.gray, marginTop: 2 }}>
            QA 维护的验收场景分母 + 仓库里真实存在的脚本分子，两边对照着看。平台只读，不回写。
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
          <Space>
            <Popover content={LEGEND} title="这一页的列都是什么意思" placement="bottomRight">
              <Button icon={<InfoCircleOutlined />} type="text">怎么读这一页</Button>
            </Popover>
            {canConfig && (
              <Button icon={<SettingOutlined />} onClick={openConfig}>{configured ? '仓库配置' : '配置 QA 仓'}</Button>
            )}
            <Button icon={<ReloadOutlined />} onClick={handleRefresh} loading={refreshing} disabled={!configured}>
              拉取最新
            </Button>
          </Space>
          {/* 拉取时间和 commit 必须挨着「拉取最新」：点完按钮眼睛就停在这儿，
              「这份数字是什么时候、哪个 commit 的」正是这一刻要回答的问题。
              以前压在页脚，而行高不齐时列表能有几千像素高 —— 那行字等于不存在。
              悬浮展开是完整来源（仓库/分支/清单/脚本数/提交主题）。 */}
          {configured && repo?.commitShort && (
            <Popover content={sourceDetail} title="QA 仓（只读）" placement="bottomRight">
              <div style={{ fontSize: 12, color: fetchAge?.stale ? C.orange : C.gray, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                {repo.fetchedAt
                  ? `拉取于 ${new Date(repo.fetchedAt).toLocaleString('zh-CN')}${fetchAge ? `（${fetchAge.text}）` : ''}`
                  : '还没拉取过'}
                {' · '}
                <code style={{ textDecoration: 'underline dotted' }}>{repo.commitShort}</code>
              </div>
            </Popover>
          )}
        </div>
      </div>

      {!loading && configured === false && (
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="尚未配置 QA 仓"
          description={
            <span>
              这个项目还没有配置 QA 仓，下面只显示表头。点右上角
              {canConfig && (
                <Button type="link" size="small" style={{ padding: '0 4px' }} onClick={openConfig}>配置 QA 仓</Button>
              )}
              填上仓库地址就行 —— 分支、清单路径、脚本范围都能自己认出来。
              平台对该仓库只读：只做 clone / fetch，不会写入任何内容。
            </span>
          }
        />
      )}

      {data?.error && (
        <Alert
          type="error" showIcon style={{ marginBottom: 16 }}
          message="读取 QA 仓失败" description={data.error}
          action={canConfig && <Button size="small" onClick={openConfig}>改配置</Button>}
        />
      )}

      {configured && summary && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 12, flexWrap: 'wrap', alignItems: 'stretch' }}>

          {/* 1. 覆盖到哪了 —— 并且说清「已覆盖」不等于「跑绿了」 */}
          <Panel
            title="覆盖到哪了"
            extra={<span style={{ fontSize: 11, color: C.gray }}>不含 {summary.deprecated} 条已废弃</span>}
          >
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 30, fontWeight: 600, color: C.teal, lineHeight: 1 }}>{coverRate}%</span>
              <span style={{ fontSize: 12, color: C.gray }}>
                {summary.covered} / {summary.total} 条清单里有脚本认领
              </span>
            </div>
            {['P0', 'P1', 'P2', 'P3'].filter(p => summary.byPriority?.[p]).map(p => {
              const s = summary.byPriority[p]
              return (
                <Hit key={p} active={priority === p && !state && !quick} onClick={() => jump({ priority: p })}>
                  <span style={{ width: 22, color: PRIORITY_COLOR[p] }}>{p}</span>
                  <Progress
                    percent={s.total ? Math.round((s.covered / s.total) * 100) : 0}
                    size="small" strokeColor={PRIORITY_COLOR[p]} style={{ flex: 1, margin: 0 }} showInfo={false}
                  />
                  <span style={{ color: C.gray, width: 60, textAlign: 'right' }}>{s.covered}/{s.total}</span>
                </Hit>
              )
            })}
            {/* 「N 条挂着缺陷」和「几个缺陷单」是两个数：一个单子常压住好几条场景
                （实测 F-5 一个号压住 8 条）。只给前一个数，会被读成"有 12 个缺陷要修" */}
            {summary.coveredWithBugs > 0 && (
              <div style={{ marginTop: 8 }}>
                <Hit active={quick === 'bugs'} onClick={() => jump({ quick: 'bugs' })} style={{ color: C.red }}>
                  <WarningFilled />
                  <span style={{ flex: 1 }}>
                    <b>{summary.coveredWithBugs}</b> 条挂着已知缺陷，归到
                    {bugRefs.length > 0 ? (
                      <Popover
                        placement="bottomLeft"
                        title={<span style={{ fontSize: 12 }}>这些红在等 {bugRefs.length} 个缺陷单</span>}
                        content={
                          <div style={{ maxWidth: 360, fontSize: 12, lineHeight: 1.9 }}>
                            {bugRefs.map(b => (
                              <div key={b.ref}>
                                <code style={{ color: C.red }}>{b.ref}</code>
                                <span style={{ color: C.gray }}> 压住 {b.scenarios.length} 条 · </span>
                                {b.scenarios.join(' ')}
                              </div>
                            ))}
                            <div style={{ color: C.gray, marginTop: 8, lineHeight: 1.6 }}>
                              缺陷号取自脚本头的 <code>@known-bug</code>。修掉一个单子，
                              上面对应的那几条一起转绿 —— 所以要排期的是这 {bugRefs.length} 个，
                              不是 {summary.coveredWithBugs} 个。
                            </div>
                          </div>
                        }
                      >
                        <span
                          onClick={e => e.stopPropagation()}
                          style={{ cursor: 'help', borderBottom: `1px dotted ${C.red}`, margin: '0 2px' }}
                        >
                          <b>{bugRefs.length}</b> 个缺陷单
                        </span>
                      </Popover>
                    ) : <b> —</b>}
                  </span>
                </Hit>
                <div style={{ fontSize: 11, color: C.gray, paddingLeft: 20, lineHeight: 1.5 }}>
                  有脚本，但结论已知是红的
                </div>
              </div>
            )}
          </Panel>

          {/* 2. 还欠多少 —— 一个数字要能直接变成明天的活儿 */}
          <Panel title="还欠多少" extra={<span style={{ fontSize: 11, color: C.gray }}>清单标 ⬜ 待补</span>}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 30, fontWeight: 600, color: C.orange, lineHeight: 1 }}>{summary.gap}</span>
              <span style={{ fontSize: 12, color: C.gray }}>条场景还没有任何脚本</span>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
              {['P0', 'P1', 'P2', 'P3'].filter(p => summary.byPriority?.[p]?.gap).map(p => (
                <Tag
                  key={p} onClick={() => jump({ priority: p, state: 'gap', sortRisk: true })}
                  color={priority === p && state === 'gap' ? PRIORITY_COLOR[p] : undefined}
                  style={{ cursor: 'pointer', margin: 0, borderColor: PRIORITY_COLOR[p] }}
                >
                  {p} 缺 {summary.byPriority[p].gap}
                </Tag>
              ))}
            </div>
            <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 8 }}>
              <div style={{ fontSize: 11, color: C.gray, marginBottom: 4 }}>
                要挑一批今天就动手的，就挑这批：
              </div>
              <Button
                size="small" danger={urgentCount > 0} type={quick === 'urgent' ? 'primary' : 'default'}
                onClick={() => jump({ quick: 'urgent', sortRisk: true })} disabled={!urgentCount}
              >
                P0 待补 · 风险 9 —— {urgentCount} 条
              </Button>
            </div>
          </Panel>

          {/* 3. 清单可信吗 —— 前两项是 QA 自己门禁会 BLOCK 的，不该埋在页面底部 */}
          <Panel
            title="清单可信吗"
            tone={healthy ? undefined : 'bad'}
            extra={healthy
              ? <span style={{ fontSize: 11, color: C.teal }}><CheckCircleFilled /> 清单和脚本对得上</span>
              : <span style={{ fontSize: 11, color: C.red }}><WarningFilled /> 有对不上的</span>}
          >
            <Hit active={quick === 'lying'} onClick={() => summary.claimedButUncovered && jump({ quick: 'lying' })}>
              {summary.claimedButUncovered
                ? <WarningFilled style={{ color: C.red }} />
                : <CheckCircleFilled style={{ color: C.teal }} />}
              <span style={{ flex: 1 }}>标了 ✅ 却没有任何脚本</span>
              <b style={{ color: summary.claimedButUncovered ? C.red : C.gray }}>{summary.claimedButUncovered}</b>
            </Hit>
            <Hit>
              {summary.orphanScripts
                ? <WarningFilled style={{ color: C.red }} />
                : <CheckCircleFilled style={{ color: C.teal }} />}
              <span style={{ flex: 1 }}>脚本声明了清单外的 ID</span>
              <b style={{ color: summary.orphanScripts ? C.red : C.gray }}>{summary.orphanScripts}</b>
            </Hit>
            <Hit active={quick === 'mismatch'} onClick={() => summary.riskMismatch && jump({ quick: 'mismatch' })}>
              {summary.riskMismatch
                ? <WarningFilled style={{ color: C.orange }} />
                : <CheckCircleFilled style={{ color: C.teal }} />}
              <span style={{ flex: 1 }}>风险 ≥{HIGH_RISK} 却排在 P2/P3</span>
              <b style={{ color: summary.riskMismatch ? C.orange : C.gray }}>{summary.riskMismatch}</b>
            </Hit>
            {/* 这一行 0 也要显示：只在出问题时才冒出来的指标，跟"没算过"长得一模一样，
                而这里少读一行的后果是那条场景在页面上根本不存在 —— 覆盖率不掉、缺口不涨 */}
            <Popover
              placement="bottomLeft"
              title={<span style={{ fontSize: 12 }}>解析这份清单时丢掉的行</span>}
              content={
                <div style={{ maxWidth: 460, fontSize: 12, lineHeight: 1.8 }}>
                  {parseLoss === 0 && (
                    <div style={{ color: C.teal }}>
                      <CheckCircleFilled /> 清单里每一行都读进来了，
                      上面的 {summary.total} 条就是清单的全部。
                    </div>
                  )}
                  {catalogIssues?.unparsedRows?.map(r => (
                    <div key={r.line} style={{ marginBottom: 4 }}>
                      <span style={{ color: C.gray }}>第 {r.line} 行 </span>
                      <code style={{ fontSize: 11, wordBreak: 'break-all' }}>{r.raw}</code>
                    </div>
                  ))}
                  {catalogIssues?.duplicateIds?.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      同一个 ID 出现了两次（只留了第一条）：
                      <b style={{ color: C.orange }}> {catalogIssues.duplicateIds.join(' ')}</b>
                    </div>
                  )}
                  <div style={{ color: C.gray, marginTop: 8, lineHeight: 1.6 }}>
                    首列像场景 ID、整行却没解析成 —— 常见是行尾少一根 <code>|</code>、
                    短横打成了中文破折号、域码写成小写。丢掉一行不会让覆盖率变低，
                    只会让那条场景「不存在」，所以这里必须自己报出来。
                  </div>
                </div>
              }
            >
              {/* Popover 靠 cloneElement 往 child 上挂 onMouseEnter/ref，而 Hit 自己就用了
                  这两个名字、也不透传 ref —— 直接把 Hit 当 child 会一辈子弹不出来 */}
              <div>
                <Hit style={{ cursor: 'help' }}>
                  {parseLoss
                    ? <WarningFilled style={{ color: C.red }} />
                    : <CheckCircleFilled style={{ color: C.teal }} />}
                  <span style={{ flex: 1 }}>清单里读不进来的行</span>
                  <b style={{ color: parseLoss ? C.red : C.gray }}>{parseLoss}</b>
                </Hit>
              </div>
            </Popover>
            {/* 这一行是 2026-08-30 补的。网关那份清单列序跟 uag 不一样，老解析器按列位
                硬读，把「类型」当优先级、真正的状态列根本没读到 —— 268 行整份判成缺口，
                而上面每一盏灯都是绿的（行一条没少、ID 没重复、error 也是 null）。
                所以「读串了」必须自己有一盏灯，而且**认列结果要能展开看**：
                「状态 = 第几列」是唯一能让人一眼对出来的东西。 */}
            <Popover
              placement="bottomLeft"
              title={<span style={{ fontSize: 12 }}>这份清单的列是怎么认出来的</span>}
              content={
                <div style={{ maxWidth: 480, fontSize: 12, lineHeight: 1.8 }}>
                  <div style={{ color: C.gray, marginBottom: 6 }}>
                    列是按<b>每列的值长什么样</b>认的，不按列位 —— 换个项目的清单
                    （列少几个、顺序不同、状态写中文词）不用改代码。
                  </div>
                  {(catalogIssues?.columnRoles || []).map(c => (
                    <div key={c.index}>
                      第 {c.index + 1} 列
                      {c.header ? <code style={{ margin: '0 4px' }}>{c.header}</code> : ' '}
                      → <b style={{ color: C.teal }}>{COLUMN_ROLE_CN[c.role] || c.role}</b>
                      <span style={{ color: C.gray }}>（{c.basis}）</span>
                    </div>
                  ))}
                  {catalogIssues?.unresolvedColumns?.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <b style={{ color: C.orange }}>没认出角色的列（一个字都没往里填）：</b>
                      {catalogIssues.unresolvedColumns.map(c => (
                        <div key={c.index}>
                          第 {c.index + 1} 列
                          {c.header ? <code style={{ margin: '0 4px' }}>{c.header}</code> : ' '}
                          <span style={{ color: C.gray }}>
                            {c.count} 行有值，例如 {c.samples.slice(0, 3).join(' / ')}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                  {catalogIssues?.unknownStateTokens?.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <b style={{ color: C.orange }}>词表里没有的状态写法（平台自己反推的）：</b>
                      {catalogIssues.unknownStateTokens.map(t => (
                        <div key={t.token}>
                          <code>{t.token}</code> × {t.count} → <b>{STATE_TAG[t.resolvedAs]?.text || t.resolvedAs}</b>
                          <span style={{ color: C.gray }}>（{t.basis}）</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{ color: C.gray, marginTop: 8, lineHeight: 1.6 }}>
                    认不出来的列<b>一律空着</b>，绝不猜着往某个字段里塞；没见过的状态词
                    拿「有没有脚本声明过这条场景」反推，也绝不默认判缺口 ——
                    把「没读懂」写成一个确定的结论，之后就再也看不出这里发生过什么。
                  </div>
                </div>
              }
            >
              <div>
                <Hit style={{ cursor: 'help' }}>
                  {parseConfusion
                    ? <WarningFilled style={{ color: C.red }} />
                    : <CheckCircleFilled style={{ color: C.teal }} />}
                  <span style={{ flex: 1 }}>清单里读不懂的列 / 状态写法</span>
                  <b style={{ color: parseConfusion ? C.red : C.gray }}>{parseConfusion}</b>
                </Hit>
              </div>
            </Popover>
            <div style={{ fontSize: 11, color: C.gray, marginTop: 8, lineHeight: 1.6 }}>
              前两项是 QA 自己门禁（<code>check-coverage.sh</code>）会直接 BLOCK 的；
              第三项是「回去重新审优先级」的信号，不阻断；后两项是我们自己的解析靠不靠谱 ——
              分别是「行读掉了没」和「列读串了没」，鼠标停上去能看到认列结果。
            </div>
          </Panel>
        </div>
      )}

      {/* 按域看：24 个域一屏看完。位置按域码钉死 —— 见 domainRows 上面那段 */}
      {configured && domainRows.length > 0 && (
        <Collapse
          size="small" defaultActiveKey={['d']} style={{ marginBottom: 12 }}
          items={[{
            key: 'd',
            label: <span style={{ fontSize: 13 }}>
              按域看缺口（{domainRows.length} 个域 · 按域码排，位置不随进度动 · 点一行筛这个域 ·
              点「AI 评审」看这个域的脚本撑不撑得起清单）
              {summary?.activityUnavailable ? (
                <Tag color="warning" style={{ marginLeft: 8 }}>更新时间这次没算出来</Tag>
              ) : summary?.activityTruncated ? (
                <Tooltip title="只走了最近 5000 个提交。更早改过的域会显示成「更早」，不是「没动过」">
                  <Tag color="warning" style={{ marginLeft: 8 }}>时间只算到最近 5000 个提交</Tag>
                </Tooltip>
              ) : null}
            </span>,
            // 584 = 这一行的 min-content（在浏览器里量的，不是估的）。多了「最近更新」
            // 那一格之后它从 498 涨到 592，评审那格 88→80 之后又降 8 —— min 跟不上就是
            // 进度条被挤成 0 宽（min 给大了则是白留一条空档，两边都得跟着改）
            children: (<>
              <div style={{ fontSize: 11, color: C.gray, margin: '0 0 8px', display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
                <span>进度条<b>长短</b> = 覆盖率，<b>颜色</b> = 缺的是什么：</span>
                {COVER_STROKE.map(cs => (
                  <Tooltip key={cs.key} title={cs.note}>
                    <span style={{ cursor: 'help', whiteSpace: 'nowrap' }}>
                      <span style={{ display: 'inline-block', width: 18, height: 5, borderRadius: 3, background: cs.color, marginRight: 5, verticalAlign: 'middle' }} />
                      {cs.label}
                    </span>
                  </Tooltip>
                ))}
                <span style={{ color: C.faint }}>
                  <WarningFilled style={{ color: C.orange, marginRight: 4 }} />
                  评审徽标前有这个 = 评完之后这个域又动过，结论已过期
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(600px, 1fr))', gap: '2px 24px' }}>
                {domainRows.map(d => {
                  const rv = reviews[d.code]
                  return (
                    <Hit key={d.code} active={domain === d.code} onClick={() => jump({ domain: domain === d.code ? undefined : d.code })}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, width: 40 }}>{d.code}</span>
                      <span style={{ width: 110, color: C.gray, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                      <Progress
                        percent={d.total ? Math.round((d.covered / d.total) * 100) : 0}
                        size="small" showInfo={false} strokeColor={coverStrokeOf(d).color}
                        style={{ flex: 1, margin: 0, minWidth: 60 }}
                      />
                      <span style={{ width: 52, textAlign: 'right', color: C.gray }}>{d.covered}/{d.total}</span>
                      <span style={{ width: 96, textAlign: 'right', color: d.gap ? C.orange : C.faint }}>
                        缺 {d.gap}{d.p0Gap ? <b style={{ color: C.red }}> · P0 {d.p0Gap}</b> : null}
                      </span>
                      <DomainWhen d={d} now={renderedAt} anchor={activityAnchor} />
                      {/* 80 → 96。原来这一格里最宽的是「AI 评审」按钮（实测 62px），
                          徽标最长「多数不实」实测 69px；过期时徽标前面多一个警告标
                          （12px 图标 + 4px 间距 = 16），69 + 16 = 85 就顶破 80 了。
                          留 96 是给它一点余量。上面 grid 的 minmax 跟着 +16（584 → 600）——
                          那个 584 是量出来的 min-content，这一格宽了它就得跟着涨，
                          不涨就是进度条被挤掉宽度。 */}
                      <span style={{ width: 96, textAlign: 'right' }} onClick={e => e.stopPropagation()}>
                        {REVIEW_RUNNING(rv?.status) ? (
                          <Tag icon={<LoadingOutlined />} color="processing" style={{ margin: 0, cursor: 'pointer' }}
                               onClick={() => setOpenReview(rv)}>评审中</Tag>
                        ) : rv?.status === 'done' ? (
                          // 徽标是缩写版，长句和时间都在悬浮里补齐 —— 缩了字不等于可以不说全
                          <ReviewBadge d={d} rv={rv} now={renderedAt} onOpen={setOpenReview} />
                        ) : rv?.status === 'failed' ? (
                          <Tag color="error" style={{ margin: 0, cursor: 'pointer' }}
                               onClick={() => setOpenReview(rv)}>没评上</Tag>
                        ) : canGenerate ? (
                          <Button
                            type="link" size="small" icon={<RobotOutlined />}
                            style={{ padding: 0, height: 'auto', fontSize: 12 }}
                            onClick={() => { setReviewFor(d); setEnvId(envs[0]?.id) }}
                          >AI 评审</Button>
                        ) : null}
                      </span>
                    </Hit>
                  )
                })}
              </div>
            </>),
          }]}
        />
      )}

      <Card styles={{ body: { padding: 16 } }}>
        <Space wrap style={{ marginBottom: 12 }}>
          <Input
            placeholder="搜索 ID / 场景 / 脚本路径" prefix={<SearchOutlined />} allowClear
            value={keyword} onChange={e => setKeyword(e.target.value)} style={{ width: 240 }}
          />
          <Select
            placeholder="域" allowClear value={domain} onChange={setDomain} style={{ width: 200 }}
            // 24 个域，翻着找太慢。label 里域码和中文名都在，打 MCP 或「能力」都能命中
            showSearch optionFilterProp="label"
            options={(data?.domains || []).map(d => ({
              value: d.code,
              label: `${d.code}${d.name ? ' · ' + d.name : ''}（${d.covered}/${d.total}）`,
            }))}
          />
          <Select placeholder="优先级" allowClear value={priority} onChange={setPriority} style={{ width: 110 }}
            options={['P0', 'P1', 'P2', 'P3'].map(p => ({ value: p, label: p }))} />
          <Select placeholder="执行层" allowClear value={tier} onChange={setTier} style={{ width: 140 }}
            options={tiers.map(t => ({ value: t, label: `${tierText(t)}（${t}）` }))} />
          <Select placeholder="状态" allowClear value={state} onChange={setState} style={{ width: 130 }}
            options={[
              { value: 'covered', label: '✅ 已覆盖' },
              { value: 'gap', label: '⬜ 待补' },
              { value: 'deprecated', label: '❌ 已废弃' },
            ]} />
          {quick && (
            <Tag color="processing" closable onClose={() => setQuick()} style={{ margin: 0 }}>
              {QUICK[quick].label}
            </Tag>
          )}
          {hasFilter && (
            <Button size="small" type="text" icon={<CloseCircleOutlined />} onClick={clearFilters}>清除筛选</Button>
          )}
          {summary?.deprecated > 0 && (
            <Checkbox checked={showDeprecated} onChange={e => setShowDeprecated(e.target.checked)}>
              <span style={{ fontSize: 12, color: C.gray }}>显示已废弃（{summary.deprecated}）</span>
            </Checkbox>
          )}
          <span style={{ fontSize: 12, color: C.gray }}>共 {filtered.length} 条</span>
        </Space>

        <Table
          rowKey="id"
          columns={columns}
          dataSource={filtered}
          loading={loading}
          size="small"
          // 行高很不齐：带判据说明的场景单行能到 200px+，20 行铺开就是几千像素，
          // 分页器被推到十几屏之外，翻页得先滚半天。
          // 表体自己滚（表头跟着固定），整页高度才可预期。写法跟用例管理页保持一致，
          // 不写死 px：小屏会被撑爆，大屏又白白浪费。
          scroll={{ x: 1300, y: 'calc(100vh - 300px)' }}
          onChange={(_p, _f, s) => setSorter({ columnKey: s?.columnKey, order: s?.order })}
          pagination={{
            current: page, pageSize, showSizeChanger: true, showTotal: t => `共 ${t} 条`,
            // 两个都得收：只接 page 的话，换每页条数会被受控的 pageSize 按回原值
            onChange: (p, s) => { setPage(p); setPageSize(s) },
          }}
        />
      </Card>

      {configured && data?.orphanScriptList?.length > 0 && (
        <Card
          title="声明了清单外 ID 的脚本"
          size="small"
          style={{ marginTop: 16 }}
          extra={<span style={{ fontSize: 12, color: C.gray }}>
            脚本声明的场景 ID 在清单里查无此条 —— 要么清单漏登记，要么脚本抄错了 ID
          </span>}
        >
          <Table
            rowKey="path" size="small" pagination={false}
            dataSource={data.orphanScriptList}
            columns={[
              {
                title: '脚本', dataIndex: 'path',
                render: p => (
                  <a onClick={() => openFile(p)} style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                    <FileTextOutlined style={{ marginRight: 4 }} />{p}
                  </a>
                ),
              },
              { title: '未知 ID', dataIndex: 'ids', render: v => v.join(' ') },
            ]}
          />
        </Card>
      )}

      {/* QA 仓配置：只在这一页维护 —— 它只影响这一页，认错了也只在这一页报错 */}
      <Modal
        title="QA 仓（只读）"
        open={cfgOpen}
        onCancel={() => setCfgOpen(false)}
        width={560}
        footer={[
          canConfig && data?.config?.url ? (
            <Popconfirm
              key="clear" title="取消配置后这一页只剩表头，确定？"
              onConfirm={() => saveConfig({ url: '', branch: '', catalogPath: '', caseGlobs: [] })}
            >
              <Button danger type="text" style={{ float: 'left' }}>取消配置</Button>
            </Popconfirm>
          ) : null,
          <Button key="cancel" onClick={() => setCfgOpen(false)}>取消</Button>,
          canConfig ? (
            <Button key="ok" type="primary" loading={saving} onClick={handleSaveConfig}>保存</Button>
          ) : null,
        ]}
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="平台对这个仓库永远只读"
          description="只做 clone --bare / fetch / git show，不写入、不建分支、也不要求对方仓库为我们加任何文件。"
        />
        <Form form={form} layout="vertical">
          <Form.Item
            name="url" label="仓库地址"
            rules={[{ required: true, message: '请输入 QA 仓地址' }]}
            extra="服务器要能免密访问它（SSH key / 只读 token）"
          >
            <Input placeholder="git@gitlab.example.com:qa/uag-qa.git" />
          </Form.Item>
          <Collapse
            ghost size="small"
            items={[{
              key: 'adv',
              label: <span style={{ fontSize: 13 }}>高级 · 三项都留空 = 自动识别</span>,
              children: (
                <>
                  <Form.Item name="branch" label="分支" extra="留空 = 跟仓库自己的默认分支走">
                    <Input placeholder="留空即可" />
                  </Form.Item>
                  <Form.Item name="catalogPath" label="场景清单文件" extra="留空 = 找场景行最多的那份 .md">
                    <Input placeholder="如 docs/test-scenario-catalog.md（留空即可）" />
                  </Form.Item>
                  <Form.Item
                    name="caseGlobs" label="用例脚本范围"
                    extra="留空 = 用 git grep 捞所有声明了 @scenario 的文件；填了就只认这些 glob（逗号分隔）"
                  >
                    <Input placeholder="如 api/**/*.sh, ui/tests/**/*.spec.ts（留空即可）" />
                  </Form.Item>
                </>
              ),
            }]}
          />
        </Form>
      </Modal>

      {/* 脚本原文：git show 出来的那份，只读 */}
      <Drawer
        title={<span style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}>{file?.path}</span>}
        open={!!file} onClose={() => setFile(null)} width={860}
        extra={file?.commitSha && <span style={{ fontSize: 12, color: C.gray }}>
          {file.lines} 行 · {(file.bytes / 1024).toFixed(1)} KB · <code>{file.commitSha.slice(0, 10)}</code>
        </span>}
      >
        {fileLoading ? <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div> : (
          <>
            {/* 点开脚本第一件想知道的事：它自己声明覆盖了哪几条、跟清单对不对得上 */}
            {(file?.header?.ids?.length > 0 || file?.header?.tier || file?.header?.knownBugs?.length > 0) && (
              <Space wrap size={4} style={{ marginBottom: 10 }}>
                <span style={{ fontSize: 12, color: C.gray }}>脚本头声明：</span>
                {(file.header.ids || []).map(id => <Tag key={id} color="blue" style={{ margin: 0 }}>{id}</Tag>)}
                {file.header.tier && <Tag style={{ margin: 0 }}>{tierText(file.header.tier)}</Tag>}
                {(file.header.knownBugs || []).map((b, i) => (
                  <Tag key={i} icon={<BugOutlined />} color="error" style={{ margin: 0 }}>{b}</Tag>
                ))}
              </Space>
            )}
            {file?.truncated && (
              <Alert type="warning" showIcon style={{ marginBottom: 10 }}
                     message="文件太大，只显示了前面一段" />
            )}
            <pre style={{
              margin: 0, padding: 12, background: '#0f1720', color: '#d8e0ea', borderRadius: 6,
              fontSize: 12, lineHeight: 1.7, overflow: 'auto', maxHeight: 'calc(100vh - 220px)',
              fontFamily: 'var(--font-mono)',
            }}>{file?.content}</pre>
          </>
        )}
      </Drawer>

      {/* 选环境 —— 环境是结论的一部分：脚本要的变量这个环境有没有，直接决定它跑不跑得起来 */}
      <Modal
        title={`AI 评审 · ${domainLabel(reviewFor?.code, reviewFor?.name)}`}
        open={!!reviewFor} onCancel={() => setReviewFor(null)}
        okText="开始评审" confirmLoading={starting} onOk={startReview}
        okButtonProps={{ disabled: !envs.length }} width={520}
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 16 }}
          message="只读这个域的清单和脚本，不跑任何东西"
          description="平台不会在这个环境上执行 QA 的脚本，也不会往 QA 仓写任何内容。结论只存在本平台。"
        />
        <div style={{ fontSize: 13, lineHeight: 2, marginBottom: 12 }}>
          <div>这个域共 <b>{reviewFor?.total || 0}</b> 条场景（已覆盖 {reviewFor?.covered || 0} · 待补 {reviewFor?.gap || 0}）</div>
          <div style={{ color: C.gray }}>
            重点看「声明覆盖了、其实没验到」—— 这正是 QA 自己的 <code>check-coverage.sh</code> 查不了的那一层。
          </div>
        </div>
        <div style={{ fontSize: 13, marginBottom: 6 }}>在哪个环境上评</div>
        {envs.length ? (
          <Select
            value={envId} onChange={setEnvId} style={{ width: '100%' }}
            options={envs.map(e => ({ value: e.id, label: e.name }))}
          />
        ) : (
          <Alert type="warning" showIcon message="这个项目还没配环境"
                 description="去「项目设置 → 环境」加一个再来，评审要拿环境的变量名跟脚本引用对账。" />
        )}
        <div style={{ fontSize: 12, color: C.gray, marginTop: 8 }}>
          只把环境的<b>变量名</b>交给模型对账（脚本要 <code>ADMIN_TOKEN</code>、这个环境有没有），
          <b>变量值一个字节都不会外传</b>。
        </div>
      </Modal>

      {/* 评审结论 */}
      <Drawer
        title={<Space>
          <span>AI 评审 · {domainLabel(openReview?.domain, openReview?.domainName)}</span>
          {openReview?.status === 'done' && (
            <Tag color={VERDICT[openReview.result?.verdict]?.color || 'default'} style={{ margin: 0 }}>
              {VERDICT[openReview.result?.verdict]?.text || '已评'}
            </Tag>
          )}
        </Space>}
        open={!!openReview} onClose={() => setOpenReview(null)} width={780}
        extra={openReview?.status === 'done' && canGenerate && (
          <Button size="small" icon={<RobotOutlined />} onClick={() => {
            const d = domainRows.find(x => x.code === openReview.domain)
            setOpenReview(null); setReviewFor(d || { code: openReview.domain }); setEnvId(openReview.environmentId || envs[0]?.id)
          }}>重评</Button>
        )}
      >
        {!openReview ? null : REVIEW_RUNNING(openReview.status) ? (
          <div style={{ textAlign: 'center', padding: '48px 0', color: C.gray }}>
            <Spin /><div style={{ marginTop: 12 }}>正在读这个域的 {openReview.scriptCount} 份脚本…几十秒，可以关掉页面</div>
          </div>
        ) : openReview.status === 'failed' ? (
          <Alert type="error" showIcon message="这次没评上" description={openReview.error} />
        ) : (
          <ReviewTabs r={openReview} onOpenFile={openFile} projectId={projectId} />
        )}
        {openReview && (
          <div style={{ marginTop: 20, paddingTop: 10, borderTop: '1px solid rgba(0,0,0,0.06)', fontSize: 12, color: C.gray, lineHeight: 1.9 }}>
            环境 <b>{openReview.environmentName || '—'}</b> · QA 仓 {openReview.branch} <code>{openReview.commitSha}</code>
            {' · '}{openReview.actor} 发起于 {openReview.createdAt && new Date(openReview.createdAt).toLocaleString('zh-CN')}
            <div>结论只存在本平台，QA 仓没有任何变化。</div>
          </div>
        )}
      </Drawer>
    </div>
  )
}

const SEVERITY = { blocker: C.red, major: C.orange, minor: C.gray }

// 判据回验（后端 `qa_evidence_check`：把模型给的 evidence 拿回脚本正文搜一遍）。
// 三档都算搜到 —— 真实判据经常是「第 12 行的断言 + 第 40 行的清理」拼起来的，
// 要求整块一字不差会把这类**真判据**判成编造。
const EV_PASS = ['verbatim', 'reflowed', 'stitched']
const EV_CN = {
  verbatim: '一字不差抄的', reflowed: '只有换行/缩进变了', stitched: '从正文几处拼起来的',
  'wrong-path': '判据是真的，但路径写错了', unmatched: '在这一批脚本里搜不到',
  too_short: '太短，搜到了也不算验过', empty: '没给判据',
}

// **从行本身数，不读后端那份 `coverage.evidence` 汇总。** 页面列的就是这些行，
// 同一个来源就不可能出现「上面写「12 条 grep 得到」、底下列着 9 条 ⚠」这种一屏里
// 两个数打架 —— 那种页面读的人只会得出「这页的数不能信」。
// 存量结论（回验上线之前评的）没有 evidenceCheck 键，**一律算没验过，不算验过**：
// 拿一句没验过的话去担保另一句没验过的话，正是这个模块要抓的形状。
// 旧后端 + 新前端也落在这一档（本仓后端故意不带 --reload），说出来好过悄悄挂个对勾。
function evidenceStats(gaps) {
  const rows = gaps || []
  const known = rows.filter(g => EV_CN[g.evidenceCheck])
  return {
    total: rows.length,
    unchecked: rows.length - known.length,
    verified: known.filter(g => EV_PASS.includes(g.evidenceCheck)).length,
  }
}

// 一次评审有两拨读者，需要的东西不是同一个东西 —— 所以分两页，不做成一页里的折叠。
//
// **人**（测试经理/项目经理）：三十秒决定要不要停下来处理。他不需要知道
// 是哪一句 `assert_status 200`，他需要知道"这个域标着已覆盖的 7 条里有 5 条是 P0，
// 这次运行一条都没执行"。细节混在里面，这句话就被埋掉了。
//
// **AI / 动手改脚本的人**：要的恰恰是被埋掉的那些 —— 哪个文件、哪一句、改成什么。
// 判据锚点（evidence）是从脚本正文原样抄的，能直接 grep 到。
//
// 默认停在「给人看」那一页：打开这个抽屉的十有八九是人。
function ReviewTabs({ r, onOpenFile, projectId }) {
  return (
    <Tabs
      size="small" defaultActiveKey="human"
      items={[
        { key: 'human', label: '给人看 · 结论', children: <ReviewBrief r={r} /> },
        {
          key: 'ai',
          label: '给 AI / 整改 · 细节',
          children: <ReviewBody r={r} onOpenFile={onOpenFile} projectId={projectId} />,
        },
      ]}
    />
  )
}

// 人话那一页。**只说结论和后果**，一个脚本路径都不出现。
// 「我是怎么看的」。别人第一次看到这份结论，第一个念头是"你凭什么这么说" ——
// 与其等他质疑，不如先把方法和边界摆出来。三句话，不解释术语。
// 「我是怎么看的」+「这次读了多少」合成一块，**默认折起来**。
// 这两段每个域都一模一样，24 个域就是同一段话读 24 遍 —— 第二个域起它就是噪声。
// 但也不能删：别人第一眼的质疑就是"你凭什么这么说"。折起来 = 想看的点开，
// 不想看的不占屏。⚠ 只有"没读全"那句是例外，它必须一直露在外面。
function HowIRead({ res, r }) {
  const [open, setOpen] = useState(false)
  const c = res.coverage || {}
  const total = c.scenariosTotal || res.scenarioCount || r?.scenarioCount || 0
  const shown = c.scenariosShown
  const missedS = shown != null && total > shown ? total - shown : 0
  const missedF = (c.scriptsTotal || 0) - (c.scriptsRead || 0)
  const cut = (res.reviewedScripts || []).filter(x => x.truncated).length
  const batches = c.batches || 1
  const read = c.scriptsRead || (res.reviewedScripts || []).length
  const failed = c.batchesFailed || []
  const ev = evidenceStats(res.scriptGaps)
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 12, color: C.gray, lineHeight: 1.9 }}>
        读了 <b style={{ color: C.ink }}>{read}</b> 份脚本正文
        {batches > 1 && `（分 ${batches} 批读完再合并）`}
        、{shown != null ? shown : total} 条场景，一份都没真跑。
        <a onClick={() => setOpen(v => !v)}
           style={{ marginLeft: 8, color: C.teal, cursor: 'pointer' }}>
          {open ? '收起' : '怎么看的？'}
        </a>
      </div>
      {(missedS > 0 || missedF > 0 || cut > 0) && (
        <div style={{ fontSize: 12, color: C.orange, lineHeight: 1.9 }}>
          ⚠ 这个域共 {total} 条场景
          {missedS > 0 && `，其余 ${missedS} 条这次没进模型`}
          {missedF > 0 && `；还有 ${missedF} 份脚本没读进来`}
          {cut > 0 && `；${cut} 份正文被截断（截断的不下结论）`}
          —— 上面的结论只覆盖读到的这部分。
        </div>
      )}
      {/* 有批次没读成，这句必须露在外面：少读一批 = 少读十几份脚本，
          而"少读了"和"没问题"在页面上长得一模一样 */}
      {failed.length > 0 && (
        <div style={{ fontSize: 12, color: C.red, lineHeight: 1.9 }}>
          ⚠ {failed.map(i => `第 ${i} 批`).join('、')}没读成（网关限流或超时），
          那几批的脚本这一趟等于没看 —— 重跑一次这个域就补上了。
        </div>
      )}
      {open && (
        <div style={{
          background: 'rgba(0,0,0,0.015)', border: `1px solid ${C.line}`,
          borderRadius: 8, padding: '12px 14px', marginTop: 8, fontSize: 12.5,
          color: C.gray, lineHeight: 2,
        }}>
          <div style={{ fontWeight: 600, color: C.ink }}>流程（每一步都只读，QA 仓一个字没动）</div>
          <div>① 取这个域的场景清单 —— 它<b>说要验</b>什么；</div>
          <div>② 取认领了这些场景的脚本正文 —— 它<b>实际在验</b>什么；</div>
          <div>③ 环境变量对账 —— <b>纯代码算的，不过模型</b>；只有变量<b>名</b>进提示词，值一个字节都不进；</div>
          <div>④ 一条条对，只问一个问题：<b style={{ color: C.ink }}>这条断言能不能失败？</b>
            改坏了会红才算真在验，恒真的断言跑绿等于没跑
            {batches > 1 && `（脚本一次装不进一轮对话，切成 ${batches} 批各读各的，每批都拿到完整场景清单 —— ${read} 份全读了，不是抽了几份）`}；</div>
          <div>⑤ 合并：结论取<b>最坏</b>的那一批（平均一下会把最要命的那批稀释掉），
            各堆的条数由代码数好、模型只许照抄；</div>
          <div>⑥ 渲染成这一页。<b>QA 那边自己来拉</b>（导出 / MCP），平台不往他仓里放任何东西。</div>

          <div style={{ fontWeight: 600, color: C.ink, marginTop: 8 }}>为什么一份都没跑</div>
          <div>· <b>跑不了</b>：脚本要 QA 自己那套运行环境，而且真跑会往被测系统写数据（造数、审批、删除）—— 那是别人的环境。</div>
          <div>· <b>更要紧的是不该靠跑</b>：这次要判的恰恰是「跑绿了但没验到」。
            恒真断言跑一万遍也是绿的，<b>跑本身对这个问题零信息量</b>。
            要判它只有两条路：读正文（这一趟做的），或者把动作删掉再跑看它变不变红 —— 后者要改人家的脚本，只读做不到。</div>
          <div>· <b>代价</b>：所以<b>脚本在真环境里跑不跑得起来，这份结论判不了</b>，那一半只有 QA 自己跑得出来。</div>

          <div style={{ fontWeight: 600, color: C.ink, marginTop: 8 }}>靠得住吗 —— 自己掂量这四条</div>
          {/* 这句话原来是**无条件**写死的 —— 一句自己没验过的承诺，而这个模块的
              全部意义就是抓「结论看起来有据、依据其实没验过」。现在它跟着回验结果走，
              导出的那份 Markdown 同理（`_导出结论` 里是同一套分支）。 */}
          {ev.unchecked > 0 ? (
            <div>· ⚠ <b>这份结论的判据没回验过</b>：它评在回验上线之前，
              {ev.total} 条判据平台一条都没搜过。要用就自己 grep 一遍。</div>
          ) : ev.total === 0 ? (
            <div>· ✅ <b>每条都能十秒内被否掉</b>：判据是从脚本正文原样抄的，
              grep 一下就知道我说得对不对。<b>这才是它能被信的理由，不是「AI 说的」。</b>
              （这一趟没有脚本级发现，没有可回验的判据。）</div>
          ) : (
            <div>· {ev.verified === ev.total ? '✅' : '⚠'} <b>判据回验过了</b>：
              {ev.total} 条判据平台已经拿回脚本正文搜过一遍，{ev.verified} 条 grep 得到，
              {ev.verified === ev.total ? '一条不落。' : (
                <><b>{ev.total - ev.verified} 条搜不到</b>，在「给 AI · 逐条」那页
                  逐条标着 —— 那几条先别照着改。</>
              )}
              <b>这才是它能被信的理由，不是「AI 说的」。</b></div>
          )}
          <div>· ⚠ <b>单趟单模型，没有第二意见</b>：同一份脚本再评一次，措辞会变、条数会差几条。
            拿它当「要不要停下来处理」的依据可以，别拿它当分数。</div>
          <div>· ⚠ <b>漏判是看不见的</b>：抓到多少不等于只有多少；某一格 0 条只等于这一趟没抓到。</div>
          <div>· ⚠ <b>环境那一列判的是我们这侧</b>：QA 自己跑的时候有没有那些变量，平台看不到。</div>
        </div>
      )}
    </div>
  )
}

// 人看那页的主体：**三个大维度一张表，查法缩进在底下**。
// 上一版是「抓到 46 条」+ 三栏各露 3 条一句话 —— 信息量按域的大小长，大域看不完。
// 第一次改成维度时直接摆了六条查法，结果是「你写的都是什么维度，我咋看不懂」——
// 那六条是**我怎么查的**，不是他脑子里的维度。现在顶层只有覆盖面 / 场景设置 / 断言，
// 三个数就够做决定；查法退到子项，想知道"凭什么这么判"的人才往下读。
// 手上这条评审没带 `dims` 时走这里。
//
// ⚠ 2026-08-29 这段只写了一条原因（「后端还跑着旧代码」），而当时**同时有两个 bug**，
// 它只说中了其中一个：
//   ① 后端确实跑着旧代码 —— 那份后端里 `with_dims` 一个字都没有，详情接口不发 `dims`。
//      **人截图时看到的就是这一条。**
//   ② 抽屉压根没去取过详情。列表接口**故意不发** `dims`（几十行每行挂一份同样的
//      口径常量），而抽屉一直拿列表行直接渲染 —— 就算后端是新的，这张表照样画不出来。
// 只写一条的代价不是"少写一句"：人照着它重启了后端，②还在，页面一模一样，
// 于是那句唯一的诊断从"帮忙"变成了"排除掉一个正确方向"。
// **降级文案的规矩：原因不确定就把候选全列上，别挑一个说得最像的当结论** ——
// 这跟这整个模块要抓的毛病是同一种（断言只验一条就宣布整件事成立）。
//
// 拿不到时前端**不自己重算一份**，只把结论里的原始维度标签**原样**列出来：
// 不猜名字、不摆 0，更**不许显示 `?`** —— 正常路径上 `?` 专指「这一趟没查」，
// 两个意思撞在一起就是一条假信息，比一片空白坏得多。
function DimUnavailable({ res }) {
  const n = {}
  ;[...(res.scriptGaps || []), ...(res.catalogGaps || [])].forEach(g => {
    const k = g.dim || '(模型没给它归维度)'
    n[k] = (n[k] || 0) + 1
  })
  const keys = Object.keys(n).sort((a, b) => n[b] - n[a])
  return (
    <div style={{ marginBottom: 16, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
      <div style={{ fontWeight: 600, color: C.orange }}>
        ⚠ 还没拿到维度口径，分好组的那张表画不出来
      </div>
      <div style={{ fontSize: 12, color: C.gray, lineHeight: 1.8, marginBottom: 6 }}>
        维度的中文名、分成哪三块、哪一项「这一趟没查」，全由后端随详情一起发
        （<code>GET …/qa-catalog/reviews/&#123;id&#125;</code> 的 <code>dims</code>；列表接口不发，
        几十行每行挂一份同样的口径太浪费）。拿不到时前端<b>不自己重算一份</b> ——
        重算就得再抄一份口径回来，抄的那份漂了会把「压根没查」渲染成一个漂亮的 0。
        <b>刚点开时闪一下是正常的</b>，详情还在路上。一直停在这里就是那一发没拿到，
        原因<b>不止一种，按这个顺序排掉</b>：① 后端跑着旧代码，那一版还没有
        <code>dims</code> 这个字段（<code>bash deploy/restart-backend.sh</code>，
        再对一眼进程启动时间和最新提交时间）；② 详情那一发失败了（刷新一次，看浏览器
        网络面板里这条是不是非 200）；③ 都不是的话看后端日志。
        下面是结论里的原始维度标签，按条数排：
      </div>
      {keys.length ? keys.map(k => (
        <div key={k} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '3px 0' }}>
          <span style={{ width: 26, flexShrink: 0, textAlign: 'right', fontWeight: 700,
                         fontVariantNumeric: 'tabular-nums', color: C.ink }}>{n[k]}</span>
          <code style={{ fontSize: 12.5, color: C.gray }}>{k}</code>
        </div>
      )) : <div style={{ fontSize: 12.5, color: C.hint }}>这一趟一条都没抓到</div>}
    </div>
  )
}

function DimTable({ res, r }) {
  // **口径由后端发**（`to_dict(..., with_dims=True)`）。以前这里是前端自己按一份
  // 抄来的常量重算的，注释写着「跟后端必须一字不差」—— 而那是一句没有任何东西
  // 在执行的话。拿不到就明说拿不到，不许为了"兜底"把那份副本抄回来。
  const rows = r?.dims
  if (!rows) return <DimUnavailable res={res} />
  const hit = rows.filter(d => d.count > 0).map(d => d.name)
  const stale = rows.reduce((a, d) => a + d.unavailable, 0)
  return (
    <div style={{ marginBottom: 16, paddingTop: 12, borderTop: `1px solid ${C.line}` }}>
      <div style={{ fontWeight: 600, color: C.ink }}>
        按维度看{hit.length ? ` —— ${hit.join('、')}都有问题` : ' —— 这一趟三块都没抓到'}
      </div>
      <div style={{ fontSize: 12, color: C.gray, lineHeight: 1.8, marginBottom: 6 }}>
        三个维度是固定的，24 个域横着比也是这三块。
        <b>某一格 0 条只说明这一趟没抓到，不等于那一块没问题。</b>
        {stale > 0 && (
          <div style={{ color: C.orange }}>
            ⚠ 有 {stale} 项标着「这一趟没查」：这个域是加这几条判据之前评的，
            模型没被问过它们 —— <b>重评一次这个域就补上了</b>，在那之前别读成没问题。
          </div>
        )}
      </div>
      {rows.map(d => (
        <div key={d.axis} style={{ paddingTop: 6, borderTop: `1px solid ${C.line}` }}>
          {/* 大维度那一行：人只看这三个数 */}
          <div style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
            <span style={{
              width: 26, flexShrink: 0, textAlign: 'right', fontWeight: 700, fontSize: 15,
              fontVariantNumeric: 'tabular-nums', color: d.count ? C.red : C.teal,
            }}>{d.count || '—'}</span>
            <span style={{ fontWeight: 600, color: d.count ? C.ink : C.gray }}>{d.name}</span>
            <span style={{ fontSize: 12, color: C.hint }}>{d.why}</span>
          </div>
          {/* 子项 = 怎么判的。想细看的人才往下读，大维度那三个数已经够做决定 */}
          {d.items.map(it => (
            <div key={it.key} style={{
              display: 'flex', gap: 10, alignItems: 'baseline', padding: '3px 0 3px 36px',
            }}>
              {/* 「没查」和「查了没抓到」都是 0，但意思正好相反 —— 混在一起就是假安心 */}
              <span style={{
                width: 18, flexShrink: 0, textAlign: 'right',
                fontVariantNumeric: 'tabular-nums', color: it.count ? C.orange : C.faint,
              }}>{it.unavailable ? '?' : it.count || '—'}</span>
              <span style={{ width: 178, flexShrink: 0, fontSize: 12.5,
                             color: it.count ? C.ink : C.gray }}>{it.name}</span>
              <span style={{ fontSize: 12, color: it.unavailable ? C.orange : C.hint,
                             lineHeight: 1.8 }}>
                {it.unavailable ? '这一趟没查 —— 这个域是加这条判据之前评的，重评一次就补上' : it.why}
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function ReviewBrief({ r }) {
  const res = r.result || {}
  const b = res.brief || {}
  const v = VERDICT[res.verdict]
  const gaps = res.scriptGaps || []
  // `catalogGaps` 也算进「谁动手」的总数：它此前只在「给 AI」那页露过，于是人看的这页
  // 出现过一句「清单要商量：17 处」底下却只列 1 条 —— 一屏里两个数打架，读的人只会
  // 得出「这页的数不能信」。
  const nCat = (res.catalogGaps || []).length
  const n = {
    script: gaps.filter(g => blameOf(g) === 'script').length,
    env: gaps.filter(g => blameOf(g) === 'env').length,
    catalog: gaps.filter(g => blameOf(g) === 'catalog').length + nCat,
  }
  const total = gaps.length + nCat
  // **只数 `absent`。** `ambiguous` 是"名字对不上、环境里有同族的"，
  // 把它算进「缺 N 个」等于把那条误报从列表挪到了摘要里 —— 摘要还更醒目。
  // 没标 state 的（存量结论）按真缺算：多一条要人看的行，好过悄悄洗白一个真缺口。
  const nEnvVar = (res.envMissing || []).filter(v => (v.state || 'absent') === 'absent').length
  return (
    <div style={{ fontSize: 13 }}>
      <div style={{
        padding: '14px 16px', borderRadius: 8, marginBottom: 14,
        background: 'rgba(0,0,0,0.02)', border: `1px solid ${C.line}`,
      }}>
        <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.8, color: C.ink }}>
          {b.headline || res.summary || '这一轮没给出结论'}
        </div>
        {v && (
          <div style={{ fontSize: 12.5, color: C.gray, marginTop: 8, lineHeight: 1.9 }}>
            这次判「<b style={{
              color: res.verdict === 'ok' ? C.teal : res.verdict === 'bad' ? C.red : C.orange,
            }}>{v.text}</b>」= {v.why}
            {/* 结论词的主语必须写出来。省掉主语，读的人会把它读成"评审只做了一半" */}
            <div style={{ color: C.hint, marginTop: 2 }}>{VERDICT_SUBJECT}</div>
            <div style={{ color: C.hint }}>{VERDICT_SOURCE}</div>
          </div>
        )}
        {/* 人话那段是拼接版时**必须当场说**。退回拼接之后 headline 是概述的前 120 字，
            底下的重点、下一步、撑得住的部分全是空的 —— 这一页于是长成
            「这个域没什么重点」，跟「总结那一趟根本没跑成」一模一样。
            2026-08-29 跑 TEM 时真撞到：明细 14+6 条都在，人看的这页是白的。
            折起来不行，这句要的就是拦住"没重点 = 没问题"这个念头。 */}
        {res.briefSource === 'stitched' && (
          <div style={{ fontSize: 12, color: C.red, marginTop: 8, lineHeight: 1.9 }}>
            ⚠ 上面这句是<b>拼接版</b>：把各批结论收成一段人话的那一趟没跑成（网关限流或超时），
            所以只剩一句概述，下面的重点和「下一步」是空的 ——
            <b>「没列重点」是这次没写出来，不是这个域没有重点</b>。
            逐条发现一条没少，重跑一次这个域就补上了。
          </div>
        )}
        {/* 存量结论没这个键。**不许当成"收口跑成了"** —— 老记录里同样混着收口挂过的，
            折进去就是把「不知道」渲染成「跑成了」。 */}
        {!res.briefSource && (
          <div style={{ fontSize: 12, color: C.hint, marginTop: 8, lineHeight: 1.9 }}>
            这一趟没记「上面这段是怎么来的」（旧口径评的，当时不区分「收口跑成了」和
            「收口挂了退回拼接」）—— 底下的重点要是空的，别读成这个域没有重点。
          </div>
        )}
      </div>

      <HowIRead res={res} r={r} />

      <DimTable res={res} r={r} />

      {total > 0 && (
        <div style={{ fontSize: 12.5, color: C.gray, lineHeight: 2, marginBottom: 14 }}>
          这 <b style={{ color: C.ink }}>{total}</b> 条<b>按谁动手分</b>：
          <b style={{ color: C.red }}>QA 改脚本 {n.script} 条</b>
          <span style={{ color: C.faint }}> · </span>
          我们这侧铺环境 {n.env} 条
          <span style={{ color: C.faint }}> · </span>
          找 QA 对清单口径 {n.catalog} 条。
          {/* 变量个数和场景条数是两码事，一屏之内并排出现过 10 和 7，得说清是哪个 */}
          {nEnvVar > 0 && `（环境那几条的根子：我们这条环境记录里缺 ${nEnvVar} 个变量名，
            值要在真正跑套件的地方注入，平台这边补上也不会让 QA 的脚本真跑起来。）`}
          <div style={{ color: C.hint }}>
            要逐条看（哪个文件、哪一句、改成什么）—— 切到隔壁「给 AI / 整改」那一页。
          </div>
        </div>
      )}

      {b.solid?.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontWeight: 600, color: C.ink, marginBottom: 4 }}>撑得住的部分</div>
          {b.solid.map((x, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 0', lineHeight: 1.9 }}>
              <span style={{ color: C.teal, flexShrink: 0 }}>✓</span>
              <span style={{ fontSize: 12.5, color: C.gray }}>{x}</span>
            </div>
          ))}
        </div>
      )}

      {b.nextStep && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
               message="下一步" description={b.nextStep} />
      )}

      {/* 三个数字块撤了：「这次的结论」抽屉标题上有、「脚本要改」上面那行有、
          「缺的变量」也并进那行了 —— 同一个数在一屏里出现两次，人就得对一次。 */}
      <div style={{ fontSize: 12, color: C.gray, lineHeight: 1.9 }}>
        <b>结论是建议</b>，不是门禁 —— 清单和脚本都是 QA 自己维护的，平台只读。
      </div>
    </div>
  )
}

// 「这次到底看了多少」。**上限截掉的那部分必须写在这儿** ——
// 页面上方写着「场景 75 条」，而进模型的只有 60 条；不说的话读的人默认 75 条都评过了。
// 截断本身不是问题（额度有限，先给 P0/高风险），把截断说成全量才是。
function Scanned({ res, r }) {
  const c = res.coverage || {}
  const total = c.scenariosTotal || res.scenarioCount || r.scenarioCount
  const shown = c.scenariosShown
  const missedS = shown != null && total > shown ? total - shown : 0
  const missedF = (c.scriptsTotal || 0) - (c.scriptsRead || 0)
  const cut = (res.reviewedScripts || []).filter(x => x.truncated).length
  return (
    <div style={{
      fontSize: 12, color: C.gray, marginTop: 12, paddingTop: 10,
      borderTop: `1px solid ${C.line}`, lineHeight: 1.9,
    }}>
      这次读了 {(res.reviewedScripts || []).length} 份脚本
      {cut > 0 && `（其中 ${cut} 份正文太长被截断，截断的那几份不下结论）`}
      ，评了 {shown != null ? shown : total} 条场景。
      {(missedS > 0 || missedF > 0) && (
        <div style={{ color: C.orange }}>
          ⚠ 这个域共 {total} 条场景
          {missedS > 0 && `，其余 ${missedS} 条这次没进模型`}
          {missedF > 0 && `；还有 ${missedF} 份脚本没读进来`}
          —— 上面的结论只覆盖读到的这部分。
        </div>
      )}
    </div>
  )
}


// 评审结论的正文。三块的顺序 = 测试员下一步该干什么的顺序：
// 先看「声明了没验到」（覆盖率是虚的），再看环境跑不跑得起来，最后才是清单本身缺什么。
//
// 原来还有第四块「待补的先做哪条」（nextUp），2026-08-29 去掉了：分批读的时候
// 每批只看得到一部分脚本却要给全域排序，各批各排一份再拼起来 —— 实测同一个域
// 六批产出 18 行、去重后只有 3 件事，第 1/4/7/10/13/16 位全是同一条。
// 存量结论的 result 里还留着这个键，这里不渲染它（不会崩，就是不显示）。
function ReviewBody({ r, onOpenFile, projectId }) {
  const res = r.result || {}
  // 注意别把 res.nextUp 加回来充数：存量结论里它有值，加回来会让一份
  // 「这一轮什么都没说」的旧结论看起来像有内容。
  const empty = !res.scriptGaps?.length && !res.catalogGaps?.length
    && !res.envMissing?.length
  return (
    <div style={{ fontSize: 13 }}>
      <TakeAway r={r} projectId={projectId} />
      {res.summary && (
        <div style={{ marginBottom: 16, lineHeight: 1.9 }}><Rich text={res.summary} /></div>
      )}

      <Section title="抓到的问题（按谁动手排）"
               hint="脚本头写了 @scenario，但正文没验到那件事 —— QA 自己的门禁查不了这一层">
        {res.scriptGaps?.length ? [...res.scriptGaps]
          .sort((a, c) => BLAME_ORDER.indexOf(blameOf(a)) - BLAME_ORDER.indexOf(blameOf(c)))
          .map((g, i) => (
          <div key={i} style={{ padding: '8px 0', borderTop: i ? '1px dashed rgba(0,0,0,0.06)' : 'none' }}>
            <Space size={6} wrap style={{ marginBottom: 4 }}>
              {g.id && <Tag color="blue" style={{ margin: 0 }}>{g.id}</Tag>}
              {/* 动手的人也要先知道这条归谁：改脚本解决不了的那些，别让他白改一遍 */}
              <Tag style={{ margin: 0, color: BLAME[blameOf(g)].color,
                            borderColor: BLAME[blameOf(g)].color }}>
                {BLAME[blameOf(g)].title}
              </Tag>
              {g.severity && <Tag color={SEVERITY[g.severity] ? undefined : 'default'}
                                  style={{ margin: 0, color: SEVERITY[g.severity], borderColor: SEVERITY[g.severity] }}>
                {g.severity}</Tag>}
              {g.path && (
                <a onClick={() => onOpenFile(g.path)} style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>
                  {g.path}
                </a>
              )}
            </Space>
            <div style={{ lineHeight: 1.8 }}><Rich text={g.problem} /></div>
            {/* 判据锚点：从脚本正文原样抄的，拿去 grep 就能定位到要改的那一句。
                没有它，"这条断言不够"就只是一句评价，接手的人还得自己把整份脚本读一遍。 */}
            {g.evidence && (
              <pre style={{
                margin: '6px 0', padding: '6px 10px', background: 'rgba(0,0,0,0.03)',
                borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 12,
                color: '#476582', whiteSpace: 'pre-wrap', overflowX: 'auto',
              }}>{g.evidence}</pre>
            )}
            {/* 判据没搜到就必须在**这段引文旁边**说，不能只写在页面顶上那句汇总里：
                照着 evidence 动手的人是一条一条看的，他不会先回头读汇总。
                **只标记，不删也不降 severity** —— severity 说的是「对仓库有多糟」，
                回验说的是「我有多确信」，两个正交的轴合成一个就都读不出来了。
                存量结论（没有这个键）不逐条标：顶上那句已经说了整份都没回验，
                这里再标一遍就是给每一行都糊上噪音。 */}
            {g.evidenceCheck && !EV_PASS.includes(g.evidenceCheck) && (
              <div style={{ fontSize: 12, color: C.orange, lineHeight: 1.8 }}>
                ⚠ <b>这条的判据平台没验上</b>（{EV_CN[g.evidenceCheck] || g.evidenceCheck}）
                {g.evidenceFoundIn && <>，不过在 <code>{g.evidenceFoundIn}</code> 里搜到了</>}
                {' '}—— 结论本身可能仍然成立，但<b>先回原文确认再动手</b>。
              </div>
            )}
            {g.fix && <div style={{ color: C.gray, lineHeight: 1.8 }}>建议改成：<Rich text={g.fix} /></div>}
          </div>
        )) : <Nothing text="逐条读下来没抓到「声明了没验到」的" />}
      </Section>

      <Section title="我们这侧环境记录里没有的名字（不是脚本的问题）"
               hint={`脚本引用的、或 config 里声明「要从外面传」的，而我们这条 ${r.environmentName || '所选环境'} 记录里没有。代码算的，不是模型猜的 —— 但它只说明我们这侧没记着，推不出 QA 自己跑的时候也缺`}>
        {res.envMissing?.length ? (
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            {/* 分母。「缺 2 个」既可能是 2/3 也可能是 2/40 —— 没分母读的人判不了
                这一列有多严重，而判不了的结果通常是整列被当噪音略过。 */}
            {res.envSatisfied?.length > 0 && (
              <div style={{ fontSize: 12, color: C.hint }}>
                这个域要从外面拿 {res.envMissing.length + res.envSatisfied.length} 个变量，
                其中 {res.envSatisfied.length} 个这个环境里有。
              </div>
            )}
            {res.envMissing.map(v => (
              <div key={v.name}>
                {/* 两档分开画。混在一起是这一列最贵的毛病：一条响亮的假阳
                    （7 组角色账号都在，却报「缺 PASSWORD」）跟真缺口并排、
                    同样的警告色 —— 人扫两眼就把整列当噪音，真缺口跟着被无视。 */}
                <Tag color={v.state === 'ambiguous' ? 'default' : 'warning'}
                     style={{ fontFamily: 'var(--font-mono)' }}>{v.name}</Tag>
                <Tooltip title={(v.scripts || []).join('\n')}>
                  <span style={{ fontSize: 12, color: C.gray }}>
                    {(v.scripts || []).map(p => p.split('/').pop()).join('、')}
                  </span>
                </Tooltip>
                {v.state === 'ambiguous' && (
                  /* 降级要连**凭什么降**一起写出来，否则它就是一句无从复核的断言 */
                  <div style={{ fontSize: 12, color: C.hint, marginLeft: 2 }}>
                    名字对不上，<b>不是真缺</b>：环境里有{' '}
                    <span style={{ fontFamily: 'var(--font-mono)' }}>
                      {(v.family || []).join('、')}
                    </span>
                  </div>
                )}
              </div>
            ))}
            <div style={{ fontSize: 12, color: C.hint }}>
              公共库里真赋过值的、自带兜底值的、shell 自带的、夹具运行时拼出来的都已经排掉。
              写成 <code>{'export X="${X:-}"'}</code> 的算缺 ——
              那是仓库在明说这个值得从环境来，没配就整条静默跳过。
              <div style={{ marginTop: 4 }}>
                ⚠ 两件事别搞混：在平台这边补上变量<b>不会</b>让 QA 的脚本真跑起来（值要在真正跑套件的地方注入）；
                而平台这边没记着，也<b>不等于</b>那边缺。所以这一列不构成对 QA 的意见。
              </div>
            </div>
          </Space>
        ) : <Nothing text="脚本要的变量这个环境都有" />}
      </Section>

      <Section title="清单本身漏了什么" hint="这个域的场景之间明显缺的一环 —— 清单是别人维护的，这只是建议">
        {res.catalogGaps?.length ? res.catalogGaps.map((g, i) => (
          <div key={i} style={{ padding: '6px 0', lineHeight: 1.8 }}>
            <Space size={6}>
              <Rich text={g.scenario || g.problem} />
              {/* 域级结论每批都会各说一遍。修好去重键之后这里会**少掉一大截行** ——
                  不说清"这条 N 批都提到"，读的人会以为这一趟少发现了东西。 */}
              {g.mergedFrom > 1 && (
                <Tag style={{ margin: 0 }}>{g.mergedFrom} 批都提到</Tag>
              )}
            </Space>
            {g.why && <div style={{ color: C.gray }}><Rich text={g.why} /></div>}
          </div>
        )) : <Nothing text="没看出明显缺的一环" />}
        <DroppedNoAnchor res={res} />
      </Section>

      {empty && <Empty description="模型这一轮什么都没说 —— 重评一次试试" />}

      <Scanned res={res} r={r} />
    </div>
  )
}

// S8.1 · 清单侧结论指不出出处的，后端整条丢掉了。**这里必须说丢了几条。**
//
// 闸门本身不是问题，**静默的闸门才是** —— 一条没丢和丢了 8 条要是在页面上长得一样，
// 这道闸门就变成了它自己要防的那个东西（这个模块存在的意义就是抓这种形状）。
//
// 三档，一档都不能并：
//   · `undefined` —— 存量结论，那一趟压根没有这道闸门。**「没查」不是「零」**，
//     渲染成 0 就是替它宣布"这些都有出处"。同 `DimUnavailable` 那套。
//   · `[]` —— 查过了，一条没丢。这才是那个可以安静的档。
//   · 有东西 —— 摊开原话，划掉。列出来不是让人去改，是让「丢了几条」可见。
function DroppedNoAnchor({ res }) {
  const dn = res.droppedNoAnchor
  if (dn === undefined) {
    return (
      <div style={{ marginTop: 8, color: C.gray, fontSize: 12 }}>
        这一趟评的时候还没有「清单侧结论必须指得出出处」这道闸门，上面这些<b>没经过锚点检查</b>。
        不是它们都有出处，是这一版没查。
      </div>
    )
  }
  if (!dn.length) return null
  return (
    <div style={{ marginTop: 8, color: C.gray, fontSize: 12 }}>
      ⚠ 另有 <b>{dn.length} 条</b>指不出出处，已经丢掉，没算进上面。模型说清单缺这些，
      却一句原文都抄不出来 —— 指不出出处的结论<b>没人能十秒内否掉它</b>，
      不该混进要发给清单主人的整改建议里。列在这儿只为让「丢了几条」可见，不是让你去改：
      {dn.map((g, i) => (
        <div key={i} style={{ marginTop: 2 }}>
          <s>{g.scenario || g.why || g.problem || '—'}</s>
        </div>
      ))}
    </div>
  )
}

// 「QA 那边怎么拿到这份结论」—— 只能是他自己来拉，因为平台对 QA 仓永远只读。
//
// 所以这里给的是**文本**：复制走贴 issue、或存成 .md 交给他那边的 AI 改脚本。
// QA 那边跑 Claude Code 的话有第三条路：MCP 工具 lum_get_qa_review，直接拿同一份东西。
// 三条路都是"拉"，平台一个字节都不会往那个仓库写。
function TakeAway({ r, projectId }) {
  const [busy, setBusy] = useState(false)

  const fetchMd = async () => {
    const res = await api.get(
      `/projects/${projectId}/qa-catalog/reviews/${r.id}/export`, { params: { format: 'md' } })
    return res.data
  }

  const copy = async () => {
    setBusy(true)
    try {
      const d = await fetchMd()
      await navigator.clipboard.writeText(d.markdown)
      message.success('已复制 Markdown 全文，可直接贴到 issue 或交给 AI')
    } catch (e) {
      message.error(e.message || '复制失败')
    } finally { setBusy(false) }
  }

  const download = async () => {
    setBusy(true)
    try {
      const d = await fetchMd()
      const url = URL.createObjectURL(new Blob([d.markdown], { type: 'text/markdown' }))
      const a = document.createElement('a')
      a.href = url; a.download = d.filename; a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      message.error(e.message || '导出失败')
    } finally { setBusy(false) }
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
      padding: '8px 12px', marginBottom: 14, borderRadius: 6,
      background: 'rgba(14,165,160,0.06)', border: '1px solid rgba(14,165,160,0.2)',
    }}>
      <span style={{ fontSize: 12, color: C.ink }}>把这份结论交给 QA：</span>
      <Button size="small" icon={<CopyOutlined />} loading={busy} onClick={copy}>复制 Markdown</Button>
      <Button size="small" icon={<DownloadOutlined />} loading={busy} onClick={download}>存成 .md</Button>
      <Tooltip title={
        <div style={{ fontSize: 12, lineHeight: 1.9 }}>
          QA 那边跑 Claude Code 的话，让它直接调 MCP 工具
          <code> lum_get_qa_review</code>（带 project_id 和 domain）拿同一份东西，
          不用人来回传。
          <div style={{ marginTop: 6 }}>
            三条路都是<b>他来拉</b> —— 平台对 QA 仓永远只读，不会替他往仓库里放文件。
            他那边的 <code>check-coverage.sh</code> 拿清单当判据来源，
            我们多写一个文件，他就会红在一个查不到原因的地方。
          </div>
        </div>
      }>
        <span style={{ fontSize: 12, color: C.gray, cursor: 'help', borderBottom: `1px dashed ${C.faint}` }}>
          QA 用 MCP 直接拉？
        </span>
      </Tooltip>
    </div>
  )
}

function Section({ title, hint, children }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ fontWeight: 600, color: C.ink }}>{title}</div>
      <div style={{ fontSize: 12, color: C.gray, marginBottom: 6 }}>{hint}</div>
      {children}
    </div>
  )
}

const Nothing = ({ text }) => <div style={{ fontSize: 12, color: C.hint }}>{text}</div>
