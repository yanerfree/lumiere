import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  Card, Table, Tag, Space, Button, Input, Select, message, Tooltip,
  Progress, Modal, Form, Collapse, Popconfirm, Popover, Checkbox, Drawer, Empty, Spin, Tabs,
  ConfigProvider,
} from 'antd'
import {
  ReloadOutlined, SearchOutlined, BugOutlined, FileTextOutlined, SettingOutlined,
  InfoCircleOutlined, CheckCircleFilled, WarningFilled, CloseCircleOutlined,
  BorderOutlined,
  RobotOutlined, LoadingOutlined, CopyOutlined, DownloadOutlined,
} from '@ant-design/icons'
import { useParams } from 'react-router-dom'
import { api } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { PERM } from '../../utils/permissions'
import { usePermissions } from '../../utils/PermissionContext'

// 这一页的颜色只有一条规矩：**颜色只画色块，文字只用墨色**。
//
// 上一版栽的跟头不是色相不统一（那一版已经统一到四支色相了），是**把颜色刷到了笔画上**。
// 起因听着很对：让「P1」「待补」「风险错配」这些字自己带上语义色。但 12px 的字要过
// AA 4.5:1，在这一页的纸（最暗 #edecf6）上就把色相硬压到 L*≤45 —— 而 L*45 那一带：
//     琥珀 = #9d500e 深褐      蓝 = #1e699e 藏青      绿 = #17734c 墨绿
// 三支全是"掺了黑的颜色"。整页于是铺着几十处深褐深青，这就是**暗沉**的来源；
// 又因为文字 / 底纹 / 条子 / 图标四种形态各自带色，同屏有色元素上百个，这就是**杂色**。
//
// 结论很硬：**「文字上色」和「不暗沉」在 4.5:1 这条线上是互斥的**，只能选一个。
// 这一版选不暗沉：
//     笔画（文字）→ 中性墨色三档 ink / gray / faint，**一处彩字都没有**
//     色块        → 颜色全在这里：标签底纹 WASH、进度条 BAR、图标与大数字 VIVID
//
// 中间试过一版"两支能读的彩字"（红 #ca1e3a / 绿 #007b4e，都贴着 4.5:1 的天花板），
// 上线当天就被同一个人退了两次，两次的话正好把这条路的两头都堵死：
//   「更新时间几档全是黑色，不好看」  → 语义**必须**看得见颜色；
//   「暗色的全部换掉」                → 而 4.5:1 的彩字**必然**是暗的（L*≤45）。
// 两句话在彩字这个形态上无解，换个形态就都成立：**同一支色相，做成底纹比做成笔画又亮又艳**。
//   风险 ≥9 这个数：彩字 #ca1e3a 是 L*44 的深绛；药丸 #fcc4c1 是 L*84 的浅粉，
//   上面那个「9」还是 6.57:1 的墨字 —— 比彩字更好认，而且**没有一个像素是暗的**。
// 所以这一版的规矩比上一版更硬一条：**颜色只做底、点、条、图标，永远不做笔画。**
//
// 色块有色彩空间，是因为门槛根本不同：
//   · 图形和 ≥24px 大字只要 **3:1** —— 天花板在 L*56，那是**鲜亮**的红/琥珀/绿；
//   · 底纹只需要托住**墨字**（6.5:1，富余得很），不必再托 4.5:1 的彩字 ——
//     于是底纹可以同时更亮**又**更艳：C*12.8 → C*21.6，对纸 1.09:1 → 1.30:1。
//     上一版底纹淡到 1.09:1，"底 + 字"那个标签形状根本没长出来，屏幕上只剩一串
//     深色小字浮着 —— 那是暗沉的另一半原因：**不是底太深，是底没有、字太深**。
//
// 全页三支彩色相 + 一支冷灰。蓝**整支退役**：原来更新时间那三级藏青
//（live #0f4266 / today #175785，两列合计重复 500+ 次，是全页最大的一片蓝）撤掉，
// 改成「热的两档给绿、凉的两档退灰阶」—— 新鲜度在这一页跟覆盖率是同一种好，
// 所以它跟条子、跟 ✓ 用同一支绿，不再自己占一支色相。
//    25°  红   —— 挡住你的：P0、风险 ≥9、对不上、已知缺陷
//    60°  琥珀 —— 要处理但不挡：P1、有缺口、待补、风险错配
//   158°  绿   —— 好了：已覆盖、全认领、还热着、清单和脚本对得上
//   305°  淡紫 —— **不着急**：P2/P3 的条子和药丸、以及所有进度条的空轨
//   265°  冷灰 —— 不表态：身份与分类（ID、场景名、路径、执行层）、凉掉的那两档新鲜度
//
// 淡紫（305°）是这一轮新加的第四支，只为一件事：P2 原来是蓝灰 #a2acb9 ——
// 「蓝」和「灰底配灰字」恰好是这一页被点名最多的两样东西，而 P2 又确实需要一个
// **看得出是颜色、但不表态好坏**的位置。305° 是彩虹纸上粉紫那一段的同族色，
// 跟红（dE 57）、橙（82）、绿（80）全都拉得开，不会跟"好/坏"混。
// 空轨也一起从蓝灰挪到这支的极淡档：轨道铺满每一根条子的整个宽度，
// 24 个域 + 4 档优先级 —— 它才是全页面积最大的那片蓝。
//
// 同一个语义在三种形态里仍然是**同一支色相**，只是深浅随形态走：
//     P0     → 底纹 #fcc4c1 / 条子 #f9797a / 图标 #f73e52     全在 25°
//     P1     → 底纹 #fcc7a5 / 条子 #f99140 / 图标 #d36900     全在 60°
//     已覆盖 → 底纹 #aedcc2 / 条子 #3ecd8d / 图标 #229866     全在 158°
//     P2     → 底纹 #d8cced / 条子 #c4b4e3                    全在 305°（没有图标档，它不表态）
// 不用纯灰当中性：纯灰挨着琥珀会把琥珀衬得发脏，一点冷味（265°）是这页彩虹纸的补色方向。
//
// ⚠ 定色不许心算，也不许拿白底算。这一页的"纸"不是白的：.ant-card 只有
// rgba(255,255,255,.3)，底下压着 .app-layout-root 那条六段彩虹渐变（styles/global.css），
// 实测各区众数：卡1 #e8f2f8  卡2 #edecf6  卡3 #f2ecf3  域网格 #f7f2f7  表格 #f4ecf2。
// 下面每个比值都是对**最暗的那块 #edecf6** 实算、六块纸逐块验过取最低值。

// —— 文字档：全页**只有这四个值**允许落在笔画上。
// 前三档同在 265°、C*5.0，是一支冷灰的三个亮度，不是三支灰。
const C = {
  ink: '#3d434a',   // L*28  8.54:1  正文、标签文字、强调数字 —— 唯一的"深"色
                    //   上一版是 #21252a（L*14.5，近黑，13.16:1）。提亮 13.5 个 L* 之后
                    //   仍有 8.5:1，读着一点不吃力；而"近黑"本身就是一层暗沉的底噪。
  gray: '#636970',  // L*44  4.74:1  次要文字、说明小字、可点路径
                    //   上一版这里是两支：gray #5e6268(L*41.4) 和 hint #666a70(L*45)。
                    //   两支都被 4.5:1 顶在同一个位置上，差 3.6 个 L* —— 那不是两档，
                    //   是同一档写了两遍。合并掉，全页少一支灰。
  faint: '#81878f', // L*56  3.09:1  **只许装饰**：分隔点、占位破折号、0 值、虚线边框、图标。
                    //   按非文字 3:1 定线 —— 够画图标，不够读一句话，这就是它的用途边界。
  line: '#e0e6ef',  // L*91  分隔线、发丝边、白纱药丸的环
                    //   **不管空条** —— 空轨是 BAR_TRAIL（见下），两支差 3 个 L*。
                    //   差这么点谁都看不出，可"看不出"恰恰是它坑人的地方：
                    //   图例色块原来取这一支、格子里的空轨取那一支，于是「清单没行」
                    //   这条图例和它要解释的那根条子**不是一个颜色**，而且没人会发现。
                    //   H266 那点冷味是**故意留的**（理由见上）：同 L* 挪到 305° 去凑
                    //   BAR_TRAIL 只动 dE 3.5，肉眼无差，却把这支四值中性族拆了。
  // ⚠ **这里不再有第五、第六个值，别往回加。**
  //   删掉的是 crit #ca1e3a（L*44）和 good #007b4e（L*45）——「能读的彩字」那一版。
  //   它们不是算错了，4.77:1 / 4.55:1 都是实测达标的；错的是这个**形态**：
  //   4.5:1 把红压成深绛、把绿压成墨绿，而「不要暗色」是这一页第一条审美要求。
  //   要给一个语义上色，就给它 WASH 的底、VIVID 的图标、BAR 的条子 —— 三个档都比
  //   彩字亮，也都比彩字显眼。**任何"给这个字上个色"的需求，答案都是给它一个药丸。**
}

// —— 图形档：图标、✓/⚠ 这类符号、以及 ≥24px 的英雄数字。**不渲染正文字**。
//
// ⚠ **这一档 2026-09-01 整体从 L*56 抬到 L*68，也就是主动放弃了 WCAG 的 3:1。**
//   别当成漏检查改回去 —— 那是一个权衡，不是一个疏忽，来龙去脉如下。
//
// 原来的理由是硬的：非文字图形要 3:1，而这一页的纸近白，3:1 的天花板正好在 L*56
// （L*57 就掉到 2.99:1，实算过）。于是三支只能是 #f73e52 / #d36900 / #229866。
// 问题也出在这里 —— L*56 的 60° 本来就叫"褐"，而 L* 被 3:1 顶死之后**只剩彩度
// 一个把手**，拧到 100% 也还是深橙。同一天里"这个颜色太暗了"被说了四次，
// 四次点的位置各不相同（P1 那根条、165 那个数字、✓ 已覆盖那颗药丸、整体印象），
// 但量下来**全部落在这一档**：这不是四个毛病，是一个。
//
// 所以这次动的是判据本身，而不是继续在 L*56 里挑色：
//   3:1 那条线管的是"颜色是**唯一**的信息出口"。这一页不是那种情形 ——
//   每个有色图标旁边都钉着同义文字（「已覆盖」「风险 ≥6 却排在 P2/P3」
//   「评完之后这个域又动过」），每个大数字下面都写着它是什么数。
//   颜色在这里做的是**强调**，不是编码。
// ⚠ 反过来说：**哪天把某个图标旁边的文字拿掉了，这一档必须跟着回深。**
//   判断方法很简单 —— 遮住颜色，这条信息还读得出来吗？读不出来就不许用这一档。
//
// 新值取 L*68 各自贴自己色域的顶（99% / 99% / 100%）。为什么是"贴顶"而不是像
// 底纹档那样按色相分配百分比：这一档的笔画细（11~15px 的图标），细笔画本来就吃亏，
// 三支同时开到最艳是唯一不让某一支发闷的做法 —— 尤其是橙，它欠一点饱和就是土黄
// （这条规律在下面 BAR 那段有三次实测记录）。
// 为什么是 68 而不是更亮：再往上走绿会先掉出可辨范围（L*72 时对纸只有 1.8:1，
// 那颗 ✓ 就成了一团淡雾）。68 这一档三支对最深那张纸都是 2.09:1，整齐。
const VIVID = {
  red: '#ff7f7f',  // L*68 C*53.7 H 25°   99% 上限   对纸 2.09:1（旧值 dE 26.1）
  warn: '#ff8415', // L*68 C*82.2 H 60°   99% 上限   对纸 2.09:1（旧值 dE 14.3）
  ok: '#00bd7b',   // L*68 C*60.0 H158°  100% 上限   对纸 2.09:1（旧值 dE 17.6）
}
// ⚠ 抬亮的一个**已知副作用**，写在这儿免得被当成手误：
//   `VIVID.red` 和下面的 `BAR.danger`（#f9797a）现在只差 dE 2.1，肉眼是同一个颜色。
//   不是漏改 —— 红在 L*66~68 之间的色域就那么点空间，而这两档原来分得开，
//   靠的正是 VIVID 被 3:1 压在 L*56。那条线一去，红这一支的两档就合并了。
//   橙（dE 8.3）和绿（dE 6.9）还分得开。真要把红拆回两档，只有把 BAR.danger
//   往亮处挪，而那会动到进度条的轻重次序（红 66 < 橙 70 < 绿 74 是故意的），
//   代价比"红的图标和红的条子同色"大得多。**同色在这里不产生歧义**：
//   一个是图标、一个是长条，形状本来就不一样。

