"""场景 → k6 压测脚本生成器 + 部署指引。

设计：数据驱动。生成的脚本是一段固定的 k6 模板（含与 Lumiere 平台执行语义一致的
helper：变量替换 ${var}、简化 jsonpath 提取、断言→check），把场景的 STEPS / VARIABLES /
executor 配置以 JSON 注入。这样脚本既是标准可读的 k6 脚本，又忠实还原平台里配置的行为。

本模块**不运行 k6**，只产出脚本文本与部署说明——真正的压测在专用压测机上跑。
"""

from __future__ import annotations

import json

# k6 脚本模板：__TOKEN__ 处注入 JSON。JS 大括号原样保留（不要用 f-string / .format）。
_K6_TEMPLATE = r"""// ============================================================================
// Lumiere 生成的 k6 压测脚本
// 场景: __SCENARIO_NAME__
// 生成后可直接在装有 k6 的压测机上运行，见文件末尾「部署与运行」注释。
// 行为与 Lumiere 平台内的执行语义一致（变量替换 / jsonpath 提取 / 断言）。
// ============================================================================
import http from 'k6/http';
import { check } from 'k6';

// ---- 场景数据（由 Lumiere 注入）----
const STEPS = __STEPS__;
const VARIABLES = __VARIABLES__;

// 可选：运行时用 -e BASE=http://目标主机:端口 覆盖所有请求的 scheme+host，
// 便于把同一脚本指向不同环境/靶机，而无需改脚本。
const BASE = __ENV.BASE || '';

export const options = {
  scenarios: {
    default: __EXECUTOR__,
  },
  // 示例阈值：失败率 <1%、p95 <2s。按需修改；超过即判定不达标（k6 退出码非 0）。
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<2000'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

// ---- helpers：与 Lumiere 平台执行语义保持一致 ----
function resolveVars(tpl, env) {
  if (tpl == null) return tpl;
  let r = String(tpl);
  for (const k in env) r = r.split('${' + k + '}').join(String(env[k]));
  return r;
}

function extractPath(body, path) {
  // 简化 jsonpath：$.a.b.0 —— 逐段走 dict/list，与平台 _extract_jsonpath 一致
  try {
    const data = JSON.parse(body);
    const parts = path.replace(/^\$\.?/, '').split('.').filter(Boolean);
    let cur = data;
    for (const p of parts) {
      if (cur == null) return null;
      if (Array.isArray(cur)) cur = cur[parseInt(p, 10)];
      else if (typeof cur === 'object') cur = cur[p];
      else return null;
    }
    return cur == null ? null : String(cur);
  } catch (e) {
    return null;
  }
}

function applyBase(url) {
  if (!BASE) return url;
  try {
    const u = new URL(url);
    const b = new URL(BASE);
    u.protocol = b.protocol;
    u.host = b.host;
    return u.toString();
  } catch (e) {
    return url;
  }
}

export default function () {
  const idx = __ITER;
  const env = {};
  for (const v of VARIABLES) {
    if (v && v.name && Array.isArray(v.values) && v.values.length) {
      env[v.name] = v.values[idx % v.values.length];
    }
  }

  for (const step of STEPS) {
    const name = step.name || ((step.method || 'GET') + ' ' + step.url);
    const url = applyBase(resolveVars(step.url, env));

    const headers = {};
    for (const h of (step.headers || [])) {
      if (h && h.key) headers[h.key] = resolveVars(String(h.value != null ? h.value : ''), env);
    }

    let body = null;
    if (step.body && step.body_type && step.body_type !== 'none') {
      body = resolveVars(step.body, env);
    }

    const res = http.request(step.method || 'GET', url, body, {
      headers: headers,
      tags: { name: name },   // 按步骤分组统计，报告里可看每个接口的分位延迟
    });

    // 提取变量供后续步骤引用
    for (const ext of (step.extractions || [])) {
      const vn = ext.variable_name || ext.variableName;
      const jp = ext.jsonpath;
      if (vn && jp && res.body) {
        const val = extractPath(res.body, jp);
        if (val !== null) env[vn] = val;
      }
    }

    // 断言 → k6 check（与平台 _check_assertion 一致）
    const checks = {};
    checks[name + ' :: status<400'] = (r) => r.status > 0 && r.status < 400;
    for (const a of (step.assertions || [])) {
      const t = a.type || '';
      const val = String(a.value != null ? a.value : '');
      if (t === 'status') {
        checks[name + ' :: status==' + val] = (r) => String(r.status) === val;
      } else if (t === 'body_contains') {
        checks[name + ' :: body 含 "' + val + '"'] = (r) => (r.body || '').indexOf(val) >= 0;
      } else if (t === 'body_regex') {
        let re = null;
        try { re = new RegExp(val); } catch (e) { re = null; }
        checks[name + ' :: body 匹配 /' + val + '/'] = (r) => (re ? re.test(r.body || '') : false);
      }
    }
    check(res, checks);
  }
}

/* ============================================================================
 * 部署与运行
 * __DEPLOY_HINT__
 * ========================================================================== */
"""


