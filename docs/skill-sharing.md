# Skill 共享通道 —— 把项目的 skill 传上平台，给别的项目取用

## 先分清两类 Skill，这是全篇的前提

平台上「Skill」这个词指两种东西，**边界是谁执行**，混了会真出问题：

| | 平台 Skill | 项目 Skill |
|---|---|---|
| 存在哪 | `backend/app/skills/preset/`（文件系统） | DB `skills` 表 |
| 谁执行 | **平台后端** —— `skill_executor` 读 SKILL.md 当 prompt 喂给 LLM | **客户端** —— 你机器上的 Claude Code |
| 用什么工具 | 平台 MCP 工具（`lum_*`） | 本地工具（Bash / Edit / Playwright） |
| 绑模型档位 | 要，在「AI 能力→模型」页 | 不要，不占档位 |
| 命名 | `lum-*` | 随你 |
| 举例 | `lum-scenario-extract`、`lum-case-generate` | `feature-verify`、`fix-issue`、`seed-data` |
| 接口 | `/api/skills` | `/api/projects/{pid}/skills` |

**为什么必须分开**：项目 skill 如果混进 preset 目录，一是「AI 能力→模型」页会冒出
一批绑不上模型的空档位；二是 `skill_executor` 万一加载到它，会把引用 Bash/Edit 的
内容当 prompt 喂给后端 LLM，那些工具在后端根本不存在，纯烧 token。

本文说的是**项目 Skill**。

---

## 传上来

三条路，按省事程度排。**单文件 skill 就走路线一，别打包。**

### 路线一：页面上粘贴（最省事）

「AI 智能 → Skill 管理 → 项目 Skill → 添加 Skill」，默认就停在「粘贴 SKILL.md」页签，
把内容复制进去点保存即可。不用装东西、不用打包。

- **名字从 frontmatter 的 `name` 读**，所以那一行必须写。忘了写会明确提示你补
- 不知道格式点「填入模板」，会填一份起手骨架
- 可见性当场选：全平台可取用 / 仅本项目

### 路线二：MCP（批量、或带附属文件时最省事）

项目侧的 Claude Code 已经连了 Lumiere MCP，直接说人话：

```
「把我 .claude/skills 下的 feature-verify 传到 Lumiere」
```

它会读本地 `SKILL.md`（连 `references/` 等附属文件一起）然后调：

```
lum_push_skill(project_id, content=SKILL.md全文, files={"references/x.md": "..."})
```

- `name` 不传就取 frontmatter 里的 `name`
- 同名**会覆盖**，覆盖前自动留档，可回滚
- `visibility` 默认 `public`（别的项目能取用）；只想自己用传 `project`

### 路线三：打包上传（只有带附属文件时才需要）

```bash
tar czf feature-verify.tar.gz feature-verify
```

「添加 Skill → 上传压缩包」拖进去。支持 `.zip` / `.tar.gz`，包内必须有 `SKILL.md`，
容忍两种结构（包根就是 SKILL.md，或套一层 `<name>/`）。

### 路线四：curl / CI

```bash
curl -X POST "$BASE/api/projects/$PID/skills" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"content":"---\nname: my-skill\ndescription: ...\n---\n# ...","files":{}}'
```

---

## 取下去

### MCP

```
「看看 Lumiere 上有哪些 skill 能用」   → lum_list_skills
「把 feature-verify 拉到本地」            → lum_pull_skill
```

`lum_pull_skill` 的返回里直接带落盘路径，不用自己拼：

```json
{
  "writeTo": ".claude/skills/feature-verify/SKILL.md",
  "content": "...",
  "extraWriteTo": {"references/checklist.md": ".claude/skills/feature-verify/references/checklist.md"}
}
```

跨项目取用要用 `skill_id`（`lum_list_skills` 返回里有），且该 skill 必须是 `public`。
取自己项目的可以用 `project_id + name`，不受 public 限制。

> 写到本地后**要重启 Claude Code 会话**，新 skill 才会被识别。

### HTTP（CI / 非 Claude Code 场景）

```bash
# 自己项目的
curl "$BASE/api/projects/$PID/skills/feature-verify/bundle" -H "$AUTH" \
  | tar xz -C .claude/skills/

# 别的项目共享的（先 GET /skills/shared 拿 skillId）
curl "$BASE/api/projects/$PID/skills/shared/$SKILL_ID/bundle" -H "$AUTH" \
  | tar xz -C .claude/skills/
```

tar 包解出来就是一个 `<name>/` 目录，直接落进 `.claude/skills/` 即可。

---

## 管理员改与删

「AI 智能 → Skill 管理 → 项目 Skill」分区：

- **编辑** —— 改 SKILL.md 正文和 visibility。保存前自动把旧内容存进 `skill_versions`
- **版本历史 / 回滚** —— 回滚本身也算一次覆盖，当前内容同样先留档，所以回滚可撤销
- **删除** —— 仅项目管理员（`project_admin`）。历史版本靠 FK CASCADE 一起删

附属文件不在页面编辑，要改就重新打包上传。

权限：读 = 含 guest 的全部项目成员；写 = `project_admin` / `developer` / `tester`；
删 = 仅 `project_admin`。

---

## 约束与红线

写入通道对外开放，所以校验全部集中在 `app/services/skill_registry.py`，
HTTP 和 MCP 两条路共用同一份规则 —— 不会出现「页面拦住了、MCP 放进来了」。

- **skill 名**：`^[a-z0-9][a-z0-9._-]{0,63}$`。名字会直接当目录名用，这条正则
  同时是防路径穿越的闸门
- **附属文件路径**：必须相对路径、不含 `.` / `..`、层级 ≤ 5、单条 ≤ 200 字符
- **只存文本**。skill 里放二进制没意义，且会把表撑坏；包里含非 UTF-8 直接拒收
- **大小**：单个 SKILL.md ≤ 512 KB，附属文件 ≤ 50 个，总计 ≤ 2 MB
- `SKILL.md` 只能走 `content` 字段，塞进 `files` 会被拒（避免两份真相）
- `visibility=project` 的 skill 别人取不走 —— 这是 visibility 唯一的作用点

## 相关代码

| 位置 | 干什么 |
|---|---|
| `backend/app/models/skill.py` | `skills` + `skill_versions` 两张表 |
| `backend/app/services/skill_registry.py` | 校验 / frontmatter 解析 / 打包解包 / upsert，两条通道共用 |
| `backend/app/api/project_skills.py` | HTTP 接口 |
| `backend/app/mcp/tools/skills.py` | `lum_push_skill` / `lum_list_skills` / `lum_pull_skill` |
| `frontend/src/pages/settings/ProjectSkillSection.jsx` | Skill 管理页的「项目 Skill」分区 |
| `backend/app/api/skill_manage.py` | 平台内置 `lum-*`，**不是**本文说的这类 |