// —— 条子档：进度条 + 药丸/圆点以外的所有色块。**不许拿去渲染字**（对纸只有 1.6~2.2:1）。
//
// 彩度这一档来回调过三次，值本身不重要，**判据**才重要：
//   C*51 等绝对彩度 → 红占到自己上限的 99%，是一根发光的荧光粉（第一次被退）。
//   C*44 等绝对彩度 → 红 81%、绿 69%，而**琥珀只有 63%** —— 于是那一根是土黄
//                     （#e79d69，第二次被退：「这3个颜色不好看」）。
//   现在改成**等相对彩度 0.88~0.92**：同一个百分比，三支同时"开到自己最艳的九成"。
//   第三次（2026-09-01）退的不是这一档，是另两档的橙 —— 图形档 92% 和底纹档 70%
//                     被同时点名土黄。
//   三次的共同点不是"某个方向调错了"，而是**一刀切的彩度规则总有一支吃亏**：
//   等绝对彩度先把红烧成荧光（第一次），再把橙闷成土（第二次）；等相对彩度治好了红，
//   第三次说明它对橙还是不够 —— **橙的下限比别人高**。
//   落到手上就一句：**橙一旦欠饱和就是土黄，而红绿欠饱和只是变淡（藕粉、灰绿，都不脏）。**
//   所以定色时橙那一支不跟别人的百分比走，单独贴着自己上限量；红绿反而要留余量。
// 为什么等绝对彩度在这里是错的：色块虽然是大片的，但**黄橙一带要靠高彩度才不发脏** ——
// 同样 C*44，红看着还是红，琥珀已经掺进灰里去了（这就是"土"的来源）。
// 等相对彩度让每支色各自离自己的边界一样远，代价是绝对彩度不再相等（红 54 / 橙 67 / 绿 57）。
// 而这个代价看不见 —— 三支并排时眼睛比的是"够不够艳"，不是"艳得一不一样"。
// 轻重仍然整个交给 L*：红 66 < 琥珀 70 < 绿 74 < 淡紫 76，越暗越沉、越先跳出来。
//
// 代价照旧写清楚：条对轨道只有 1.2~1.9:1，够不到图形 3:1 的线。敢这么做，是因为
// **每根条子右边都钉着精确数字**（`182/252`、`13/28`）—— 条长和颜色都不是唯一出口。
// 哪天把数字挪走了，这套颜色必须跟着回深。
const BAR = {
  danger: '#f9797a', // L*66 C*54.0（92% 上限）H 25°  ← 和 VIVID.red 同色相（现已同色，见上）
  warn: '#ff8d31',   // L*70 C*74.1（99% 上限）H 60°  ← 和 VIVID.warn 同色相
                     //   2026-09-01 从 90% 提到贴顶（#f99140 → dE 7.4）：P1 那根条子
                     //   被单独点名"偏暗"。**橙在三档里都得贴着自己的上限** ——
                     //   这是下面那三次实测唯一收敛出来的一条规矩。
  ok: '#3ecd8d',     // L*74 C*56.6（88%）    H158°  ← 和 VIVID.ok 同色相
  mute: '#c4b4e3',   // L*76 C*26.0（58%）    H305°  ← P2：淡紫，不表态但**是个颜色**
  mute2: '#d3c6eb',  // L*82 C*20.2（60%）    H305°  ← P3：同一支再淡一档
}

// 空轨归到 305° 那支淡紫的**极淡档**（C*2.7，基本是中性，只留一点紫味）。
// 原来是 265° 的蓝灰 #d8dde4 —— 它是全页面积最大的一片蓝：24 个域 + 4 档优先级，
// 每一根都从头铺到尾，条子只占其中一段，剩下的全是轨。挪到跟纸同族的淡紫之后，
// 整页少掉的蓝比删掉"新鲜度三级藏青"那次还多。
// 深浅仍按"看得见但不抢条"定，对最亮的那块纸 1.16:1（旧值 1.17，几乎没动）——
// 再淡下去 0/9 那种行看上去就成了"这里什么都没有"，而不是"这一格一条都没认领"。
const BAR_TRAIL = '#dedce1'

// —— 底纹档：标签的底。彩度**按色相各自定，不搞一刀切**：
//   红 C*21.6（92% 上限）—— L*84 处红的色域最窄，上限只有 C*23.5，等于已经贴满
//   橙 C*28.1（91%）—— **必须贴着自己的上限**。原来它跟红取同一个绝对值（都是 C*21.6），
//                       只占上限 70%，那就是被点名两次的土黄 #f3c9af
//   绿 C*21.6（30%）—— 绿的上限是 C*71.7，宽得离谱；欠饱和的绿是薄荷不是土，
//                       所以这一支反而要**压住**，跟红取同绝对彩度才是视觉等重
//   紫 C*18.0（60%）—— 它管「不着急」，故意比三支表态的轻一档；
//                       跟条子那两支淡紫（58% / 60%）对上，两处紫是同一族
// 上一版这里写的是"前三支取等绝对彩度 C*21.6"，**那条规则本身就是把橙做坏的原因**：
// 同一个绝对彩度落在窄色相上是贴满、落在宽色相上是欠饱和，而橙欠饱和就变土。
// 为什么是 L*84 而不是更浅：近白处 sRGB 的色域窄得厉害，实测四支色相的**共同**彩度上限
//     L*88 → C*17.1     L*86 → C*20.2     L*84 → C*23.5     （每一档都卡在红上）
// 想更艳就得往下走。而"往下走"这次不再有代价：底纹上放的是**墨字**（6.56~6.58:1），
// 不是上一版那种 4.5:1 贴着线的彩字 —— 门槛一让开，L*84 / C*21.6 就取得到了，
// 比上一版（L*90.5 / C*12.8）艳了 69%，对纸也从 1.09:1 提到 1.30:1，
// 标签终于是"一块有颜色的底"，而不是"一串深色小字"。
const WASH = {
  danger: '#fcc4c1', // 墨字其上 6.57:1   对纸 1.30:1
  warn: '#fcc7a5',   //          6.60:1        1.29:1
  ok: '#aedcc2',     //          6.58:1        1.30:1
  low: '#d8cced',    //          6.56:1        1.30:1   ← P2 的药丸底（L*84 C*18 H305）
  // 原来这里的第四支是蓝（H250），管「不表态 / 正在进行 / 选中」——
  // 一件都不是好坏，删掉之后分别由白纱片、白纱片、墨色接走。
  // 现在的第四支是淡紫，管的是**另一件事**：「不着急」（P2/P3）。
  // 它跟 mute（什么都没有）的分工是：P2 有一个档位要认，只是不催你 ——
  // 所以它有底、有色，但那支色不在"好/坏"这条轴上。
}
// 四支互相的 dE：最近的一对是 红↔琥珀 15.9，其余 26~40。
// 这一对恰恰是并排出现最多的（P0 标签紧挨 P1 标签），所以单独记一笔 ——
// 琥珀贴满彩度之后它从 12.6 拉到 15.9，顺带把这两枚药丸也分得更开了。

// ✅/⬜/❌ 这三个 emoji 是**清单文件自己的记号**，引用原文时该留（"标了 ✅ 却没有脚本"）；
// 但拿它们当页面自己的状态图标就不行 —— emoji 的颜色是字体给的，
// ✅ 那支绿和这一页的绿差着十万八千里，于是"已覆盖"这一个意思，
// 一个格子里同时用两支绿画了两遍。改成矢量图标，颜色走 VIVID。
//
// 文字一律 ink：图标已经把"是好是坏"说了，字再上一遍色就是同一件事说两遍 ——
// 而那第二遍的代价是把字压到 L*45 变褐变青，正是这次要消掉的东西。
// 容器底纹一律**白纱**。上一版全是黑纱（rgba(0,0,0,.015~.04)）：黑纱做层次是靠
// 把纸压暗，一页叠七八块就是整体发灰。白纱做同样的层次是靠提亮，卡片本来也是
// rgba(255,255,255,.3) 浮在彩色渐变上，白纱只是把这件事继续做下去。
const VEIL = 'rgba(255,255,255,.55)'

// **标签上写 borderColor 一个像素都画不出来** —— `global.css` 有一条全站的
// `.ant-tag { border: none !important }`。这一版最早给严重度设的鲜色描边、
// 给 info 片设的发丝边，就是这么静静地没生效的：不报错、不告警，
// 只是三档严重度（blocker/major/minor）在页面上长得一模一样，
// 而白纱片落在近白的纸上等于隐形 —— 一个标签看着像没有标签。
// 换 inset box-shadow：视觉上就是一圈 1px 的边，而 `border: none` 管不到它。
// （原生 <button> 那几个筹码不受影响，它们自己写了 borderStyle/borderWidth。）
const RING = c => `inset 0 0 0 1px ${c}`

// ⚠ **有底色的那两档故意不带图标**（2026-09-01）。这一颗药丸是被单独截图点名
//   「这个颜色太暗了」的那一个，而量它的像素得到的答案很具体：整片 65.6% 是
//   #aedcc2 那层浅绿底，真正暗的只有两小块 —— 那枚 L*56 的 ✓（4.1% 面积）
//   和墨字（5.2%）。图形档抬亮到 L*68 之后 ✓ 压在自己那块底上只剩 1.61:1，
//   留着就是一团看不清的绿雾：**要么是暗点，要么是脏点，没有第三种结果。**
//   所以直接去掉 —— 「已覆盖」三个字本来就在图标右边，颜色少一个出口不丢信息。
//   （「已废弃」那一档反而必须留图标：它没有底色，图标是它唯一的形。）
const STATE_TAG = {
  covered: { text: '已覆盖', color: C.ink, bg: WASH.ok },
  gap: { text: '待补', color: C.ink, bg: WASH.warn },
  // 「已废弃」不给底：它是"这条不算了"，不是一种表态。见下面 TAG_TONE.mute。
  deprecated: { text: '已废弃', color: C.gray, bg: 'transparent', icon: C.faint, Icon: CloseCircleOutlined },
}

