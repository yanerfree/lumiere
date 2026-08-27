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

cd "$REPO/backend"
nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" > "$LOG" 2>&1 &
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
