# 代理观测 —— HTTP 正向代理观测仪器

验证「网关配了出站代理之后，请求是否真的走了代理」。

**判读方式极简：有记录 = 走了代理；没记录 = 没走代理（功能失效）。**

## 交付形态：两个，各管一件事

| | 用途 | 在哪 |
|---|---|---|
| **testBench 页面**（主交付物） | 测试人员日常用。全程在浏览器里完成判断，不碰命令行 | 菜单 **测试工具 → 代理观测** |
| **独立单文件脚本** | 丢到任意测试机上跑（尤其是没装 testBench 的机器）、命令行验收 ①~⑥ | [tools/proxy_probe.py](proxy_probe.py) |

两者协议处理逻辑一致（日志格式、两种形态、统计字段都对齐），都各自用**真 undici + 真 Go**
验过，不存在「验的是一个、交的是另一个」。

代码位置：

- 监听器 `backend/app/services/proxy_probe_manager.py`（asyncio，随后端进程托管，
  跟 API Mock 一个套路：状态持久化 + 后端重启自动恢复）
- 接口 `backend/app/api/proxy_probe.py`
- 页面 `frontend/src/pages/proxy-probe/ProxyProbe.jsx`

---

## 0. 页面怎么用（推荐路径）

打开 **测试工具 → 代理观测**：

1. 顶部确认是「● 运行中」，没启动就点「启动」。
2. 复制**代理地址**（形如 `http://192.168.51.108:28900`），填到被测系统某个上游服务商的
   「出站代理」里。
   > 这个地址取的是后端探测到的**内网 IP**，不是浏览器地址栏的 host。
   > 因为如果你从 `localhost` 打开 testBench，按地址栏拼出来会是 `http://127.0.0.1:28900`，
   > 容器里的 `127.0.0.1` 是容器自己，填了它请求永远打不过来，本页会一直是空的，
   > 于是被误判成「出站代理没生效」。探测不到内网 IP 时页面会直接红字告警。
3. 点 **「清零」** 打基线（计数归零、列表清空）。
4. 切到被测系统那个标签页，点一次触发请求的按钮。
5. 切回本页 —— 列表每秒自动刷新，新记录会黄色高亮闪一下。

**判读**：列表里出现记录 = 走了代理。清零后操作完仍然是空的 = 没走代理。
页面空状态直接把这句结论写出来了，不用自己推断。

形态列区分链路：`CONNECT`（紫）= Node.js / undici 那条；`GET`/`POST`（绿/橙）=
Go `net/http` 那条。**只做一种形态的后果不是少测一条，而是另一条链路的记录为空、
被误判成代理没生效。**

### 点任意一行看报文

抽屉里分三段，用来回答「代理有没有把请求改坏」：

| 段 | 内容 |
|---|---|
| ① 原始请求 | 客户端 → 代理，原样。能看到 `GET http://…`（absolute-URI）和 `Proxy-Authorization` / `Proxy-Connection` |
| ② 转发给上游的请求 | 代理 → 上游，改写后。能看到请求行已变成 `GET /v1/models HTTP/1.1`（origin-form），并列出**被剥掉的逐跳头** |
| ③ 上游响应 | 状态行 + 响应头，另附请求体/响应体预览（各最多 4KB） |

对比 ① 和 ② 就能自己确认两件事：请求行有没有从 absolute-URI 改写成 origin-form
（不改，规范上游会回 400）；逐跳头有没有剥掉。这两件事以前只能靠读代码相信，现在页面上看得见。

两点实现约束：

- **预览是「旁抄」不是缓冲**：数据收到就立刻转出去，只额外拷前 4KB 留证。
  所以 SSE 仍然是边到边流的（实测事件到达时刻 `[0.0, 0.4, 0.8, 1.2, 1.6]`，不是攒齐才给）。
- **报文原样显示，不做任何删改**：`Proxy-Authorization` 的完整值照原样呈现，
  另外把 base64 解码出的用户名和密码也列出来（base64 肉眼看不出内容），
  方便核对被测系统到底送了个什么凭证过来。这是测试辅助工具，职责是如实呈现收到了什么。
- CONNECT 隧道那条不显示改写后的请求（隧道不改写），并明确写出「隧道内容通常是 TLS 加密，
  本工具不做中间人、不解密」。

独立脚本那份不带这个明细（它的界面是日志文件 + JSON 接口，没有 UI），
需要看报文就用页面。