// antd 的 <Tag color="error|warning|blue"> 拿的是**全站** token（见 main.jsx：
// colorError #e8453c、colorWarning #f0a020、colorInfo #4e8af0），不是这一页的，
// 而且实测在这一页的纸上是不合规的（带缺陷 3.61:1 / P0 按钮 3.71:1 / 脚本链接 2.87:1，
// 12px 正文要 4.5）—— 全站 token 是照白底挑的，落到这一页的彩虹纸上就掉下来了。
// 所以这一页不许出现 color="error" 那种预设，一律走这张表。
const TAG_TONE = {
  bad: { color: C.ink, background: WASH.danger },   // 6.57:1
  warn: { color: C.ink, background: WASH.warn },    // 6.56:1
  ok: { color: C.ink, background: WASH.ok },        // 6.58:1
  // info 档不再是蓝底。它身上挂的三件事 —— 「清单口径要商量」（类别）、
  // 「评审中」（进行中）、快捷筛选的回显（选中）—— 一件都不是**好坏**，
  // 占掉一支色相纯属浪费，而且蓝是本页跟三支语义色最不搭的一支。
  // 改成白片 + 发丝边：跟 bad/warn/ok 的「有色底」区分得开，跟 mute 的
  // 「什么都没有」也区分得开，三档各是一种形，不靠辨色。
  info: { color: C.ink, background: VEIL, boxShadow: RING(C.line) },
  // 「不表态」这一档**取消底色**。上一版是 rgba(33,37,42,.075) 的灰底 + C.gray 的灰字：
  // 灰底配灰字本身就发脏，而它是全页出现次数最多的标签 —— 执行层那一列 537 行每行一个，
  // 于是表格正中竖着一条灰块带。底一去，那一列变成干净的灰字；
  // 而且「有底 = 这条有话说 / 没底 = 中性」本身就成了一条能看懂的规矩，
  // 不用再靠辨认灰的深浅去猜。
  mute: { color: C.gray, background: 'transparent' },
  // 「不着急」：有底、有色，但不表态好坏。只给 P2 —— 见 WASH.low 的注。
  low: { color: C.ink, background: WASH.low },      // 6.56:1
}
// 不描边：这一页的标签本来就是「底纹 + 文字」那个样子（见上面 STATE_TAG），
// antd 默认那圈边会让同一排里两种标签长得不一样。
// 这一页四条 info 说的都是「平台对这个仓库永远只读」这类**中性说明** ——
// 不表态好坏，不该占一支色相（原来占的是蓝，这一版蓝整支删了）。
// 所以 info 走白纱 + 发丝边，只有真的报警才上色。
// 这里的图标**留着**，跟上面 STATE_TAG 去图标不矛盾：说明条是整条宽底，图标 15px，
// 1.61:1 在这个尺寸上还能看出是个三角还是个圆；12px 的小药丸上就看不出了。
// 何况说明条只出现四五处，不像状态列那样 537 行每行一个。
const ALERT_TONE = {
  info: { bg: VEIL, Icon: InfoCircleOutlined, icon: C.gray },
  warning: { bg: WASH.warn, Icon: WarningFilled, icon: VIVID.warn },
  error: { bg: WASH.danger, Icon: WarningFilled, icon: VIVID.red },
  success: { bg: WASH.ok, Icon: CheckCircleFilled, icon: VIVID.ok },
}
// 图标自己给，不然 antd 会按 colorInfo/colorWarning 那套全站 token 上色 ——
// 那几支是照白底挑的，落到这一页的彩虹纸上都不合规（实测 2.87~3.71:1）。
// 文案也要自己包一层墨色。antd 把 .ant-alert-message / -description 的颜色写在
// class 上，用的是全站 colorText #1d2129（L*13，近黑）—— 那支比本页的 C.ink 还深两档，
// 挂在浅底纹上就是一块「暗沉」。挂在 class 上的颜色赢不过子元素的行内 style。
// 这一页自己的默认文字色。全站 colorText 是 #1d2129（L*13 近黑，对纸 15:1）——
// 探针数过：本页 1385 个可见文本节点里有 218 个**没有**显式给色，全在继承那支近黑，
// 12~13px 的近黑铺满一页就是「整体偏暗沉」。
// 挨个去补那 218 个 style 是错的做法：下次谁新写一行 <span> 又回到近黑。
// 所以换页面根上的**默认值** —— 显式写了色的地方一个都不受影响（行内 style 更强）。
const PAGE_THEME = {
  token: {
    colorText: C.ink,            // 近黑 L*13 → 墨色 L*28，仍有 8.54:1
    colorTextHeading: C.ink,
    colorTextDescription: C.gray,
    // 占位符和禁用文字原来是**带 alpha 的黑**（rgba(0,0,0,.25) 一类）。
    // 半透明黑压在这一页的彩色纸上不是浅黑，是**脏灰** —— 纸有什么色它就脏什么色，
    // 六张纸六个结果，正是要消掉的杂色。换成实色的浅墨，六张纸上长一个样。
    colorTextPlaceholder: C.faint,
    colorTextDisabled: C.faint,
    colorIcon: C.gray,
    colorIconHover: C.ink,
    colorSplit: C.line,
    // 语义四色也换成本页那三支（图形档 3:1）。antd 那四支是照白底挑的，
    // 落到这页彩虹纸上 2.87~3.71:1，而 colorInfo 的蓝本页已经整支删掉了 ——
    // 「信息」不表态好坏，退回中性。
    colorSuccess: VIVID.ok,
    colorWarning: VIVID.warn,
    colorError: VIVID.red,
    colorInfo: C.gray,
    // 下拉箭头这类「三四级」图标 antd 也给的是 rgba(0,0,0,.25)（实测 .ant-select-suffix），
    // 同样是六张纸六个脏灰，一并换成实色。
    colorTextTertiary: C.gray,
    colorTextQuaternary: C.faint,
  },
  components: {
    // 排序箭头（表头那两个小三角，激活时上色）是全页最后一处品牌青 #0ea5a0，
    // H192 —— 整页唯一一支青，也是"蓝"那一族的最后一个据点。
    // 这一页选中态的语法本来就是**墨底白字**（见 SELECTED_FILL），所以箭头也退到墨色：
    // 激活 = 变深，不换色相。
    // **只给 Table，不设全局 colorPrimary**：全局设了会把配置弹窗那颗「保存」
    // 一起变成深墨块（`.ant-btn-primary` 的填充态在 global.css 里没上 !important，
    // 是这一页唯一真会被带走的地方）—— 而"别用暗色"是这一页第一条审美要求。
    Table: { colorPrimary: C.ink },
    // **悬浮提示原来是近黑的**：实测底 rgba(0,0,0,.85)、字纯白。
    // 这一页有 17 个 Tooltip，域表里那几个还是 420px 宽的多行块 ——
    // 一页里最大最黑的面就是它，鼠标一扫就糊一片，正是「偏暗沉」最直接的来源。
    // 改成白底墨字（跟 Popover 的白一致），靠 antd 自带的投影跟纸分开。
    // 只落在这一页：外面别的页还指着深色提示，不动它们。
    Tooltip: { colorBgSpotlight: '#fff', colorTextLightSolid: C.ink },
  },
}

// **不用 antd 的 `<Alert>`** —— `global.css` 里 `.ant-alert-info` 一类把 `background`
// 和 `border-color` 双双写成了 `!important`，行内样式压不过去（作者 !important > 行内）。
// 实测：给它 `background: rgb(1,2,3)`，量出来是 `rgba(78,138,240,.1)`。
// 也就是说这一页所有说明条一直在用全站那支**蓝**，跟本页配色没有半点关系 ——
// 而蓝这一版是整支删掉的，「说明」不表态好坏，退回中性白纱。
// 自己拼一个 div 就没有这个问题（页首那几个筹码用原生 <button> 也是同一个原因）。
function PageAlert({ type = 'info', style, message, description, action, showIcon = true }) {
  const t = ALERT_TONE[type] || ALERT_TONE.info
  return (
    <div
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 10,
        padding: description ? '12px 14px' : '9px 14px',
        borderRadius: 12, background: t.bg,
        // 白纱那档几乎和纸同色，得靠一圈发丝边才看得出是一条说明；
        // 有色底的三档自己就有形，再描一圈只是多一道杂色。
        border: t.bg === VEIL ? `1px solid ${C.line}` : '1px solid transparent',
        ...style,
      }}
    >
      {showIcon && <t.Icon style={{ color: t.icon, fontSize: 15, marginTop: 2, flexShrink: 0 }} />}
      <div style={{ flex: 1, minWidth: 0, color: C.ink, fontSize: 13, lineHeight: 1.7 }}>
        <div style={{ fontWeight: description ? 600 : 400 }}>{message}</div>
        {description && (
          <div style={{ marginTop: 4, fontSize: 12, color: C.ink, lineHeight: 1.8 }}>{description}</div>
        )}
      </div>
      {action && <div style={{ flexShrink: 0 }}>{action}</div>}
    </div>
  )
}

const tagStyle = tone => ({ margin: 0, ...TAG_TONE[tone] })

// 优先级是**序数**，不是好坏 —— 但四档**每一档都有自己的颜色**，这是被要求了两次的：
// 「P1、P2 的颜色怎没了，该有的还是要有」。
// 上一版让 P2/P3 走 mute（不给底、干净的灰字），理由是"页面要先跳出来的是哪些挡门禁"——
// 那个理由本身没错，但它解决问题的手段是**把一个档位画成没有档位**：
// 扫这一列的人分不出"P2"和"这一格没填"，两个都是灰字。
// 现在四档全有底，轻重交给**色相离红有多远** + 底有多淡：
//   P0 红 → P1 琥珀 → P2 淡紫 → P3 白纱（只有一圈发丝边）。
const PRIORITY_TONE = { P0: 'bad', P1: 'warn', P2: 'low', P3: 'info' }
// 条子那档跟着同一张表分：P2/P3 是**同一支淡紫的两级**（L*76 / L*82），
// 所以在一根条子上，深浅本身就是优先级顺序，不用再辨色。
const PRIORITY_BAR = { P0: BAR.danger, P1: BAR.warn, P2: BAR.mute, P3: BAR.mute2 }
// 筛选筹码的**选中**态：实底 + 白字。底色统一用 ink（白字 8.5:1），不按优先级分色 ——
// 白字要过 4.5:1，实底就必须 L*≤45，而那个亮度上的琥珀正是要消掉的深褐。
// 一次只可能选中一个，筹码上又写着「P0 缺 70」，深浅分色在这里挣不到任何信息。
const SELECTED_FILL = C.ink
// 优先级出现在三个地方（表格一列、覆盖率卡四行、筛选筹码），三个地方**同一个形**：
// 药丸底 + 墨字。底色就是 PRIORITY_TONE 那四档，所以「P0」在哪儿都是同一颗浅粉。
//
// 为什么不是彩字（上一版就是彩字，只有 P0 上红）：见 C 那一段。一句话 ——
// 彩字要过 4.5:1 就得是暗的，而药丸的底可以放到 L*84，上面还是 6.5:1 的墨字。
//
// ⚠ 这里**故意不做"稀有度调音量"**，尽管数据支持那么做（P0 占 47%、P1 占 44%，
//   "满屏都在响等于没有响"）。理由是这一列**不是报警，是分类**：
//   报警可以只喊最急的那一档，分类不能只标一半 —— 少标的那一半会被读成"没填"。
//   真正在做"调音量"的是它右边的**风险**列（≥9 才给药丸，30% 的行），
//   两列紧挨着，一列全带壳一列只有 30% 带壳，正好把"分类"和"报警"分成两种东西。
// 底色就用 PRIORITY_TONE 那一张表，别在这儿再抄一份 —— 三个地方同一颗药丸，
// 靠的就是"只有一张表"。
// 药丸的形：定宽 + 居中，四档等宽 —— 否则一列里 P0/P1/P2 三种宽度会抖。
const pill = (tone, w) => ({
  ...tagStyle(tone),
  display: 'inline-block', minWidth: w, textAlign: 'center',
  padding: '1px 7px', borderRadius: 9, fontSize: 12, lineHeight: '16px', fontWeight: 500,
})


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

// 三档，但**只有最烫的那档上色**：这份清单 78% 的行风险 ≥6、30% ≥9 ——
// 给 6~8 也上色就是五行里响四行，那不是强调，是底噪。
// 所以 ≥9 判红（跟 P0 同一支红：都是「挡着门禁的」），6~8 留墨，<6 退灰。
// 只剩「墨 / 灰」两档给裸数字用；≥9 那档走药丸（见风险那一列的 render）。
const riskColor = r => (r >= HIGH_RISK ? C.ink : C.gray)

