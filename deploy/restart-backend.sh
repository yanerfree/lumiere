#!/usr/bin/env bash
# 幂等重启后端（:8756）。
#
# 为什么要有这个脚本：后端**故意不带 `--reload`** —— lifespan 会绑一整串端口
# （MCP :18800、LLM/API mock 与代理观测的 28xxx 段），`--reload` 每次存盘都要把
# 它们重绑一遍，撞 "address already in use" 不说，还会把连在 :18800 上的
# MCP 客户端（Claude Code 自己）踢下线。代价是：**提交之后不重启，跑的就是旧代码。**
#
# 所以这里做两件 `kill` + `nohup` 做不到的事：
#   1. 等端口真的释放再起（9 个端口一起重绑，抢跑就起不来）
#   2. 起完探活，并把「进程启动时间 vs 最新提交时间」打出来，让"跑的是不是新代码"当场可见
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8756
LOG="$REPO/backend/.logs/uvicorn-lumiere.log"
mkdir -p "$(dirname "$LOG")"

pids_on_port() { ss -ltnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | sort -u; }

# ── 解释器要在**杀之前**定下来 ──
# 这个脚本先 kill 再 nohup。解释器找不到就是「旧的杀了、新的起不来」——
# 后端停机，而报出来只是一行 nohup 的 No such file。**先验后杀**，验不过一个都不动。
# （2026-08-29 在 worktree 里真撞了一次：worktree 的 backend/ 下没有 .venv，
#   而第 35 行写的是相对路径 `.venv/bin/python`。）
# worktree 没有自己的 venv 是正常的 —— venv 不进 git，只有主 checkout 有一份。
# 所以兜底到主 checkout 那个：`--git-common-dir` 在 worktree 里指的就是主库的 .git。
PYBIN=""
for _cand in "$REPO/backend/.venv/bin/python" \
             "$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/../backend/.venv/bin/python"; do
  if [ -x "$_cand" ]; then PYBIN="$(cd "$(dirname "$_cand")" && pwd)/python"; break; fi
done
if [ -z "$PYBIN" ]; then
  echo "✗ 找不到可用的解释器，$REPO/backend/.venv 和主 checkout 的都不在。"
  echo "  **没有动正在跑的后端** —— 先把 venv 装好再来。"
  exit 1
fi
[ "$PYBIN" = "$REPO/backend/.venv/bin/python" ] || echo "注意: 本地无 venv，借用 $PYBIN"

OLD=$(pids_on_port)
if [ -n "$OLD" ]; then
  echo "停止旧后端: $OLD"
  kill $OLD 2>/dev/null
  for _ in $(seq 1 20); do
    [ -z "$(pids_on_port)" ] && break
    sleep 0.5
  done
  if [ -n "$(pids_on_port)" ]; then
    echo "  未退出，强杀"; kill -9 $(pids_on_port) 2>/dev/null; sleep 1
  fi
fi

# cwd 必须是 backend/ —— .env 是按 cwd 找的，换个目录起会静默丢掉它（429 降级通道跟着没）。
cd "$REPO/backend"
nohup "$PYBIN" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" > "$LOG" 2>&1 &
echo "启动中 (pid $!) …"

for _ in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT/api/projects" 2>/dev/null)
  # 401 = 路由已挂载、鉴权生效，就是起来了
  [ "$code" = "401" ] || [ "$code" = "200" ] && break
  sleep 0.5
done

NEW=$(pids_on_port)
if [ -z "$NEW" ]; then
  echo "✗ 起failed，看日志: $LOG"; tail -20 "$LOG"; exit 1
fi

echo "✓ 后端已就绪 (pid $NEW)"
echo "  进程启动: $(ps -o lstart= -p ${NEW%% *})"
echo "  最新提交: $(git -C "$REPO" log -1 --format='%h %ci %s')"
echo "  已绑端口: $(ss -ltnp 2>/dev/null | grep -oP "(?<=:)\d+(?= .*pid=${NEW%% *})" | sort -n | tr '\n' ' ')"