页面上的「故障注入」实时生效，不用重启：

| 开关 | 验证什么 |
|---|---|
| 拒绝所有请求 | 代理不可达时，被测系统是否明确报错（而非假成功或无限等待） |
| 强制认证（填用户名/密码） | **价值最高**：凭证传错就连不上，把「凭证传对了吗」从看日志猜变成硬断言 |
| 延迟 N 秒 | 被测系统的超时与重试逻辑 |

页面之外仍然保留：日志文件照写（事后追溯），路径见顶部「日志文件」提示；
JSON 接口 `GET /api/proxy-probe/stats`、`POST /api/proxy-probe/reset`、
`GET /api/proxy-probe/records`（注意 testBench 全局中间件会把响应 key 转成
camelCase，是 `connectCount` 不是 `connect_count`；独立脚本那份是 snake_case）。

---

## 1. 独立脚本怎么启动

```bash
python3 tools/proxy_probe.py --log-file /tmp/proxy.log
```

就这一条。默认监听 `0.0.0.0:28900`（代理）+ `0.0.0.0:28901`（统计接口）。

另开一个窗口盯日志，一边点界面一边看：

```bash
tail -f /tmp/proxy.log
```

被测系统那边把出站代理填成 `http://<本机内网IP>:28900`（**不要填 127.0.0.1**，
被测系统在容器里，容器的 127.0.0.1 是它自己）。

## 2. 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` | `0.0.0.0` | 监听地址。**别改成 127.0.0.1**，容器会连不上 |
| `--port` | `28900` | 代理端口 |
| `--stats-port` | `28901` | 统计 / JSON 接口端口；`0` 表示关闭 |
| `--log-file` | 无 | 日志文件路径（不影响 stdout 输出） |
| `--idle-timeout` | `60` | 空闲超时秒数。大模型响应慢，别调小 |

故障注入（用来测被测系统在代理异常时的行为）：

| 参数 | 行为 | 验证什么 |
|---|---|---|
| `--reject-all` | 所有请求立即断开 | 代理不可达时界面是否明确报错，而不是假成功 / 无限等待 |
| `--auth-required user:pass` | 凭证缺失或错误 → `407` + `Proxy-Authenticate: Basic realm="test"` | 被测系统是否真的把正确凭证送出来了 |
| `--delay <秒>` | 转发前延迟 | 被测系统的超时与重试逻辑 |
| `--fail-rate <0~1>` | 按概率返回 `502` | 偶发故障下，被测系统是否会悄悄退回直连 |
| `--allow-host <host[:port]>` | 只放行指定目标，其余 `403`（可重复） | 「逐服务商隔离」是否真的成立 |

`--auth-required` 价值最高：它把「凭证传对了吗」从「看日志觉得像是对的」变成
**传错就连不上**，是更硬的断言。

`--allow-host` 用法：只放行 A 服务商的上游，然后用 B 服务商发请求。
B 成功 → B 确实没走代理（隔离正确）；B 失败 → 代理配置串到 B 上去了。

## 3. 日志怎么读

```
[10:08:47] GET http://10.0.0.100:28100/v1/models (no-auth)          <- absolute-URI 转发形态（Go 侧发来的）
[10:08:47] CONNECT 10.0.0.100:28100 (with-auth user=svc)            <- CONNECT 隧道形态（Node/undici 侧发来的）
[10:08:47]   !! 连接上游失败 127.0.0.1:1 — Connection refused        <- 代理收到了，但转发失败
```

- **第二列的请求形态就是判断哪条链路发来的依据**：`CONNECT` = Node.js/undici，
  `GET`/`POST` 等 = Go `http.Transport`。
- `with-auth user=xxx` / `no-auth` 用来确认凭证有没有正确送达。
- 缩进带 `!!` 的行是错误详情，紧跟在它所属的请求行后面。它的作用是区分
  **「代理根本没收到请求」**（完全没有日志）和 **「代理收到了但转发失败」**（有 `!!` 行）。
- 每行立即 flush 并 fsync，`tail -f` 是实时的。写日志加锁，并发时不会两行交错。

日志行是**一行一条的摘要**，只带 `user=`，不含任何请求头 —— 要看完整报文和凭证，
去页面的明细抽屉（那里原样显示，不删改）。

## 4. 统计接口（自动化断言用）

