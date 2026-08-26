#!/usr/bin/env bash
# 接口场景整模块自测 —— 不只验改动点，把「用例→接口」这条链能做的事全走一遍。
# 每一条都断言到"结果对不对"，不是"有没有 200"。
set -u
B=http://localhost:8756/api
T=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
      -d '{"username":"admin","password":"admin123"}' \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['token'])")
H="-H Authorization:Bearer\ $T -H Content-Type:application/json"

PROJ=c302b27a-e44a-40c6-983a-5db8eda180df          # 测试平台
BR=cf56f1cd-3eb7-436b-8e72-8561b143a5a5
CASE=$(PGPASSWORD=postgres psql -h localhost -U postgres -d lumiere -tAc \
  "SELECT source_case_id FROM api_test_scenarios WHERE branch_id='$BR' LIMIT 1;")
SC=$(PGPASSWORD=postgres psql -h localhost -U postgres -d lumiere -tAc \
  "SELECT id FROM api_test_scenarios WHERE branch_id='$BR' LIMIT 1;")
BASE=$B/projects/$PROJ/branches/$BR/api-tests
PASS=0; FAIL=0
ok(){ PASS=$((PASS+1)); printf "  ✅ %s\n" "$1"; }
no(){ FAIL=$((FAIL+1)); printf "  ❌ %s — %s\n" "$1" "$2"; }
code(){ curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $T" "$@"; }

echo "── 1. 读：列表 / 详情 / 按用例过滤 ──"
n=$(curl -s -H "Authorization: Bearer $T" "$BASE" | python3 -c "import sys,json;print(len(json.load(sys.stdin)['data']))")
[ "$n" -ge 1 ] && ok "列表返回 $n 条" || no "列表" "返回 $n 条"

d=$(curl -s -H "Authorization: Bearer $T" "$BASE/$SC")
echo "$d" | python3 -c "
import sys,json;d=json.load(sys.stdin)['data']
assert d['steps'], 'no steps'
assert d['sourceCaseId'], 'sourceCaseId 空'
assert 'sourceApiIds' not in d and 'preSteps' not in d, '死字段还在返回里'
print(f\"  ✅ 详情：{len(d['steps'])} 步、绑着用例、死字段已不在返回里\")" || no "详情" "结构不对"

m=$(curl -s -H "Authorization: Bearer $T" "$BASE?source_case_id=$CASE" | python3 -c "import sys,json;print(len(json.load(sys.stdin)['data']))")
[ "$m" = "1" ] && ok "按用例过滤：1 条（一个用例一条场景）" || no "按用例过滤" "返回 $m 条"

echo "── 2. 写：建场景的两条闸 ──"
c=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE" -H "Authorization: Bearer $T" \
      -H 'Content-Type: application/json' -d '{"title":"越界·无用例","priority":"P3"}')
[ "$c" = "422" ] && ok "不带 sourceCaseId → 422（不是 500）" || no "非空闸" "HTTP $c"

c=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
      -d '{"title":"越界·假用例","priority":"P3","sourceCaseId":"11111111-1111-1111-1111-111111111111"}')
[ "$c" = "404" ] && ok "用例不存在 → 404（不是撞外键 500）" || no "外键闸" "HTTP $c"

echo "── 3. 已删端点确实不可用 ──"
for p in "stats/quality" "folders"; do
  c=$(code "$BASE/$p"); { [ "$c" = "404" ] || [ "$c" = "422" ]; } && ok "GET $p → $c" || no "GET $p" "还活着 HTTP $c"
done
c=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/$SC" -H "Authorization: Bearer $T")
[ "$c" = "405" ] && ok "DELETE 场景 → 405（端点已删）" || no "DELETE 场景" "HTTP $c（405 才对）"
c=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "$BASE/$SC" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{"title":"x"}')
[ "$c" = "405" ] && ok "PUT 场景 → 405（端点已删）" || no "PUT 场景" "HTTP $c（405 才对）"
c=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/$SC/ai-optimize" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{"suggestion":"x"}')
[ "$c" = "404" ] && ok "AI 优化 → 404" || no "AI 优化" "HTTP $c"

echo "── 4. 步骤 CRUD（用例侧编辑器真正用的那几个）──"
sid=$(curl -s -X POST "$BASE/$SC/steps" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
      -d '{"name":"自测临时步骤","method":"GET","url":"${BASE_URL}/api/health"}' \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['id'])" 2>/dev/null)
[ -n "$sid" ] && ok "新建步骤 $sid" || no "新建步骤" "没拿到 id"
c=$(curl -s -o /dev/null -w '%{http_code}' -X PUT "$BASE/$SC/steps/$sid" -H "Authorization: Bearer $T" \
      -H 'Content-Type: application/json' -d '{"name":"自测改名","retryTimeoutMs":1500}')
[ "$c" = "200" ] && ok "改步骤 → 200" || no "改步骤" "HTTP $c"
got=$(curl -s -H "Authorization: Bearer $T" "$BASE/$SC" | python3 -c "
import sys,json;d=json.load(sys.stdin)['data']
s=[x for x in d['steps'] if x['id']=='$sid'][0];print(s['name'],s['retryTimeoutMs'])")
[ "$got" = "自测改名 1500" ] && ok "改动真落库（$got）" || no "落库" "读回来是 $got"
c=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/$SC/steps/$sid" -H "Authorization: Bearer $T")
[ "$c" = "200" ] && ok "删步骤 → 200" || no "删步骤" "HTTP $c"
left=$(curl -s -H "Authorization: Bearer $T" "$BASE/$SC" | python3 -c "
import sys,json;print(sum(1 for x in json.load(sys.stdin)['data']['steps'] if x['id']=='$sid'))")
[ "$left" = "0" ] && ok "删完真的没了" || no "删除" "还剩 $left 条"

echo "── 5. 步骤顺序 / 复制（两个修过的 bug，钉住别再回来）──"
python3 - "$T" "$BASE" "$SC" <<'PYEOF'
import json,sys,urllib.request
T,BASE,SC=sys.argv[1],sys.argv[2],sys.argv[3]
def call(p,m="GET",b=None):
    r=urllib.request.Request(BASE+p,method=m,data=json.dumps(b).encode() if b else None)
    r.add_header('Content-Type','application/json'); r.add_header('Authorization','Bearer '+T)
    return json.loads(urllib.request.urlopen(r,timeout=30).read() or b'{}')
fail=0
# ① 拖拽排序要落库。原来 saveNodes 只逐个 PUT 内容、不发顺序，拖完刷新回原样，
#    而执行按 sort_order 跑 —— 顺序错了后面一片「变量未解析」，还指错方向。
st=call(f"/{SC}")['data']['steps']; orig=[x['id'] for x in st]
call(f"/{SC}/steps/reorder","PUT",{"stepIds":orig[1:]+orig[:1]})
got=[x['id'] for x in call(f"/{SC}")['data']['steps']]
if got==orig[1:]+orig[:1]: print("  ✅ 拖拽排序真落库")
else: print("  ❌ 排序没落库"); fail=1
call(f"/{SC}/steps/reorder","PUT",{"stepIds":orig})   # 复原
# ② 复制不能覆盖原件。原来副本深拷贝连 id 一起带走，saveNodes 走 PUT 把原件改名了。
o=call(f"/{SC}/steps","POST",{"name":"自测·复制源","method":"GET","url":"${BASE_URL}/x"})['data']
call(f"/{SC}/steps","POST",{"name":"自测·复制源 (副本)","method":"GET","url":"${BASE_URL}/x"})
hits=[x for x in call(f"/{SC}")['data']['steps'] if '自测·复制源' in x['name']]
if len(hits)==2 and any(x['name']=='自测·复制源' for x in hits): print("  ✅ 复制后原件还在、副本是新的一条")
else: print(f"  ❌ 复制把原件覆盖了：{[x['name'] for x in hits]}"); fail=1
for x in hits: call(f"/{SC}/steps/{x['id']}","DELETE")
call(f"/{SC}/steps/reorder","PUT",{"stepIds":orig})
sys.exit(fail)
PYEOF
[ $? = 0 ] && PASS=$((PASS+2)) || FAIL=$((FAIL+1))

echo "── 6. 执行整链 + 出报告 ──"
r=$(curl -s -N -X POST "$BASE/run" -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
    -d "{\"scenarioIds\":[\"$SC\"],\"envId\":\"b3db66cb-745b-488b-8c1b-d004a29da5d6\"}" --max-time 180)
echo "$r" | python3 -c "
import sys,json
passed=None; rid=None; steps=0
for l in sys.stdin:
    if not l.startswith('data:'): continue
    e=json.loads(l[5:])
    if e['type']=='step_result': steps+=1
    if e['type']=='scenario_done': passed=e['passed']
    if e['type']=='report_created': rid=e['reportId']
assert steps>0, '一步都没跑'
assert passed is True, f'场景没通过'
assert rid, '没生成报告'
print(f'  ✅ 整链 {steps} 步全通过，报告 {rid[:8]}…')" || no "整链执行" "见上"

echo
echo "通过 $PASS / 失败 $FAIL"
[ "$FAIL" = "0" ] || exit 1