// 清单里一半的场景描述带 `反引号` 和 **加粗**，原样打出来是满屏符号
//
// 还得摘掉 **U+FE0F（emoji 变体选择符）**。清单原文里有 177 处 `⚠️` ——
// 带上 FE0F 就强制走 emoji 呈现，字体给的是一个**自带颜色**的金黄三角（实测 H85 C*79），
// CSS 的 `color` 一个字都管不着。于是同一句"这里要处理"，在别处是琥珀 60°，
// 在这儿是一支谁也调不动的金 —— 正好是"同一个东西两种颜色"最刺眼的那一种。
// U+26A0 本身 Emoji_Presentation=No，**去掉 FE0F 就回落成普通文字字形**，
// 颜色随宿主走，一行代码解决，且原文一个字没动（QA 仓只读，也不该动）。
// 摘完再单独把它挑出来上琥珀：说明行整体是 C.gray 的次要文字，
// 只有这个记号是"要处理"的信号，跟表头那行「拉取于…（14 小时前）」一个套路 ——
// 句子归句子，信号归信号。
const VS16 = /\uFE0F/g
// code 段必须**有边界**：不跨句号、不跨标记、不超过 80 字。
// 原来的 `[^`]+` 只要求"下一个反引号"，而清单里 2669 条长文本有 86 条（3.2%）
// 反引号是**奇数**个 —— 一个落单的反引号会一路吃到几百字之外的那一个，
// 把整段正文裹进 code 底纹里：屏幕上是一块**段落大小的灰片**，
// 而那块灰的意思本来是「这几个字是原文里的字面量」。
// 同一块颜色，一会儿标三个词、一会儿标三行话，就等于没标。
// 它还顺手把裹进去的 **…** 一起吃掉，于是正文里漏出裸露的星号 ——
// 实测 `**` 本身 100% 成对（0/2669 落单），页面上所有裸星号都是这么来的。
//
// 加边界之后：86 条奇数串里**再没有一处**吞到正文（原来处处是），
// 同时还认回了它们里面 439 个本来就合法的短片段；
// 代价是成对串里 30/1143（2.6%）的超长/跨句片段不再上底纹，退成带反引号的纯文本。
// 这个方向是**故意选的**：少一块底纹，比多一块指错东西的底纹好。
const RICH_RE = /(`[^`\n。*<>]{1,80}`|\*\*[^*]+\*\*)/g
// ⚠ 得单独走一层，不能塞进上面那条交替里。正则交替是**最左优先**的：
// 扫到 `**` 时先试加粗那一支，`[^*]+` 一口气把 `⚠` 连同整段吞掉，
// 引擎根本走不到 ⚠ 那一支 —— 实测把 `|⚠` 加进去和原正则**逐段 0 差异**，
// 而清单里 391 条带标记的文本有 **101 条**的 ⚠ 正长在 `**…**` 里面。
// 只挑裸露的那些上色，等于同一个"要处理"记号在页面上有两种颜色，
// 正是这次要消掉的东西。所以分两层：先切标记，再在每块**纯文字**里挑 ⚠。
// 反引号里的不挑 —— 那是原文的字面量，代码块里出现的 ⚠ 是内容不是信号。
function warnify(s, k) {
  if (!s.includes('⚠')) return s
  return s.split('⚠').flatMap((seg, i) => (
    i === 0 ? [seg] : [<span key={`${k}w${i}`} style={{ color: C.ink, fontWeight: 600 }}>⚠</span>, seg]
  ))
}
function Rich({ text }) {
  if (!text) return null
  return String(text).replace(VS16, '').split(RICH_RE).filter(Boolean).map((p, i) => {
    if (p.length > 2 && p.startsWith('`') && p.endsWith('`')) {
      return (
        <code key={i} style={{
          fontFamily: 'var(--font-mono)', fontSize: 12, padding: '0 4px',
          // 不定色，跟着所在那一行走。上一版这里硬编码 #476582，是一支没进上面
          // token 表的第五种蓝灰；而且它嵌在正文里时比正文浅、嵌在次要说明里时比说明深，
          // 层级一会儿正一会儿反。灰底片 + 等宽字已经把"这是原文里的字面量"说清楚了，
          // 颜色不必再说一遍 —— 继承还顺带保证了它在哪儿都跟宿主一样合规。
          background: VEIL, borderRadius: 3, color: 'inherit',
        }}>{p.slice(1, -1)}</code>
      )
    }
    if (p.length > 4 && p.startsWith('**') && p.endsWith('**')) {
      return <strong key={i}>{warnify(p.slice(2, -2), i)}</strong>
    }
    return <span key={i}>{warnify(p, i)}</span>
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

// 这里曾经按 tone 给整张卡描个红/琥珀的外沿 —— 但 `global.css` 把 `.ant-card` 的
// `border` 和 `box-shadow` 双双锁成了 `!important`，行内样式**画不进去**
// （作者样式的 !important 压得住行内），所以那圈色从来没出现在页面上。
// 与其留个假的开关，不如认下来：这张卡的红/琥珀本来就写在标题右边那行
// 「有会被门禁挡住的 / 有要处理的」上 —— 有字有鲜色图标，比一圈 1px 的边看得清。
function Panel({ title, extra, children }) {
  return (
    <Card
      size="small" style={{ flex: 1, minWidth: 300 }}
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
// 四档仍然一档一个样子，但**四档全在 264° 这一支上**（深蓝 → 蓝 → 灰 → 浅灰）。
// 上一版 live 用的是 teal —— 而绿在这一页已经占着「覆盖好了」，同一支绿于是既说
// "这条过了"又说"这个域刚有人动"。新鲜度根本不是好坏，不该借状态色；它是一条**标尺**，
// 标尺就该是同一支颜色的深浅，不是四支各不相干的颜色。
//
// 而且深浅得**单调**：越新越深。上一版 today #1868c7 是 5.46:1、week #5f6b7a 是 5.43:1，
// 排下来 today 比 week 还浅一点点 —— 尺子刻反了。现在对真纸实测
// live #0f4266 9.05:1 → today #175885 6.50:1 → week #556475 5.20:1 → cold #666b71 4.61:1，
// 一路变浅，读法和"越新越重"对得上。live↔today 亮度差从原来的"看着一样"拉到 1.39:1。
//
// 最后一档从 faint 换成 hint：「搁置」旁边渲染的是"4 天前"这种**句子**，
// 而 faint 是按非文字 3:1 定的装饰色，拿去承载句子就违了上面自己写的规矩。
// 换完四档全部过 AA（最低 4.61:1），代价是 week↔cold 只剩 1.13:1 —— 见下。
//
// 但颜色只是**第三条**通道，不是唯一那条 —— 圆点(●/○/无) 和字重 (600/400) 都得留着。
// 原因还是量出来的：week↔cold 亮度差只有 1.13:1，转成灰度图、或者红绿色弱的人看，
// 这两档几乎并成一个。真正把它们分开的是彩度（蓝灰 C*11.5 / 中性 C*4.1），而彩度恰恰是
// 灰度图里最先没的那条。所以：颜色负责一眼扫，圆点+字重负责扛住颜色失效的那些场景。
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
// 四档分成**两个色带**：热的两档给绿，凉的两档退回灰阶。
// 用绿而不另开一支色相，是因为「还热着」在这一页跟「已覆盖」是同一种好 ——
// 条子绿、✓ 绿、刚动过也绿，同一个意思全页只有一支颜色。
// 凉的两档**不给琥珀/红**：搁置是元信息，不是错。一上暖色就等于替人下
//「这个域该骂」的结论，而这一页没有任何依据这么说。
// 每一档都另留一条不靠辨色的出口：圆点 实心●/空心○/没有 + 字重。
//
// 2026-09-01：热的两档在**域网格里**从"绿圆点 + 墨字"改成整格一颗浅绿药丸
// （`pill`）。原来只有一颗 6px 的绿点上色，被点名"这一列没改" —— 说得对，
// 一颗点在 24 行里根本扫不出来。
// ⚠ 实测热的行是 **11/24（46%）**，不是少数。这个数字看着违反本页的音量规矩
//   （见风险那一列：≥9 才带壳，占 30%），但那条规矩管的是**报警**，不管**分类**：
//   这一列答的是"这个域还在做吗"，是个二分，而二分不能只标一半 ——
//   少标的那一半会被读成"没数据"（跟优先级那一列同一个道理，见 PRIORITY_TONE）。
//   46% 对 54% 恰恰是这份数据的实情：这个仓有一半的域在推进、一半搁着，
//   一眼看见"差不多一半"本身就是要传达的信息。
// 凉的两档继续走灰字，一个像素都不动 —— 「搁置」是元信息不是错，
// 给它上暖色等于替人下"这个域该骂"的结论，而这一页没有依据这么说。
// ⚠ `dotColor` 这一列**是给主表用的**（那边不带底，圆点是唯一的彩色出口），
//   别再像第一版那样把它改成墨色 —— 那会把主表 530 行的绿点一起改掉。
//   药丸里的圆点在**渲染处**就地压成墨色：浅绿底上再放绿点只有 1.61:1，看不见。
const ACTIVITY_TIERS = [
  { key: 'live',  within: 6 * H,    label: '刚动过', note: '离本仓最后一次动静 6 小时内（同一轮）', dot: '●', dotColor: VIVID.ok, color: C.ink,  weight: 600, pill: 'ok' },
  { key: 'today', within: D,        label: '今天',   note: '24 小时内',        dot: '○', dotColor: VIVID.ok, color: C.ink,  weight: 400, pill: 'ok' },
  { key: 'week',  within: 7 * D,    label: '本周',   note: '一周内 —— 别人今天动了，它没有', dot: '○', dotColor: C.faint, color: C.gray,  weight: 400 },
  { key: 'cold',  within: Infinity, label: '搁置',   note: '一周以上没动过',   dot: '',  dotColor: C.faint, color: C.gray, weight: 400 },
]

// 锚点 = 本仓最后一次动静。四档量的都是"离它多远"，不是"离今天多远"。
function activityAnchorOf(domains) {
  const times = (domains || [])
    .map(d => (d.updatedAt ? new Date(d.updatedAt).getTime() : NaN))
    .filter(Number.isFinite)
  return times.length ? Math.max(...times) : null
}

// ⚠ 一个会被当成 bug 的边界，别去"修"它：**档位按锚点算，格子里的文字按当下算。**
// 两个参照点不同（锚点 = 本仓最后一次动静，文字 = 相对现在），所以域网格里可能出现
// 「● 2 天前」是绿的、「○ 2 天前」是灰的 —— 同样的字，不同的色。
// 实测（2026-09-01，锚点 08-31 18:22）：24 个域里只有「1 天前」（live/today，两档都绿，
// 看不出来）和「2 天前」（today/week，绿↔灰）会跨档。
// 想让色和字对上，只有两条路，两条都更坏：
//   ① 档位改成按自然日算 —— 这正是上面那段注释否掉的方案（标记恒真）；
//   ② 文字改成相对锚点 —— 那就答不了「这东西多久没人动了」这个真问题。
// 所以留着，靠圆点（实心/空心）和悬浮层里的两个绝对时间兜底 —— 这个分歧在上一版
// 就已经由圆点表达了，上色只是把它变响，不是把它造出来。
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
      <span style={{ color: C.gray }}>{label}</span>{' '}
      {at ? absWhen(at) : empty}
      {commit?.sha && (
        <div style={{ color: C.gray, paddingLeft: 42 }}>
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
          <div style={{ color: C.gray, borderTop: `1px solid ${C.line}`, paddingTop: 4 }}>
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
            {/* 悬浮层现在是白底（见 PAGE_THEME.components.Tooltip），而档位色本来就是
                照白底调的 —— 所以图例可以跟格子里用同一支色。
                图例和格子不同色，比没有图例更坏：它会教错人。 */}
            {ACTIVITY_TIERS.map(t => (
              <div key={t.key} style={{ paddingLeft: 6, marginBottom: 2 }}>
                <span style={{
                  fontWeight: t.weight, color: t.color,
                  ...(t.pill ? { background: WASH[t.pill], padding: '1px 7px', borderRadius: 9 } : null),
                }}>
                  <span style={{ display: 'inline-block', width: 14, color: t.pill ? C.ink : t.dotColor }}>{t.dot}</span>
                  <b>{t.label}</b>
                </span>
                {' · '}{t.note}
              </div>
            ))}
          </div>
        </div>
      }
    >
      <span style={{ width: 96, textAlign: 'right', whiteSpace: 'nowrap' }} onClick={e => e.stopPropagation()}>
        <span
          style={{
            color: act ? act.color : C.faint,
            fontWeight: act ? act.weight : 400,
            // 只有热的两档带底。带底就得有内边距和圆角，否则字贴着底边看着像截断的
            ...(act?.pill ? {
              background: WASH[act.pill], padding: '1px 7px', borderRadius: 9,
              display: 'inline-block', lineHeight: '16px',
            } : null),
          }}
        >
          {act?.dot && <span style={{ marginRight: 3, color: act.pill ? C.ink : act.dotColor }}>{act.dot}</span>}
          {onlyCatalog && d.updatedAt && <span style={{ color: act?.pill ? C.ink : C.gray, marginRight: 3 }}>清单</span>}
          {relWhen(d.updatedAt, now)}
        </span>
      </span>
    </Tooltip>
  )
}

// 域行那条覆盖率进度条的颜色。原来 24 个域全是一支绿 —— 长短已经把覆盖率
// 说过一遍了，颜色再说同一件事等于白占一条通道，而这一页真正要先跳出来的是
// **哪几个域卡着门禁**。所以颜色改成「缺的是什么」，跟条长各说一件事。
// 取 BAR 不取 C：这一列画的是条子和图例色块，不渲染字，走图形那档浅色。
// 三支跟 VIVID / WASH 是同色相（差 0.1°/0.0°/0.3°），所以「缺 P0」的条、它右边
// 那颗红图标、P0 标签的红底纹，是同一支色相的三个亮度档 —— 整页只有一套语义。
// 缺口那颗药丸的底：跟进度条**同一个分类器**（COVER_STROKE 的 key）映到 TAG_TONE。
// 单独列一张表而不是在渲染处写 if，是为了让"两处必须同源"这件事在代码里看得见。
const GAP_TONE = { p0: 'bad', gap: 'warn', full: 'ok' }

// AI 评审那枚筹码的底：305° 淡紫的 L*92 档（C*11.7，65% 上限），墨字其上 8.15:1。
// 比 WASH.low 再淡一档（dE 11.6）—— 它是**动作入口**，不是一个语义标签，
// 不该和 P2 那颗药丸看着一样。
const AI_CHIP = '#ece5f7'

const COVER_STROKE = [
  { key: 'p0', color: BAR.danger, label: '缺 P0', note: 'P0 有缺口 —— check-coverage.sh 直接 BLOCK' },
  { key: 'gap', color: BAR.warn, label: '有缺口', note: '缺的都不是 P0，不阻断门禁' },
  { key: 'full', color: BAR.ok, label: '全认领', note: '清单里每条都有脚本认领 —— 认领不等于跑绿' },
  // 这一支取 BAR_TRAIL（真空轨那支），不取 C.line：图例是用来解释右边那根条子的，
  // 拿一支"差不多"的色去画它，等于图例和被解释的东西对不上（详见 C.line 的注释）。
  { key: 'none', color: BAR_TRAIL, label: '清单没行', note: '清单里读不到这个域的行，条是空的' },
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
          <div style={{ color: C.gray }}>{absWhen(at)}</div>
        </div>
        {stale && (
          <div style={{ marginTop: 6, paddingTop: 6, borderTop: `1px solid ${C.line}` }}>
            <div><b>⚠ 这次评审已经过期</b></div>
            <div>
              评审跑完之后，这个域的{onlyCatalog ? '清单侧' : '脚本侧'}在
              {' '}{absWhen(d.updatedAt)}{' '}又动过。
            </div>
            <div style={{ color: C.gray }}>
              上面那句结论是改动<b>之前</b>得出的，现在不一定还成立 ——
              要拿它当验收依据，得重评一次。
            </div>
            {onlyCatalog && (
              <div style={{ color: C.gray, marginTop: 4 }}>
                不过清单侧常常是整仓批量提交扫到的（重命名、一次性恢复），
                那种情况下这个域的内容未必真变了 —— 点开看 commit 标题就知道。
              </div>
            )}
          </div>
        )}
        <div style={{ color: C.gray, marginTop: 6 }}>
          {rv.environmentName || '—'} · {rv.commitSha} · 点开看结论
        </div>
      </div>
    }>
      <span style={{ cursor: 'pointer' }} onClick={() => onOpen(rv)}>
        {stale && <WarningFilled style={{ color: VIVID.warn, marginRight: 4, fontSize: 12 }} />}
        <Tag style={{ ...tagStyle(v?.tone || 'mute'), cursor: 'pointer' }}>
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
        // 选中不上色。全页「选中」只有一种表达 = 墨色（筹码是墨底白字，
        // 这里是纯白 + 一圈墨环），跟悬浮的白纱区分得开，也不占色相。
        background: active ? '#fff' : 'transparent',
        boxShadow: active ? RING(C.ink) : 'none', ...style,
      }}
      onMouseEnter={e => { if (onClick && !active) e.currentTarget.style.background = VEIL }}
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
  ok: { short: '属实', text: '「已覆盖」都成立', tone: 'ok',
        why: '清单标了「已覆盖」的场景，脚本读下来都真在验那件事' },
  risky: { short: '部分不实', text: '「已覆盖」部分不成立', tone: 'warn',
           why: '有一部分标了「已覆盖」，脚本其实没验到 —— 断言太松，或在这个环境里整条跳过了' },
  bad: { short: '多数不实', text: '「已覆盖」多数不成立', tone: 'bad',
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
// 色相跟着「挡不挡」走，不是给三个负责人各发一支颜色：
//   脚本要改 = 真挡人 → 红；环境没铺 = 要处理但不挡 → 琥珀；
//   清单要商量 = 平台这边不表态、得跟仓库主人谈 → 蓝。
// 上一版这里给 catalog 发的是绿 —— 而绿在这一页只说「好了」，
// 一支颜色不能既是"覆盖到位"又是"这条口径对不上"。
const BLAME = {
  script: { title: 'QA 的脚本要改', tone: 'bad',
            why: '断言写得站不住：跑绿了也证明不了它认领的那件事' },
  env: { title: '不是脚本的问题：环境没铺东西', tone: 'warn',
         why: '脚本可能写得很对，只是在这个环境里自己跳过了。我们只看得到自己这侧的环境记录，QA 跑的时候有没有，这儿判不了' },
  catalog: { title: '清单口径要商量', tone: 'info',
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

  // 「拉取最新」的提示语。**三种情况必须说得不一样**，不能一律"已拉取最新"：
  //   · 没有 diff（后端刚重启、这次是第一次读）→ 只说拉到了，不报 0 条 ——
  //     报"新增 0 条"会被当成"仓库确实没动"，而事实是"没得比"。
  //   · commit 没变 → 直接说清单没有变化。此前提示成功而数字一动不动，
  //     查了半天才发现是后端没重启（CLAUDE.md 记着这一笔），根因之一就是这句话
  //     从来不区分"拉到了新东西"和"拉了但什么都没变"。
  //   · 有增有改 → 报数。删除的也报，那一列没人会主动去数。
  const refreshText = (d) => {
    if (!d) return '已从 QA 仓拉取最新清单'
    const parts = []
    if (d.added) parts.push(`新增 ${d.added} 条`)
    if (d.updated) parts.push(`更新 ${d.updated} 条`)
    if (d.removed) parts.push(`移除 ${d.removed} 条`)
    if (parts.length) return `已拉取最新：${parts.join('，')}`
    return d.commitChanged ? '已拉到新 commit，场景清单没有变化' : '已是最新，清单没有变化'
  }

  const handleRefresh = async () => {
    setRefreshing(true)
    try {
      const res = await api.post(`/projects/${projectId}/qa-catalog/refresh`)
      setData(res.data)
      if (res.data?.error) message.warning(res.data.error)
      else message.success(refreshText(res.data?.refreshDiff))
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
                  <Tag style={{ ...tagStyle('warn'), marginLeft: 6 }}>清单未对上</Tag>
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
      // antd 的 <Tag> 外壳还是不用（它默认那圈 #d9d9d9 的边落在这一页的纸上是灰白贴纸），
      // 但**底纹要用**：见 PRIORITY_TONE 那一段 —— 四档各有一支色，靠底不靠字。
      // 「—」是这一格真的没填，它才是唯一没有底的样子，跟 P3 的白纱片也分得开。
      render: v => v
        ? <span style={pill(PRIORITY_TONE[v] || 'info', 30)}>{v}</span>
        : <span style={{ color: C.faint }}>—</span>,
    },
    {
      title: <Tooltip title="风险分 = 概率(1–3) × 影响(1–3)，取值 1–9。决定要不要缓解，和优先级是两条独立的轴">
        <span>风险 <InfoCircleOutlined style={{ fontSize: 11, color: C.faint }} /></span>
      </Tooltip>,
      dataIndex: 'risk', width: 74, align: 'center',
      sorter: (a, b) => (a.risk || 0) - (b.risk || 0),
      sortOrder: sortOrderOf('risk'), key: 'risk',
      // 只留一个裸数字，跟左边「优先级」那一列同一个做法 —— 底纹和加粗都撤掉。
      //
      // 撤的理由是数出来的：这份清单 554 行里 **434 行（78%）风险 ≥6**、167 行（30%）≥9。
      // 一个在五行里响四行的强调，不是强调，是底噪 —— 而且它正好把「优先级」那列刚
      // 拆掉的胶囊又贴回来，两列紧挨着一列裸一列带壳，看着像两套设计。
      // 阈值 6 是后端定的（`qa_catalog.HIGH_RISK`，不归这一页管），能改的只有画多响。
      //
      // 分档三段：<6 灰字 / 6~8 墨字 / **≥9 给一颗浅粉药丸**。
      // 只有最烫的那一档带壳，是因为这一列是**报警**不是分类（对照左边「优先级」那一列
      // 的注：那边四档全带壳）—— 报警只喊真的要现在处理的那 30%。
      // ≥9 从"深绛彩字"改成"浅粉药丸"，是因为彩字那一版是暗的（#ca1e3a L*44）；
      // 药丸的底 L*84、里面的「9」是 6.57:1 的墨字，更好认，也没有一处暗色。
      // 粗细再兜一道不靠颜色的出口（≥9 才 600），灰度打印和色弱也还分得出最烫的那批。
      //
      // ⚠ 留个坑给后来人：底纹只许走 WASH，**不能**写 `${riskColor(v)}14` 那种
      // 「拿字色本身兑 8% 当底」。那样底和字同色相同方向，字压不住自己的底：
      // 实测 12px 的「6」只有 4.15:1、「9」只有 4.04:1，全都差一口气到 4.5。
      render: v => v == null ? '—' : (
        v >= URGENT_RISK
          ? <span style={{ ...pill('bad', 22), fontWeight: 600 }}>{v}</span>
          : <span style={{ fontSize: 12, color: riskColor(v) }}>{v}</span>
      ),
    },
    {
      title: <Tooltip title={Object.entries(TIER).map(([k, t]) => `${k}=${t.text}`).join(' · ')}>执行层</Tooltip>,
      dataIndex: 'tier', width: 92,
      // 裸 <Tag> 拿的是全站默认 token（#d9d9d9 的边 + #fafafa 的底），
      // 落到这一页的彩虹纸上是一块灰白贴纸，跟旁边所有标签都不是一套。走本页的中性 tone。
      render: v => v
        ? <Tooltip title={`${v} — ${TIER[v]?.desc || ''}`}><Tag style={tagStyle('mute')}>{tierText(v)}</Tag></Tooltip>
        : <span style={{ color: C.faint }}>—</span>,
    },
    {
      title: '状态', dataIndex: 'state', width: 150,
      render: (v, r) => {
        const t = STATE_TAG[v] || STATE_TAG.gap
        return (
          <Space size={4} wrap={false}>
            <span style={{
              color: t.color, background: t.bg, padding: '2px 8px', borderRadius: 10,
              fontSize: 12, whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 4,
            }}>{t.Icon && <t.Icon style={{ fontSize: 11, color: t.icon }} />}{t.text}</span>
            {r.knownBugs?.length > 0 && (
              <Tooltip title="有脚本，但脚本头上挂着 @known-bug —— 跑得通，结论是红的">
                <Tag style={tagStyle('bad')}>带缺陷</Tag>
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
              // 不用绿色区分主脚本：绿在这一页已经是「已覆盖」，
              // 同一格里再拿它说「这条是主的」，一支颜色就说了两件事。
              // 主次改用字重，颜色跟全页其它可点路径保持同一支。
              color: C.gray, fontWeight: s.primary ? 600 : 400,
              textDecoration: 'underline dotted',
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
              {!r.scriptUpdatedAt && <div style={{ color: C.gray, marginTop: 4 }}>还没有脚本</div>}
              {act && (
                <div style={{ color: C.gray, marginTop: 4 }}>
                  {act.dot} <b>{act.label}</b> —— {act.note}
                </div>
              )}
            </div>
          }>
            {/* 跟上面「按域看缺口」用同一套冷热档：同一个页面里"时间的颜色"
                只能有一个意思。锚点也是同一个（本仓最后一次动静），所以
                场景行和域行的同一个颜色代表同一件事。
                原来整列一支 C.gray —— 一屏几十行，哪条是刚改的看不出来。

                但**粗细不跟着抄**（`act.weight` 在这里故意不用）：
                域网格只有 24 行、"谁在动"就是那一格要回答的问题，加粗是主角待遇；
                这张表 530 行，更新时间是附注。而这份数据的实情是 QA 仓常常一整轮
                一起提交 —— 于是几乎每一行都落在最新那档，`weight 600` 一加，
                整列同时变粗，成了全行最响的东西，比「状态」「优先级」还抢。
                同一套颜色、不同的分量，才是同一件事在两个语境里该有的样子。

                **同一个理由，颜色也只上到圆点上，字退回中性**（`act.color` 在这里
                也故意不用）。上面那句「一整轮一起提交」的后果在实拍里是这样的：
                一屏 8 行的更新时间全是「● 23 小时前」，绿字连成一道竖条，
                而它右边一格就是「已覆盖」的绿药丸 —— 一行里两处绿、整列又都绿，
                绿就不再是「好了」的意思，只是「这一列的底色」。
                一列里每行都一样的东西，本来就不需要颜色去区分。
                所以这里：圆点带色、字用 C.gray。圆点三态 —— 实心绿 = 这一轮动过、
                空心绿 = 今天动过、空心灰 = 更早（"搁置"连点都没有）。
                域网格反过来 —— 那 24 行真的分成两半（实测 11/24 热），
                所以那边热的两档整格给一颗浅绿药丸。**两处形不同是故意的**：
                同一件事，在"就是要回答它"的地方给足，在"只是附注"的地方留一颗点。
                ⚠ 别为了"统一"把药丸抄到这一列来 —— 上面那段说的就是抄过来的后果。 */}
            <span style={{
              fontSize: 12, whiteSpace: 'nowrap', cursor: 'help',
              color: act ? C.gray : C.faint,
            }}>
              {act?.dot && <span style={{ marginRight: 3, color: act.dotColor }}>{act.dot}</span>}
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
              <Tag icon={<BugOutlined />} style={{ ...tagStyle('bad'), maxWidth: 112, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
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
  // 只有这两项是 QA 自己的 check-coverage.sh 会直接 BLOCK 的，其余三项都不阻断门禁
  const blocking = !!(summary?.claimedButUncovered || summary?.orphanScripts)
  const parseLoss = (summary?.unparsedRows || 0) + (summary?.duplicateIds || 0)
  const parseConfusion = (summary?.unresolvedColumns || 0) + (summary?.unknownStateTokens || 0)

  const sourceDetail = repo && (
    <div style={{ maxWidth: 480, fontSize: 12, lineHeight: 2 }}>
      <div>仓库 <code>{repo.url}</code></div>
      <div>分支 <code>{repo.branch}</code>{repo.branchAuto && <Tag style={{ ...tagStyle('mute'), marginLeft: 4 }}>跟默认分支</Tag>}</div>
      <div>清单 <code>{repo.catalogPath}</code>
        <Tag style={{ ...tagStyle(repo.catalogAuto ? 'info' : 'mute'), marginLeft: 4 }}>{repo.catalogAuto ? '自动识别' : '配置指定'}</Tag>
      </div>
      <div>脚本 {summary?.scripts ?? 0} 个
        <Tag style={{ ...tagStyle(repo.caseDiscovery === 'grep' ? 'info' : 'mute'), marginLeft: 4 }}>
          {repo.caseDiscovery === 'grep' ? '按 @scenario 自动捞' : '按配置的 glob'}
        </Tag>
      </div>
      <div>commit <code>{repo.commitShort}</code> {repo.commitSubject}</div>
      <div>提交于 {repo.commitDate ? new Date(repo.commitDate).toLocaleString('zh-CN') : '—'}</div>
      <div>拉取于 {repo.fetchedAt ? new Date(repo.fetchedAt).toLocaleString('zh-CN') : '—'}
        {fetchAge && <Tag style={{ ...tagStyle(fetchAge.stale ? 'warn' : 'mute'), marginLeft: 4 }}>{fetchAge.text}</Tag>}
      </div>
      <div style={{ color: C.gray, marginTop: 6 }}>
        平台对这个仓库只读：clone --bare / fetch / git show，不写一个字。
      </div>
    </div>
  )

  return (
    <ConfigProvider theme={PAGE_THEME}>
    <div className="qa-catalog-page">
      {/* 表头文字在 global.css 里被 `!important` 焊成 --text-regular #4e5969 ——
          H265 的灰蓝，是这一页表格区唯一一支不在色板里的色（实测 7 处，全在表头）。
          这里**不是新开一条例外**：PAGE_THEME 早就声明了 colorTextHeading: C.ink，
          只是被那条 !important 压住没落地，这一段是把这一页自己的意图补完。
          用 `.qa-catalog-page` 提一级选择器权重（0,2,3 打 0,0,3），这样跟样式表
          谁先谁后无关，不靠加载顺序赌。
          底色不用管：--table-header-bg 是 rgba(255,255,255,.42)，一层白纱不是灰底 ——
          「灰底配灰字」那条只中了后半句。 */}
      <style>{`.qa-catalog-page .ant-table-thead > tr > th { color: ${C.ink} !important; }`}</style>
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
              {/* 上一版这一整行（时间 + commit）都跟着过期状态刷成琥珀 —— 页面最顶上
                  一行整条是彩的，而它说的只是"这份数字是什么时候拉的"这件中性的事。
                  现在只有「（14 小时前）」那半句表态，时间戳和 commit 一律中性。 */}
              <div style={{ fontSize: 12, color: C.gray, cursor: 'pointer', whiteSpace: 'nowrap' }}>
                {/* 分支必须显式写「QA 仓分支」：顶栏那个分支选择器是**平台自己的用例分支**
                    （v2.2.0 之类），跟这份清单读的分支是两回事，只写「分支 main」会被
                    当成同一个东西。 */}
                {repo.branch && (
                  <>
                    QA 仓分支 <code style={{ color: C.gray }}>{repo.branch}</code>
                    {repo.branchAuto && '（自动识别）'}
                    {' · '}
                  </>
                )}
                {repo.fetchedAt ? (
                  <>
                    拉取于 {new Date(repo.fetchedAt).toLocaleString('zh-CN')}
                    {fetchAge && (
                      <span style={{ color: fetchAge.stale ? C.ink : C.gray }}>（{fetchAge.text}）</span>
                    )}
                  </>
                ) : '还没拉取过'}
                {' · '}
                <code style={{ textDecoration: 'underline dotted', color: C.gray }}>{repo.commitShort}</code>
              </div>
            </Popover>
          )}
        </div>
      </div>

      {!loading && configured === false && (
        <PageAlert
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
        <PageAlert
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
            {/* 「221 / 284」这两个数原来跟后面那句散文同字号同灰色（12px gray），
                于是整行只有 78% 那个大字看得见 —— 被当面点名"只看到进度条"。
                百分比是**结论**，分子分母才是**能核对的事实**（284 是清单总行数，
                221 是有脚本认领的行数），一起被压成小灰字就等于藏了。
                现在分成两档写：数字 18px 墨色，量词和散文留在 12px 灰 ——
                一行里"能读的数"和"解释它的话"轻重分开，而不是整行一起抬字号
                （整行抬会顶到 78% 的位置，两个大字并排就没有主次了）。 */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 30, fontWeight: 600, color: VIVID.ok, lineHeight: 1 }}>{coverRate}%</span>
              <span style={{ fontSize: 12, color: C.gray }}>
                <b style={{ fontSize: 18, fontWeight: 600, color: C.ink }}>{summary.covered}</b>
                <span style={{ margin: '0 3px' }}>/</span>
                <b style={{ fontSize: 18, fontWeight: 600, color: C.ink }}>{summary.total}</b>
                <span style={{ marginLeft: 5 }}>条清单里有脚本认领</span>
              </span>
            </div>
            {['P0', 'P1', 'P2', 'P3'].filter(p => summary.byPriority?.[p]).map(p => {
              const s = summary.byPriority[p]
              return (
                <Hit key={p} active={priority === p && !state && !quick} onClick={() => jump({ priority: p })}>
                  {/* 跟表格那一列同一颗药丸（同 tone、同宽度），一眼能认出是同一个东西。
                      原来这里是彩字，而且只有 P0 有色 —— 三行下来 P1 是墨、P2 是灰，
                      看着像"这两档没配色"，实际是当时故意只给最急的那档上色。 */}
                  <span style={{ ...pill(PRIORITY_TONE[p] || 'info', 30), flex: '0 0 auto' }}>{p}</span>
                  <Progress
                    percent={s.total ? Math.round((s.covered / s.total) * 100) : 0}
                    size="small" strokeColor={PRIORITY_BAR[p]} trailColor={BAR_TRAIL}
                    style={{ flex: 1, margin: 0 }} showInfo={false}
                  />
                  {/* 每档的分子分母同理：条子只说"大概多少"，具体差几条要靠这两个数。
                      13px 墨色 —— 比总数那对小一档（它们是明细，不是头条），
                      但不再是 12px 灰的"说明小字"。宽度跟着字号从 60 放到 66，
                      不然 P1 的 101/145 会贴到右边沿。 */}
                  <span style={{ color: C.ink, fontSize: 13, width: 66, textAlign: 'right' }}>
                    {s.covered}/{s.total}
                  </span>
                </Hit>
              )
            })}
            {/* 「N 条挂着缺陷」和「几个缺陷单」是两个数：一个单子常压住好几条场景
                （实测 F-5 一个号压住 8 条）。只给前一个数，会被读成"有 12 个缺陷要修" */}
            {summary.coveredWithBugs > 0 && (
              <div style={{ marginTop: 8 }}>
                {/* 整句刷红是上一版的写法：一行里连"条挂着已知缺陷，归到""个缺陷单"这些
                    连接词都是红的，红就不再是重点，只是这一行的底色。
                    一行留一处彩 —— 图标和两个数走红，散文走灰，眼睛直接落在数上。 */}
                <Hit active={quick === 'bugs'} onClick={() => jump({ quick: 'bugs' })} style={{ color: C.gray }}>
                  <WarningFilled style={{ color: VIVID.red }} />
                  <span style={{ flex: 1 }}>
                    <b style={{ color: C.ink }}>{summary.coveredWithBugs}</b> 条挂着已知缺陷，归到
                    {bugRefs.length > 0 ? (
                      <Popover
                        placement="bottomLeft"
                        title={<span style={{ fontSize: 12 }}>这些红在等 {bugRefs.length} 个缺陷单</span>}
                        content={
                          <div style={{ maxWidth: 360, fontSize: 12, lineHeight: 1.9 }}>
                            {bugRefs.map(b => (
                              <div key={b.ref}>
                                <code style={{ color: C.ink }}>{b.ref}</code>
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
                          style={{ cursor: 'help', borderBottom: `1px dotted ${C.faint}`, margin: '0 2px' }}
                        >
                          <b style={{ color: C.ink }}>{bugRefs.length}</b> 个缺陷单
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
              <span style={{ fontSize: 30, fontWeight: 600, color: VIVID.warn, lineHeight: 1 }}>{summary.gap}</span>
              <span style={{ fontSize: 12, color: C.gray }}>条场景还没有任何脚本</span>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
              {['P0', 'P1', 'P2', 'P3'].filter(p => summary.byPriority?.[p]?.gap).map(p => (
                <Tag
                  key={p} onClick={() => jump({ priority: p, state: 'gap', sortRisk: true })}
                  style={priority === p && state === 'gap'
                    // 选中＝实底白字（P0 5.71:1 / P1 5.72:1 / P2·P3 6.44:1，都够 AA）
                    ? { margin: 0, cursor: 'pointer', border: 0, background: SELECTED_FILL, color: '#fff' }
                    : { ...tagStyle(PRIORITY_TONE[p]), cursor: 'pointer' }}
                >
                  {p} 缺 {summary.byPriority[p].gap}
                </Tag>
              ))}
            </div>
            <div style={{ borderTop: `1px solid ${C.line}`, paddingTop: 8 }}>
              <div style={{ fontSize: 11, color: C.gray, marginBottom: 4 }}>
                要挑一批今天就动手的，就挑这批：
              </div>
              {/* 这里**故意不用 antd 的 <Button>**。
                  全站 global.css 把 .ant-btn 的三种配色全用 !important 焊死了：默认档一律
                  刷成品牌青（rgba(14,165,160,.08) + #0ea5a0），danger 档一律 #e8453c。
                  两条都赢过行内 style —— 也就是说，只要挂着 .ant-btn，这一页就**没法**给
                  自己的按钮定色。实测过两种写法在这张卡上的下场：
                      danger 档   #e8453c on #f5f9fc   3.71:1  ← 12px 正文要 4.5，不过
                      默认档      #0ea5a0 的青         整块卡里唯一一处第五色相
                  这个控件的实际身份是**筛选筹码**（点一下把表筛成这批），不是表单提交，
                  用原生 button 语义一样全（键盘可达、disabled 生效），还躲开了那套 !important。
                  颜色就用本页的：选中是墨底白字（8.5:1），没选中是红底纹 + 墨字（6.57:1）。 */}
              <button
                type="button" disabled={!urgentCount}
                onClick={() => jump({ quick: 'urgent', sortRisk: true })}
                style={{
                  font: 'inherit', fontSize: 12, lineHeight: 1.6, padding: '3px 12px',
                  borderRadius: 20, borderStyle: 'solid', borderWidth: 1,
                  cursor: urgentCount ? 'pointer' : 'default',
                  ...(!urgentCount
                    // 禁用不靠"调淡到看不清"来表达 —— 那等于把一句话降到读不到。
                    // 平掉颜色、去掉手型就够了，字仍然是 4.61:1 的 hint。
                    ? { background: 'transparent', borderColor: C.line, color: C.gray }
                    : quick === 'urgent'
                      ? { background: SELECTED_FILL, borderColor: SELECTED_FILL, color: '#fff' }
                      : { background: WASH.danger, borderColor: BAR.danger, color: C.ink }),
                }}
              >
                P0 待补 · 风险 9 —— {urgentCount} 条
              </button>
            </div>
          </Panel>

          {/* 3. 清单可信吗 —— 前两项是 QA 自己门禁会 BLOCK 的，不该埋在页面底部 */}
          {/* 标题右边那行的颜色跟里面五行同一个分组：只有 QA 门禁会 BLOCK 的那两项才判红，
              其余三项只到琥珀。上一版是"只要有一项不为 0 就整卡红"——
              于是「风险排低了 3 条」和「268 行读串了」在卡片上长得一模一样。 */}
          <Panel
            title="清单可信吗"
            extra={healthy
              ? <span style={{ fontSize: 11, color: C.gray }}><CheckCircleFilled style={{ color: VIVID.ok }} /> 清单和脚本对得上</span>
              : blocking
                ? <span style={{ fontSize: 11, color: C.gray }}><WarningFilled style={{ color: VIVID.red }} /> 有会被门禁挡住的</span>
                : <span style={{ fontSize: 11, color: C.gray }}><WarningFilled style={{ color: VIVID.warn }} /> 有要处理的</span>}
          >
            <Hit active={quick === 'lying'} onClick={() => summary.claimedButUncovered && jump({ quick: 'lying' })}>
              {summary.claimedButUncovered
                ? <WarningFilled style={{ color: VIVID.red }} />
                : <CheckCircleFilled style={{ color: VIVID.ok }} />}
              <span style={{ flex: 1 }}>标了「已覆盖」却没有任何脚本</span>
              <b style={{ color: summary.claimedButUncovered ? C.ink : C.gray }}>{summary.claimedButUncovered}</b>
            </Hit>
            <Hit>
              {summary.orphanScripts
                ? <WarningFilled style={{ color: VIVID.red }} />
                : <CheckCircleFilled style={{ color: VIVID.ok }} />}
              <span style={{ flex: 1 }}>脚本声明了清单外的 ID</span>
              <b style={{ color: summary.orphanScripts ? C.ink : C.gray }}>{summary.orphanScripts}</b>
            </Hit>
            {/* 五行的**计数一律墨色**，红/琥珀只走左边那颗图标：前两行红、后三行琥珀。
                数字自己不上色，是因为这一页的规矩是**颜色只画色块，文字只用墨色** ——
                轻重由「图标那支色」＋「0 退灰、非 0 转墨」两样一起说，不靠给数字染色。
                红在这一页是「挡住你的」，而这张卡自己的脚注写得很清楚：
                只有前两项是 check-coverage.sh 会直接 BLOCK 的，后三项一条都不阻断。
                上一版这五行是 红/红/琥珀/红/红 —— 四行红把"会挡"和"不挡"混成一片，
                于是**唯一真正要人现在动手的那两行，反倒没了重量**。
                （琥珀＝要处理但不挡，正是后三行的身份；不用蓝，蓝在这一页是"不表态"，
                而"268 行整份读串了"是明确要人处理的。） */}
            <Hit active={quick === 'mismatch'} onClick={() => summary.riskMismatch && jump({ quick: 'mismatch' })}>
              {summary.riskMismatch
                ? <WarningFilled style={{ color: VIVID.warn }} />
                : <CheckCircleFilled style={{ color: VIVID.ok }} />}
              <span style={{ flex: 1 }}>风险 ≥{HIGH_RISK} 却排在 P2/P3</span>
              <b style={{ color: summary.riskMismatch ? C.ink : C.gray }}>{summary.riskMismatch}</b>
            </Hit>
            {/* 这一行 0 也要显示：只在出问题时才冒出来的指标，跟"没算过"长得一模一样，
                而这里少读一行的后果是那条场景在页面上根本不存在 —— 覆盖率不掉、缺口不涨 */}
            <Popover
              placement="bottomLeft"
              title={<span style={{ fontSize: 12 }}>解析这份清单时丢掉的行</span>}
              content={
                <div style={{ maxWidth: 460, fontSize: 12, lineHeight: 1.8 }}>
                  {parseLoss === 0 && (
                    <div style={{ color: C.ink }}>
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
                      <b style={{ color: C.ink }}> {catalogIssues.duplicateIds.join(' ')}</b>
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
                    ? <WarningFilled style={{ color: VIVID.warn }} />
                    : <CheckCircleFilled style={{ color: VIVID.ok }} />}
                  <span style={{ flex: 1 }}>清单里读不进来的行</span>
                  <b style={{ color: parseLoss ? C.ink : C.gray }}>{parseLoss}</b>
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
                      → <b style={{ color: C.ink }}>{COLUMN_ROLE_CN[c.role] || c.role}</b>
                      <span style={{ color: C.gray }}>（{c.basis}）</span>
                    </div>
                  ))}
                  {catalogIssues?.unresolvedColumns?.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <b style={{ color: C.ink }}>没认出角色的列（一个字都没往里填）：</b>
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
                      <b style={{ color: C.ink }}>词表里没有的状态写法（平台自己反推的）：</b>
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
                    ? <WarningFilled style={{ color: VIVID.warn }} />
                    : <CheckCircleFilled style={{ color: VIVID.ok }} />}
                  <span style={{ flex: 1 }}>清单里读不懂的列 / 状态写法</span>
                  <b style={{ color: parseConfusion ? C.ink : C.gray }}>{parseConfusion}</b>
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
                <Tag style={{ ...tagStyle('warn'), marginLeft: 8 }}>更新时间这次没算出来</Tag>
              ) : summary?.activityTruncated ? (
                <Tooltip title="只走了最近 5000 个提交。更早改过的域会显示成「更早」，不是「没动过」">
                  <Tag style={{ ...tagStyle('warn'), marginLeft: 8 }}>时间只算到最近 5000 个提交</Tag>
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
                {/* 这是一句话，不是装饰 —— 按上面 faint 的用途边界，得用 hint。
                    实测 faint 在这块纸上是 3.25:1，hint 是 4.61:1。 */}
                <span style={{ color: C.gray }}>
                  <WarningFilled style={{ color: VIVID.warn, marginRight: 4 }} />
                  评审徽标前有这个 = 评完之后这个域又动过，结论已过期
                </span>
              </div>
              {/* 600 → 618：缺口那格 96→104、更新时间那格 86→96，两格各多留出药丸的
                  内边距。min 跟不上就是进度条被挤成 0 宽（理由见上面那条注释）。 */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(618px, 1fr))', gap: '2px 24px' }}>
                {domainRows.map(d => {
                  const rv = reviews[d.code]
                  return (
                    <Hit key={d.code} active={domain === d.code} onClick={() => jump({ domain: domain === d.code ? undefined : d.code })}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, width: 40 }}>{d.code}</span>
                      <span style={{ width: 110, color: C.gray, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                      <Progress
                        percent={d.total ? Math.round((d.covered / d.total) * 100) : 0}
                        size="small" showInfo={false} strokeColor={coverStrokeOf(d).color} trailColor={BAR_TRAIL}
                        style={{ flex: 1, margin: 0, minWidth: 60 }}
                      />
                      <span style={{ width: 52, textAlign: 'right', color: C.gray }}>{d.covered}/{d.total}</span>
                      {/* 这一格 2026-09-01 从"整格墨色"改成一颗药丸。
                          上一版的理由是频次：24 个域里 22 个缺 P0（92%），而这一页的音量规矩
                          是"响的行要少"（见风险那一列：≥9 才带壳，占 30%），92% 轮不到带壳。
                          那个理由本身没错，但它算漏了一件事 —— **这一列是这一格的落点，
                          不是这一格的全部**：扫的人第一眼要找的就是"哪个域缺 P0"，而墨色让
                          22 行和 2 行长得一模一样，等于把主信号写成了脚注。被当面点名了。
                          现在的做法是上色，但**用左边那根条子同一个分类器**（coverStrokeOf）：
                          缺 P0 → 粉底、缺但不含 P0 → 橙底、全认领 → 绿底，跟条子的颜色一一对上
                          （WASH 和 BAR 是同色相的两个亮度档）。
                          这样"24 行叠成一条粉带"那个担心还在，但那条带**说的和条子是同一句话**，
                          不是第二句 —— 一行之内没有两个互相打架的颜色，只有一句话说了两遍，
                          一遍用长短、一遍用颜色。给 `缺 0` 判绿也是同一个道理。
                          ⚠ 别在这儿另写一套判缺口的 if：颜色一旦和条子不同源，就会出现
                          「条子是绿的、药丸是粉的」那种自相矛盾的行，而没人查得出为什么。 */}
                      <span style={{ width: 104, textAlign: 'right' }}>
                        <span style={{
                          ...tagStyle(GAP_TONE[coverStrokeOf(d).key]),
                          display: 'inline-block', padding: '1px 7px', borderRadius: 9, lineHeight: '16px',
                        }}>
                          缺 {d.gap}{d.p0Gap ? <b> · P0 {d.p0Gap}</b> : null}
                        </span>
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
                          <Tag icon={<LoadingOutlined />} style={{ ...tagStyle('info'), cursor: 'pointer' }}
                               onClick={() => setOpenReview(rv)}>评审中</Tag>
                        ) : rv?.status === 'done' ? (
                          // 徽标是缩写版，长句和时间都在悬浮里补齐 —— 缩了字不等于可以不说全
                          <ReviewBadge d={d} rv={rv} now={renderedAt} onOpen={setOpenReview} />
                        ) : rv?.status === 'failed' ? (
                          // **不给红**：「没评上」是这趟没跑成，不是这个域评出来很差。
                          //   原来它跟「多数不实」同一支粉 —— 扫这一列会数出三个
                          //   「结论很差」的域，实际只有两个，第三个是工具自己挂了。
                          //   跟「评审中」同一支中性药丸：两个都是**还没有结论**。
                          <Tag style={{ ...tagStyle('info'), cursor: 'pointer' }}
                               onClick={() => setOpenReview(rv)}>没评上</Tag>
                        ) : canGenerate ? (
                          // 同样躲开 .ant-btn 那套 !important（理由见上面「P0 待补」那颗筹码）。
                          // 更要紧的是**它得安静**：这一格在 24 行里每行都出现一次，
                          // 而它是全页重复次数最多的可点元素。上一版给的是品牌青的 link 按钮，
                          // 于是 24 个高饱和色块沿右边缘排成一列，比它旁边真正要人看的
                          // 「缺 · P0 n」还抢眼 —— **重复得越多，就越该轻**。
                          // 2026-09-01：从"全灰文字 + 悬停转墨"改成**浅紫筹码 + 墨字**。
                          // 「AI 评审按钮全是灰色」是原话。上一版那个"越重复越该轻"的判据
                          // 走过头了 —— 轻到只剩灰字，它就不再像个能点的东西，
                          // 而它右边那一列**每一行都长得像一段说明文字**。
                          // 底色取 305° 那支淡紫的**再淡一档**（L*92，比 WASH.low 还淡
                          // 一个档，dE 11.6，不会跟 P2 那颗药丸混）。为什么是紫不是三支
                          // 语义色：按这一页的规矩，紫这一族管的就是「不表态好坏」
                          // （P2、P3、空轨都在这一族），而"能点一下"正是不表态 ——
                          // 一个动作入口不该借用「危险 / 待补 / 已覆盖」里的任何一支。
                          // 24 行排下来仍然安静：这个底对纸只有 1.05:1，是一片淡影，
                          // 不是 24 个色块。悬停加深到 WASH.low（同一族，深一档）。
                          <button
                            type="button"
                            onClick={() => { setReviewFor(d); setEnvId(envs[0]?.id) }}
                            style={{
                              font: 'inherit', fontSize: 12, lineHeight: '16px',
                              padding: '1px 8px', borderRadius: 9,
                              border: 0, background: AI_CHIP, cursor: 'pointer',
                              color: C.ink, display: 'inline-flex', alignItems: 'center', gap: 4,
                            }}
                            onMouseEnter={e => { e.currentTarget.style.background = WASH.low }}
                            onMouseLeave={e => { e.currentTarget.style.background = AI_CHIP }}
                          ><RobotOutlined style={{ fontSize: 12 }} />AI 评审</button>
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
              { value: 'covered', label: '已覆盖' },
              { value: 'gap', label: '待补' },
              { value: 'deprecated', label: '已废弃' },
            ]} />
          {quick && (
            <Tag closable onClose={() => setQuick()} style={tagStyle('info')}>
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
                  <a onClick={() => openFile(p)} style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: C.gray, textDecoration: 'underline dotted' }}>
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
        <PageAlert
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
                {(file.header.ids || []).map(id => <Tag key={id} style={tagStyle('mute')}>{id}</Tag>)}
                {file.header.tier && <Tag style={{ margin: 0 }}>{tierText(file.header.tier)}</Tag>}
                {(file.header.knownBugs || []).map((b, i) => (
                  <Tag key={i} icon={<BugOutlined />} style={tagStyle('bad')}>{b}</Tag>
                ))}
              </Space>
            )}
            {file?.truncated && (
              <PageAlert type="warning" showIcon style={{ marginBottom: 10 }}
                     message="文件太大，只显示了前面一段" />
            )}
            <pre style={{
              // 这块原来是 #0f1720 的近黑板 —— 整个应用最暗的一块，而且它跟下面
              // 「判据」那个 pre 是同一个概念（从脚本原样抄来的字），按同一个东西
              // 同一个颜色，就该跟它一样：白纱 + 发丝边 + 墨字。
              margin: 0, padding: 12, background: VEIL, color: C.ink, borderRadius: 6,
              border: `1px solid ${C.line}`,
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
        <PageAlert
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
          <PageAlert type="warning" showIcon message="这个项目还没配环境"
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
            <Tag style={tagStyle(VERDICT[openReview.result?.verdict]?.tone || 'mute')}>
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
          <PageAlert type="error" showIcon message="这次没评上" description={openReview.error} />
        ) : (
          <ReviewTabs r={openReview} onOpenFile={openFile} projectId={projectId} />
        )}
        {openReview && (
          <div style={{ marginTop: 20, paddingTop: 10, borderTop: `1px solid ${C.line}`, fontSize: 12, color: C.gray, lineHeight: 1.9 }}>
            环境 <b>{openReview.environmentName || '—'}</b> · QA 仓 {openReview.branch} <code>{openReview.commitSha}</code>
            {' · '}{openReview.actor} 发起于 {openReview.createdAt && new Date(openReview.createdAt).toLocaleString('zh-CN')}
            <div>结论只存在本平台，QA 仓没有任何变化。</div>
          </div>
        )}
      </Drawer>
    </div>
    </ConfigProvider>
  )
}

// 严重度只描边不填底，所以**边**走图形档的鲜色（3:1 就够），标签里的**字**一律墨色。
// 边已经说了轻重，字再上一遍色是同一件事说两遍，而且 12px 的彩字要过 4.5:1 就得压到
// L*45 —— 那个亮度上的琥珀是深褐色，正是要消掉的暗沉。
// minor 用 C.faint（不是 C.line）：白纱片的发丝边就是 C.line，两者会撞 ——
// g3 那一行「清单口径要商量」和 minor 挨着排，都是浅边浅底，看着像同一个标签断成两截。
// C.faint 是实实在在的灰环（3.09:1，装饰档够用），三档就成了 红／琥珀／灰 的干净三级。
const SEVERITY_EDGE = { blocker: VIVID.red, major: VIVID.warn, minor: C.faint }

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
           style={{ marginLeft: 8, color: C.gray, cursor: 'pointer' }}>
          {open ? '收起' : '怎么看的？'}
        </a>
      </div>
      {(missedS > 0 || missedF > 0 || cut > 0) && (
        <div style={{ fontSize: 12, color: C.ink, lineHeight: 1.9 }}>
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
        <div style={{ fontSize: 12, color: C.ink, lineHeight: 1.9 }}>
          ⚠ {failed.map(i => `第 ${i} 批`).join('、')}没读成（网关限流或超时），
          那几批的脚本这一趟等于没看 —— 重跑一次这个域就补上了。
        </div>
      )}
      {open && (
        <div style={{
          background: VEIL, border: `1px solid ${C.line}`,
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
      <div style={{ fontWeight: 600, color: C.ink }}>
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
      )) : <div style={{ fontSize: 12.5, color: C.gray }}>这一趟一条都没抓到</div>}
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
          <div style={{ color: C.ink }}>
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
              fontVariantNumeric: 'tabular-nums', color: d.count ? C.ink : C.gray,
            }}>{d.count || '—'}</span>
            <span style={{ fontWeight: 600, color: d.count ? C.ink : C.gray }}>{d.name}</span>
            <span style={{ fontSize: 12, color: C.gray }}>{d.why}</span>
          </div>
          {/* 子项 = 怎么判的。想细看的人才往下读，大维度那三个数已经够做决定 */}
          {d.items.map(it => (
            <div key={it.key} style={{
              display: 'flex', gap: 10, alignItems: 'baseline', padding: '3px 0 3px 36px',
            }}>
              {/* 「没查」和「查了没抓到」都是 0，但意思正好相反 —— 混在一起就是假安心 */}
              <span style={{
                width: 18, flexShrink: 0, textAlign: 'right',
                fontVariantNumeric: 'tabular-nums', color: it.count ? C.ink : C.faint,
              }}>{it.unavailable ? '?' : it.count || '—'}</span>
              <span style={{ width: 178, flexShrink: 0, fontSize: 12.5,
                             color: it.count ? C.ink : C.gray }}>{it.name}</span>
              <span style={{ fontSize: 12, color: it.unavailable ? C.ink : C.gray,
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
        background: VEIL, border: `1px solid ${C.line}`,
      }}>
        <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.8, color: C.ink }}>
          {b.headline || res.summary || '这一轮没给出结论'}
        </div>
        {v && (
          <div style={{ fontSize: 12.5, color: C.gray, marginTop: 8, lineHeight: 1.9 }}>
            这次判「<b style={{
              color: C.ink,
            }}>{v.text}</b>」= {v.why}
            {/* 结论词的主语必须写出来。省掉主语，读的人会把它读成"评审只做了一半" */}
            <div style={{ color: C.gray, marginTop: 2 }}>{VERDICT_SUBJECT}</div>
            <div style={{ color: C.gray }}>{VERDICT_SOURCE}</div>
          </div>
        )}
        {/* 人话那段是拼接版时**必须当场说**。退回拼接之后 headline 是概述的前 120 字，
            底下的重点、下一步、撑得住的部分全是空的 —— 这一页于是长成
            「这个域没什么重点」，跟「总结那一趟根本没跑成」一模一样。
            2026-08-29 跑 TEM 时真撞到：明细 14+6 条都在，人看的这页是白的。
            折起来不行，这句要的就是拦住"没重点 = 没问题"这个念头。 */}
        {res.briefSource === 'stitched' && (
          <div style={{ fontSize: 12, color: C.ink, marginTop: 8, lineHeight: 1.9 }}>
            ⚠ 上面这句是<b>拼接版</b>：把各批结论收成一段人话的那一趟没跑成（网关限流或超时），
            所以只剩一句概述，下面的重点和「下一步」是空的 ——
            <b>「没列重点」是这次没写出来，不是这个域没有重点</b>。
            逐条发现一条没少，重跑一次这个域就补上了。
          </div>
        )}
        {/* 存量结论没这个键。**不许当成"收口跑成了"** —— 老记录里同样混着收口挂过的，
            折进去就是把「不知道」渲染成「跑成了」。 */}
        {!res.briefSource && (
          <div style={{ fontSize: 12, color: C.gray, marginTop: 8, lineHeight: 1.9 }}>
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
          <b style={{ color: C.ink }}>QA 改脚本 {n.script} 条</b>
          <span style={{ color: C.faint }}> · </span>
          我们这侧铺环境 {n.env} 条
          <span style={{ color: C.faint }}> · </span>
          找 QA 对清单口径 {n.catalog} 条。
          {/* 变量个数和场景条数是两码事，一屏之内并排出现过 10 和 7，得说清是哪个 */}
          {nEnvVar > 0 && `（环境那几条的根子：我们这条环境记录里缺 ${nEnvVar} 个变量名，
            值要在真正跑套件的地方注入，平台这边补上也不会让 QA 的脚本真跑起来。）`}
          <div style={{ color: C.gray }}>
            要逐条看（哪个文件、哪一句、改成什么）—— 切到隔壁「给 AI / 整改」那一页。
          </div>
        </div>
      )}

      {b.solid?.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontWeight: 600, color: C.ink, marginBottom: 4 }}>撑得住的部分</div>
          {b.solid.map((x, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 0', lineHeight: 1.9 }}>
              <span style={{ color: VIVID.ok, flexShrink: 0 }}>✓</span>
              <span style={{ fontSize: 12.5, color: C.gray }}>{x}</span>
            </div>
          ))}
        </div>
      )}

      {b.nextStep && (
        <PageAlert type="info" showIcon style={{ marginBottom: 12 }}
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
        <div style={{ color: C.ink }}>
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
          <div key={i} style={{ padding: '8px 0', borderTop: i ? `1px dashed ${C.line}` : 'none' }}>
            <Space size={6} wrap style={{ marginBottom: 4 }}>
              {g.id && <Tag style={tagStyle('mute')}>{g.id}</Tag>}
              {/* 动手的人也要先知道这条归谁：改脚本解决不了的那些，别让他白改一遍 */}
              <Tag style={tagStyle(BLAME[blameOf(g)].tone)}>
                {BLAME[blameOf(g)].title}
              </Tag>
              {/* 严重度只描边不填底：它跟左边那颗「谁动手」说的是两件事，
                  两颗都实心会读成同一个标签被切成了两半。 */}
              {g.severity && <Tag style={{ margin: 0, background: 'transparent',
                                           color: C.ink,
                                           boxShadow: RING(SEVERITY_EDGE[g.severity] || C.faint) }}>
                {g.severity}</Tag>}
              {g.path && (
                <a onClick={() => onOpenFile(g.path)} style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: C.gray, textDecoration: 'underline dotted' }}>
                  {g.path}
                </a>
              )}
            </Space>
            <div style={{ lineHeight: 1.8 }}><Rich text={g.problem} /></div>
            {/* 判据锚点：从脚本正文原样抄的，拿去 grep 就能定位到要改的那一句。
                没有它，"这条断言不够"就只是一句评价，接手的人还得自己把整份脚本读一遍。 */}
            {g.evidence && (
              <pre style={{
                margin: '6px 0', padding: '6px 10px', background: VEIL,
                // 跟 Rich 里的行内 code 同一个理由：#476582 是第五支蓝灰。
                // 这里是从脚本正文原样抄来的判据，是这一段的主要内容，给足对比度。
                borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 12,
                color: C.ink, whiteSpace: 'pre-wrap', overflowX: 'auto',
              }}>{g.evidence}</pre>
            )}
            {/* 判据没搜到就必须在**这段引文旁边**说，不能只写在页面顶上那句汇总里：
                照着 evidence 动手的人是一条一条看的，他不会先回头读汇总。
                **只标记，不删也不降 severity** —— severity 说的是「对仓库有多糟」，
                回验说的是「我有多确信」，两个正交的轴合成一个就都读不出来了。
                存量结论（没有这个键）不逐条标：顶上那句已经说了整份都没回验，
                这里再标一遍就是给每一行都糊上噪音。 */}
            {g.evidenceCheck && !EV_PASS.includes(g.evidenceCheck) && (
              <div style={{ fontSize: 12, color: C.ink, lineHeight: 1.8 }}>
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
              <div style={{ fontSize: 12, color: C.gray }}>
                这个域要从外面拿 {res.envMissing.length + res.envSatisfied.length} 个变量，
                其中 {res.envSatisfied.length} 个这个环境里有。
              </div>
            )}
            {res.envMissing.map(v => (
              <div key={v.name}>
                {/* 两档分开画。混在一起是这一列最贵的毛病：一条响亮的假阳
                    （7 组角色账号都在，却报「缺 PASSWORD」）跟真缺口并排、
                    同样的警告色 —— 人扫两眼就把整列当噪音，真缺口跟着被无视。 */}
                <Tag style={{ ...tagStyle(v.state === 'ambiguous' ? 'mute' : 'warn'),
                              fontFamily: 'var(--font-mono)' }}>{v.name}</Tag>
                <Tooltip title={(v.scripts || []).join('\n')}>
                  <span style={{ fontSize: 12, color: C.gray }}>
                    {(v.scripts || []).map(p => p.split('/').pop()).join('、')}
                  </span>
                </Tooltip>
                {v.state === 'ambiguous' && (
                  /* 降级要连**凭什么降**一起写出来，否则它就是一句无从复核的断言 */
                  <div style={{ fontSize: 12, color: C.gray, marginLeft: 2 }}>
                    名字对不上，<b>不是真缺</b>：环境里有{' '}
                    <span style={{ fontFamily: 'var(--font-mono)' }}>
                      {(v.family || []).join('、')}
                    </span>
                  </div>
                )}
              </div>
            ))}
            <div style={{ fontSize: 12, color: C.gray }}>
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

  // **别用 navigator.clipboard.writeText**：平台平时是用局域网 IP 走 http 访问的，
  // 那是非安全上下文，`navigator.clipboard` 整个对象都不存在 —— 于是这个按钮
  // 必抛 `Cannot read properties of undefined (reading 'writeText')`，
  // 一次都没成功过（2026-08-31 报过来的就是这条）。utils/clipboard.js 里
  // 那个 copyToClipboard 备了 textarea + execCommand 的老路，http 下能用。
  const copy = async () => {
    setBusy(true)
    try {
      const d = await fetchMd()
      await copyToClipboard(d.markdown)
      message.success('已复制 Markdown 全文，可直接贴到 issue 或交给 AI')
    } catch (e) {
      // 复制失败 copyToClipboard 自己已经弹过了；这里只管取全文那一步的失败
      if (!e?.reported) message.error(e.message || '复制失败')
    } finally { setBusy(false) }
  }

  const download = async () => {
    setBusy(true)
    try {
      const d = await fetchMd()
      const url = URL.createObjectURL(new Blob([d.markdown], { type: 'text/markdown' }))
      const a = document.createElement('a')
      a.href = url; a.download = d.filename; a.click()
      // 晚一拍再 revoke：紧接着 click 同步撤销会跟下载线程抢，Chrome 偶发存成
      // 一个没扩展名的 blob id 文件。别处（LoadTest 的 downloadText）就是这么写的。
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      // **必须说一句**：这个按钮原来存完一声不响 —— 人不知道成没成、存的是哪个文件，
      // 于是反复点，Chrome 攒出 xxx.md、xxx (1).md、xxx (2).md 一堆，
      // 就成了「下载了一堆东西，不知道是什么」（2026-08-31 报过来的第二条）。
      message.success(`已存成 ${d.filename}`)
    } catch (e) {
      message.error(e.message || '导出失败')
    } finally { setBusy(false) }
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
      padding: '8px 12px', marginBottom: 14, borderRadius: 6,
      background: VEIL, border: `1px solid ${C.line}`,
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

const Nothing = ({ text }) => <div style={{ fontSize: 12, color: C.gray }}>{text}</div>