```bash
curl -s localhost:28901/reset            # 打基线
# ... 触发一次被测系统的操作 ...
count=$(curl -s localhost:28901/stats | jq .connect_count)
[ "$count" -eq 1 ] || echo "失败：代理未生效"
```

`GET /stats` 返回：

```json
{
  "connect_count": 1,
  "http_count": 3,
  "with_auth_count": 1,
  "targets": {"10.0.0.100:28100": 3, "127.0.0.1:1": 1},
  "errors": 1,
  "since": "2026-07-30T02:08:45Z"
}
```

`POST /reset` 清零并返回清零后的快照。

## 5. 验收结果

执行环境：Linux 6.12.74，Python 3.13.5，本机内网 IP `192.168.51.108`，
测试上游 `192.168.51.108:28100`（一个现成的 HTTP 服务，`/v1/models` 返回模型列表）。

| # | 验收项 | 结果 |
|---|---|---|
| ① | absolute-URI 形态 | **通过** — `HTTP 200`，3368 字节；日志一行 `GET` 记录 |
| ② | CONNECT 形态（明文 28100 端口） | **通过** — `HTTP/1.1 200 Connection Established`；日志一行 `CONNECT` 记录 |
| ③ | 凭证解析 | **通过** — 日志行出现 `user=myuser`（日志是一行一条的摘要，不含请求头，所以 `grep -c mypassword` = 0；要看完整凭证去页面明细） |
| ④ | 上游不可达不崩溃 | **通过** — 返回 `502`，日志有 `Connection refused` 记录，进程继续运行 |
| ⑤ | 并发正确性 | **通过** — 20 并发 **2.01s** 完成（串行会是 40s）；20 条日志无交错；`http_count`=20 |
| ⑥ | 容器可达性 | **部分** — 监听确认 `0.0.0.0:28900`，从内网 IP `net.connect` 返回 `ok`；**但本机未装 docker，原 `docker exec` 命令未能执行，需在真实测试机上补跑**（见下方「注意」） |

①②③④⑥ 的原始输出：

```
===== ① absolute-URI 形态 =====
$ curl -x http://192.168.51.108:28900 http://192.168.51.108:28100/v1/models
HTTP 200   响应 3368 字节
{"object":"list","data":[{"id":"gpt-4o","object":"model","created":1785290927,"owned_by":" ...

===== ② CONNECT 形态（明文 28100 端口）=====
$ printf 'CONNECT 192.168.51.108:28100 HTTP/1.1\r\nHost: ...\r\n\r\n' | nc 192.168.51.108 28900
HTTP/1.1 200 Connection Established

===== ③ 凭证解析（日志摘要行只带 user=）=====
$ curl -x http://myuser:mypassword@192.168.51.108:28900 http://192.168.51.108:28100/v1/models
HTTP 200
$ grep -c mypassword /tmp/accept.log  ->  0
$ grep -o "user=myuser" /tmp/accept.log  ->  user=myuser

===== ④ 上游不可达不崩溃 =====
$ curl -x http://192.168.51.108:28900 http://127.0.0.1:1/x
HTTP 502
进程仍在运行 ✓

===== ⑥ 监听地址 / 非 loopback 可达 =====
LISTEN 0      5            0.0.0.0:28901       0.0.0.0:*
LISTEN 0      128          0.0.0.0:28900       0.0.0.0:*
node net.connect 打宿主机内网 IP -> ok

===== 完整日志 =====
[10:08:45] == proxy_probe 启动：代理 0.0.0.0:28900，统计 http://0.0.0.0:28901/stats
[10:08:45] == 空闲超时 60s；日志文件 /tmp/accept.log
[10:08:45] == 故障注入：无（纯观测模式）
[10:08:47] GET http://192.168.51.108:28100/v1/models (no-auth)
[10:08:47] CONNECT 192.168.51.108:28100 (no-auth)
[10:08:47] GET http://192.168.51.108:28100/v1/models (with-auth user=myuser)
[10:08:47] GET http://127.0.0.1:1/x (no-auth)
[10:08:47]   !! 连接上游失败 127.0.0.1:1 — Connection refused
[10:08:47]   == /reset 计数已清零（打基线）
```

⑤ 并发（用一个每请求 sleep 2s 的慢上游，才能区分并发与串行）：

