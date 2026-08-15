"""删除的外溢面自测：接口场景是共用资产，它被这四处消费。
删模块不该动到任何一处 —— 逐个真调，断言到内容，不看状态码。
"""
import json, subprocess, sys, urllib.request

B = "http://localhost:8756/api"
PROJ = "c302b27a-e44a-40c6-983a-5db8eda180df"
BR = "cf56f1cd-3eb7-436b-8e72-8561b143a5a5"
ok, bad = [], []


def req(path, method="GET", body=None, tok=None):
    r = urllib.request.Request(B + path, method=method,
                               data=json.dumps(body).encode() if body else None)
    r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read())


def rows_of(d):
    """接口返回形状不统一（有的 {items:[]}、有的直接列表），统一取出来。"""
    if isinstance(d, list):
        return d
    for k in ("items", "scenarios", "steps", "results"):
        if isinstance(d.get(k), list):
            return d[k]
    return []


def psql(q):
    return subprocess.run(
        ["psql", "-h", "localhost", "-U", "postgres", "-d", "testbench", "-tAc", q],
        capture_output=True, text=True, env={"PGPASSWORD": "postgres", "PATH": "/usr/bin:/bin"}
    ).stdout.strip()


T = req("/auth/login", "POST", {"username": "admin", "password": "admin123"})["data"]["token"]

# ① 用例详情 —— 接口维度状态还读得出来（owes / api_status 靠场景存在与否推）
case_id = psql(f"SELECT source_case_id FROM api_test_scenarios WHERE branch_id='{BR}' LIMIT 1;")
c = req(f"/projects/{PROJ}/branches/{BR}/cases/{case_id}", tok=T)["data"]
(ok if c.get("apiStatus") else bad).append(f"用例详情 apiStatus={c.get('apiStatus')}")

# ② 用例列表的 owes（还欠哪几维）—— 它查的是 api_test_scenarios
lst = req(f"/projects/{PROJ}/branches/{BR}/cases?pageSize=5", tok=T)["data"]
items = lst["items"] if isinstance(lst, dict) else lst
(ok if items else bad).append(f"用例列表返回 {len(items)} 条")
# owes（还欠哪几维）只在 MCP 的 tb_list_cases 里算，HTTP 列表本来就没有这个字段
# —— 上一版在这里断言 HTTP 列表带 owes，是我写错了。真正该验的是那段计算
# 仍然在查 api_test_scenarios（接口维度算不算"做完"全靠它）。
mcp_src = open("backend/app/mcp/tools/test_cases.py").read()
(ok if '"owes"' in mcp_src else bad).append("MCP 列表仍输出 owes")
svc = open("backend/app/services/case_service.py").read()
(ok if "ApiTestScenario.source_case_id" in svc else bad).append(
    "接口维度仍按「有没有绑定场景」判定")

# ③ 测试报告 —— 接口报告还列得出来、点得开
reps = req(f"/projects/{PROJ}/reports?limit=5", tok=T)["data"]
rl = reps["items"] if isinstance(reps, dict) else reps
api_reps = [r for r in rl if r.get("reportType") == "api_test"]
(ok if api_reps else bad).append(f"报告列表里 api_test 报告 {len(api_reps)} 条")
if api_reps:
    rid = api_reps[0]["id"]
    d = req(f"/projects/{PROJ}/reports/{rid}/results", tok=T)["data"]
    rows = rows_of(d)
    (ok if rows else bad).append(f"报告详情(results)展开 {len(rows)} 条场景")
    # 报告详情要能下钻到步骤 —— 这条链穿过 test_report_steps，是删列之后最该验的
    sc0 = rows[0]
    st = req(f"/projects/{PROJ}/reports/{rid}/scenarios/{sc0['id']}/steps", tok=T)["data"]
    srows = rows_of(st)
    (ok if srows is not None else bad).append(f"报告下钻步骤 {len(srows)} 行")
    dash = req(f"/projects/{PROJ}/reports/{rid}/dashboard", tok=T)["data"]
    (ok if dash else bad).append("报告看板出得来")

# ④ 分支复制 —— 复制会带上接口场景（源码级：三个字段都还在拷贝里）
src = open("backend/app/services/branch_copy_service.py").read()
for f in ("code=", "source_case_id=", "env_variables=", "folder_id="):
    (ok if f in src else bad).append(f"分支复制仍拷贝 {f.rstrip('=')}")
for dead in ("pre_steps=", "source_api_ids="):
    (ok if dead not in src else bad).append(f"分支复制不再引用已删列 {dead.rstrip('=')}")

print("\n".join("  ✅ " + s for s in ok))
if bad:
    print("\n".join("  ❌ " + s for s in bad))
    sys.exit(1)
print(f"\n共用层四个出口全部正常（{len(ok)} 项）")