def _build_executor(config: dict) -> dict:
    """把平台的并发配置映射成 k6 executor。"""
    vus = max(1, int(config.get("concurrent_users") or 1))
    ramp_up = int(config.get("ramp_up_seconds") or 0)
    total_iter = config.get("total_iterations")
    duration = config.get("duration_seconds")

    if duration:
        duration = int(duration)
        if ramp_up > 0:
            return {
                "executor": "ramping-vus",
                "startVUs": 0,
                "stages": [
                    {"duration": f"{ramp_up}s", "target": vus},
                    {"duration": f"{duration}s", "target": vus},
                ],
                "gracefulRampDown": "0s",
            }
        return {"executor": "constant-vus", "vus": vus, "duration": f"{duration}s"}

    if total_iter:
        return {
            "executor": "shared-iterations",
            "vus": vus,
            "iterations": int(total_iter),
            "maxDuration": "1h",
        }

    # 既没时长也没迭代数：与平台默认一致，跑 vus 次
    return {"executor": "shared-iterations", "vus": vus, "iterations": vus, "maxDuration": "1h"}


def generate_k6_script(config: dict, scenario_name: str = "") -> str:
    """场景 config（同 run.config_snapshot 结构）→ k6 脚本文本。"""
    steps = config.get("steps") or []
    variables = config.get("variables") or []
    executor = _build_executor(config)

    deploy_hint = (
        "1) 装 k6:  Debian/Ubuntu 见下方指引，或 docker run grafana/k6\n"
        " * 2) 直接跑:      k6 run scenario.js\n"
        " * 3) 换靶机:      k6 run -e BASE=http://目标主机:端口 scenario.js\n"
        " * 4) 出报告:      k6 run --summary-export=summary.json scenario.js\n"
        " * 5) 分布式(k8s): 用 k6-operator 把本脚本作为 TestRun 下发到多个 pod"
    )

    script = _K6_TEMPLATE
    script = script.replace("__SCENARIO_NAME__", (scenario_name or "unnamed").replace("*/", "* /"))
    script = script.replace("__STEPS__", json.dumps(steps, ensure_ascii=False, indent=2))
    script = script.replace("__VARIABLES__", json.dumps(variables, ensure_ascii=False, indent=2))
    script = script.replace("__EXECUTOR__", json.dumps(executor, ensure_ascii=False, indent=6))
    script = script.replace("__DEPLOY_HINT__", deploy_hint)
    return script


def generate_deploy_guide(config: dict, scenario_name: str = "") -> str:
    """生成 Markdown 部署指引：在压测机上装 k6、运行、覆盖靶机、出报告、规模化。"""
    vus = int(config.get("concurrent_users") or 1)
    return f"""# k6 压测部署指引 — {scenario_name or '场景'}

> Lumiere 只负责**生成脚本**和**本地小并发试跑**；真正的压力请在**独立压测机**上用 k6 运行，
> 避免压测机与被测系统争抢资源、也避免单机成为瓶颈。

## 1. 在压测机上安装 k6
**Debian / Ubuntu**
```bash
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \\
  --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \\
  | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update && sudo apt-get install k6
```
**macOS**: `brew install k6`　**Docker**: `docker run --rm -i grafana/k6 run - < scenario.js`
**二进制**: 从 https://github.com/grafana/k6/releases 下载解压即用（单文件）。

## 2. 运行
```bash
k6 run scenario.js                              # 按脚本内并发配置直接跑（当前 VUs≈{vus}）
k6 run -e BASE=http://目标主机:端口 scenario.js   # 覆盖所有请求的 host，指向不同靶机/环境
k6 run --vus 500 --duration 2m scenario.js      # 命令行临时覆盖并发/时长
```

## 3. 出报告
```bash
k6 run --summary-export=summary.json scenario.js     # 结束汇总(JSON)：RPS/分位延迟/错误率/阈值
k6 run --out json=result.json scenario.js            # 逐请求明细(可离线分析)
# 实时看板：--out experimental-prometheus-rw 接 Prometheus + Grafana k6 面板
```
脚本已内置阈值示例（失败率<1%、p95<2s）与分接口(tag=name)统计，报告里可看每个接口的分位延迟。

## 4. 规模化（几千~几万+ 并发）
- **单机拉满**: 一台够强的机器 k6 就能几万 VUs；先 `ulimit -n 1048576` 抬高文件描述符。
- **多机**: 每台跑同一脚本分摊 VUs，`--summary-export` 各自出结果后汇总。
- **k8s 分布式**: 用 [k6-operator](https://github.com/grafana/k6-operator)，把本脚本作为 `TestRun` 下发到 N 个 pod，自动聚合。
- **托管**: `k6 cloud scenario.js`（Grafana Cloud k6）。

## 5. 注意
- 压测机与被测系统**分开部署**，否则数字不可信。
- 关注被测端与压测端两侧的 CPU/连接数，别把压测机自己压成瓶颈。
"""