```
直连慢上游单条耗时: 2.00s（确认上游确实慢）
20 条并发经代理：成功 20/20，总耗时 2.01s
  -> 并发正确应≈2s（单条耗时）；串行会是 40s
  -> 判定: 通过（远小于串行）
新增日志行: 20（期望 20）  严格匹配单行格式(=无交错): 20  畸形/交错行: 0
统计接口: {"connect_count": 0, "http_count": 20, ..., "errors": 0}
断言 http_count==20 : PASS      断言 errors==0 : PASS
```

### ⑦~⑩ 页面验收（浏览器实测）

截图不入库（对话产物，不是代码）。要重新出图跑
`python3 tools/proxy_probe_test_page.py`，图落在 `/tmp/shots/`。

用 Playwright 实测，非人工点击，全部通过（控制台无错误）：

| # | 验收项 | 实测结果 |
|---|---|---|
| ⑦ | 浏览器打开页面正常显示，能看到监听状态 | **通过** — 标题/「● 运行中」/「监听 0.0.0.0:28900」均可见；页面显示的代理地址 `http://192.168.51.108:28900`（内网 IP，非回环） |
| ⑧ | 点「清零」→ 计数归 0、列表清空、显示空状态文案 | **通过** — 空状态「等待请求…」和「如果操作完这里仍然是空的，说明请求没有走代理。」均可见；后端 `connectCount=0 httpCount=0` |
| ⑨ | 终端发一次 curl → 不刷新浏览器，1~2 秒内自动出现新记录 | **通过** — 发请求前列表 0 行，2 秒后 1 行，**未 reload 页面**，出现 GET 记录 |
| ⑩ | 连发 CONNECT 和 GET → 两种形态清晰区分 | **通过** — CONNECT 1 行、GET 2 行；标签渲染色实测不同（`rgb(114,46,209)` vs `rgb(56,158,13)`）；认证列显示 `user=svc`（悬停可见完整 `Proxy-Authorization` 和解码后的 `user:pass`） |
| 附加 | 页面故障注入开关实时生效 | **通过** — 点「拒绝所有请求」后后端 `rejectAll=true`，此时 curl 返回码 `000`（连接被断开） |
| 附加 | 明细抽屉（原始请求 / 转发请求 / 上游响应） | **通过** — 三段齐全；原始是 `GET http://…`，转发是 `GET /v1/models HTTP/1.1`；剥掉的逐跳头列出 `Proxy-Authorization`、`Proxy-Connection`；响应头 `HTTP/1.1 200 OK` 和响应体预览可见；**凭证原样显示**（`Basic c3ZjOnNlY3JldDEyMw==` + 解码出 `user=svc` / `password=secret123`）；加了旁抄后 SSE 仍是 `[0.0, 0.4, 0.8, 1.2, 1.6]` 逐个到达 |

自测脚本（都可重复跑，退出码 0 = 通过）：

```bash
python3 tools/proxy_probe_test_backend.py   # 后端监听器：并发 / SSE 不缓冲 / 请求体透传 / origin-form
python3 tools/proxy_probe_test_page.py      # ⑦~⑩ 页面
python3 tools/proxy_probe_test_detail.py    # 明细抽屉三段 + 凭证原样显示
```

后端那一份监听器（页面用的就是它）也跑了完整协议验收：

```
①absolute-URI HTTP 200 3368 字节   ②CONNECT 明文 28100 → 200 Connection Established
③with-auth user=myuser，日志与 API 中 mypassword 均为 0 次
④上游不可达 → 502，进程继续跑
⑤20 并发 2.02s 完成（串行会是 40s），/stats httpCount=20 errors=0
SSE 事件到达时刻 [0.0, 0.4, 0.8, 1.2, 1.6] → 不缓冲
Content-Length 5000 / chunked 3300 透传，上游看到 "POST /echo HTTP/1.1"，Proxy-* 头数量 0
真 undici → CONNECT ；真 Go net/http → GET absolute-URI，user=svc，secret123 不泄露
```

### 用真客户端验证两种协议形态（最关键的一段）

第 6 节的 ①② 是用 `curl -x` 和手写 `CONNECT` 报文验的。手写报文只能证明
「我按这个形态发，工具能处理」，**不能证明被测系统真的按这个形态发**。
所以另外用需求指名的那两个真客户端各打了一次：

| 链路 | 真客户端 | 代理日志实际收到 |
|---|---|---|
| Node.js / undici | Node 24 内置 undici（`NODE_USE_ENV_PROXY=1`） | `CONNECT 192.168.51.108:28100 (no-auth)` |
| Go / `http.Transport` | Go 1.25.11 + `http.ProxyURL` | `GET http://192.168.51.108:28100/v1/models (no-auth)` |

```
$ NODE_USE_ENV_PROXY=1 http_proxy=http://<IP>:28900 node -e "fetch('http://<IP>:28100/v1/models')..."
undici 结果: HTTP 200 | 字节 3368
代理日志 -> [10:17:17] CONNECT 192.168.51.108:28100 (no-auth)

$ go run . "http://<IP>:28900" "http://<IP>:28100/v1/models"      # http.Transport{Proxy: http.ProxyURL(p)}
Go 结果: HTTP 200 | 字节 3368
代理日志 -> [10:18:44] GET http://192.168.51.108:28100/v1/models (no-auth)
```

**结论：需求里那条警告是真的。** 真实 undici 对 **明文 `http://`、28100 端口** 的上游
依然发 `CONNECT`。如果按「只有 443 才允许 CONNECT」实现，Node 这条链路会被错误拒绝，
测试时代理日志为空，进而被误判成「代理没生效」——正是需求要避免的误报。
同时这也证实了「**日志第二列就是判断哪条链路的依据**」这个用法真的成立。

真客户端带凭证也验了（`user=` 提取正确）：

```
undici 带凭证 -> [10:17:44] CONNECT 192.168.51.108:28100 (with-auth user=svc)
Go   带凭证 -> [10:18:44] GET http://.../v1/models (with-auth user=svc)
两者日志中 secret123 出现次数均为 0
```

`--auth-required` 对真客户端确实构成硬断言，不是「看日志觉得像是对的」：

```
凭证错误   -> undici: fetch failed (Request was cancelled)   代理日志: !! 认证失败 — 凭证不匹配 (user=svc)，返回 407
缺少凭证   -> undici: fetch failed (Request was cancelled)   代理日志: !! 认证失败 — 缺少凭证，返回 407
凭证正确   -> undici: HTTP 200
```

### 超出第 6 节、额外补测的项

第 6 节没覆盖但属于 MUST 的几条，单独验了：

```
========== 不缓冲响应体（SSE 流式）==========
各事件到达时刻(秒): [0.0, 0.4, 0.8, 1.2, 1.6]
首个事件 0.00s / 全部结束 2.00s
判定: 通过 —— 事件逐个流过来（若缓冲则首字节≈总耗时 2.0s）

========== 请求体透传 ==========
Content-Length : {"mode":"content-length","received":5000,"proxy_headers":0}  通过
chunked        : {"mode":"chunked","received":3300,"proxy_headers":0}         通过
origin-form    : 上游实际看到的请求行 = "POST /echo HTTP/1.1"（不含 absolute-URI）通过
Proxy-* 剥除   : 上游看到的 Proxy-* 头数量 = 0                                通过

========== CONNECT 隧道真能通数据（不只回 200）==========
隧道内跑一次普通 HTTP -> HTTP/1.1 200 OK，3514 字节，含真实模型列表  通过

========== 故障注入五项 ==========
--auth-required : 无凭证 407(带 Proxy-Authenticate) / 错凭证 407 / 对凭证 200   通过
--reject-all    : curl (52) Empty reply from server                            通过
--delay 2       : HTTP 200，耗时 2.004s                                        通过
--fail-rate 1.0 : HTTP 502                                                     通过
--allow-host    : 白名单内 200 / 白名单外 403 / 白名单外 CONNECT 403            通过
上述故障注入的日志行均为一行一条的摘要格式（只带 user=，不含请求头）        通过

========== 错误隔离（畸形请求不许打死进程）==========
纯二进制垃圾 / 控制字符 method / 超长 method / 非法 IPv6 字面量 / 超长 URL /
origin-form 误用 / 空连接  ->  全部得到 400（或按预期静默断开），进程存活，
随后的正常请求照样 HTTP 200
日志中控制字符数 = 0，最长日志行 261 字符（80KB 的畸形 URL 已截断）           通过

========== 挂机稳定性 ==========
开发过程中一个实例连续运行 15 小时 29 分（跨机器休眠）未退出、未泄漏端口       通过
```

### 接上被测系统之后怎么用

本工具是通用仪器，不针对特定系统：只要对方发的是标准 CONNECT 隧道或 absolute-URI
转发，就能观测。上面的真客户端验证针对的是 **undici 和 Go 标准库本身的行为**，
所以对任何用这两个客户端的服务都成立，不需要按使用方逐个重验。

第一次接到一个新环境时，只有「容器能不能连到宿主机」这一跳需要现场确认
（即验收 ⑥，取决于部署环境的网络和防火墙，跟工具无关）：

```bash
# 1. 宿主机起本工具
python3 tools/proxy_probe.py --log-file /tmp/proxy.log
# 2. 容器可达性先过（这条不过，后面全是「日志为空」的假象）
docker exec <容器> node -e "require('net').connect(28900,'<宿主机内网IP>',()=>console.log('ok'))"
# 3. 页面上把某服务商的出站代理填 http://<宿主机内网IP>:28900，打一次请求，看日志有没有那一行
curl -s localhost:28901/reset   # 先打基线
# ... 触发一次请求 ...
curl -s localhost:28901/stats | jq .
```

判读：`connect_count` 涨了 = Node 那条链路走了代理；`http_count` 涨了 = Go 那条链路
走了代理；两个都是 0 = 没走代理（功能失效）。

## 6. 注意事项 / 已知限制

- **⑥ 必须在真实测试机上补跑。** 本次开发机没有装 docker，
  `docker exec <容器> node -e "require('net').connect(28900,'<宿主机IP>',...)"`
  这条原始命令无法执行。已做的等价验证是：监听地址确实是 `0.0.0.0`（`ss -ltn` 确认），
  且从本机内网 IP（非 loopback）用 `node net.connect` 连通返回 `ok`。
  但**容器网络到宿主机这一段、以及防火墙，只能在真实环境验**——本机也没权限读
  `iptables`/`nft` 规则。这条不通过，后续所有测试都会得到「日志为空」的假象，
  会被误判成代理没生效。
- **每个客户端连接只处理一个请求。** 转发给上游时会加 `Connection: close`，
  本工具不解析响应体，靠上游关闭来界定一次转发的结束。这样才能做到不缓冲、
  SSE 能一直流。代价是不复用连接——对 Go `http.Transport` 和 undici 都没影响
  （它们会自己重新建连），但这是有意的取舍，不是 bug。
- **不做 TLS 中间人**，看不到 HTTPS 内容，只知道「有没有经过」——这正是需求要的，
  也免了往容器里塞 CA 证书。
- **不支持 SOCKS5**（被测系统入口就会拦，无需支持）。
- 停止：`Ctrl-C`，或 `kill <PID>`。退出时会在日志尾部打一行累计计数。

## 7. 复现验收结果

```bash
python3 tools/proxy_probe.py --log-file /tmp/proxy.log &
# 把 <IP> 换成本机内网 IP，<UPSTREAM> 换成一个可访问的 HTTP 服务
curl -x http://<IP>:28900 http://<UPSTREAM>/v1/models                    # ①
printf 'CONNECT <UPSTREAM> HTTP/1.1\r\nHost: <UPSTREAM>\r\n\r\n' | nc <IP> 28900   # ②
curl -x http://myuser:mypassword@<IP>:28900 http://<UPSTREAM>/v1/models  # ③
grep -c mypassword /tmp/proxy.log                                       # ③ 必须是 0
curl -x http://<IP>:28900 http://127.0.0.1:1/x                           # ④ 502
for i in $(seq 20); do curl -s -x http://<IP>:28900 http://<UPSTREAM>/v1/models & done; wait  # ⑤
```

⑤ 和「不缓冲 / 请求体透传」这几项手工不好测（需要一个慢上游、一个 SSE 上游），
所以附了两个自测脚本，各自把测试上游跑在脚本进程内，跑完随进程消失，不留后台残留：

```bash
python3 tools/proxy_probe_test_concurrency.py   # ⑤ 并发 + 日志无交错 + /stats 断言
python3 tools/proxy_probe_test_streaming.py     # SSE 不缓冲 + CL/chunked 请求体 + origin-form
```

两个脚本开头的 `HOST_IP` / `LOG` 改成你的实际值即可，退出码 0 = 全通过，可直接进 CI。

注意 ⑤ 如果上游响应很快（几毫秒），并发和串行的耗时差别会被 curl 自身的进程开销淹没，
测不出东西。要证明并发正确，上游得慢——本次验收用的是一个每请求 `sleep 2s` 的慢上游，
20 并发 2.01s 完成，对比串行的 40s，差异才有意义。
