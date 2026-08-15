"""SSE 解析必须活过网络分片 —— 用真 node 跑前端那个解析器。

补的事故：UI 执行面板永远停在「正在收尾」转圈不结束，而后端那次 13.2 秒就
passed 了、script_runs 记录完整。被当成"跑不完"报上来。

真因不在执行，在前端解析：事件名 `currentEvent` 声明在读循环**内部**，每读一个
网络分片重置一次。而 done 那一帧有 47KB（37 步 13KB + 96 条流量 34KB），
一个分片装不下，必然被劈成：

    分片 A: "event: done\\ndata: {\"status\":\"passed\",..."   ← 后半截没换行
    分片 B: "...余下 40KB...}\\n\\n"

A 里 `event: done` 是完整行 → 记下事件名；`data:` 半行留进 buffer 等下一片 ——
然后函数返回，事件名没了。B 里 data 行拼完整了，却因为事件名是 null 被整条跳过。
done 静默丢弃：不报错、不重试、没痕迹。**帧越大越必然触发，跟运气无关。**

为什么写成 pytest 而不是前端测试：这个仓库前端没有测试框架，而这条不能只靠
读源码 grep 一句「currentEvent 不许在循环里声明」—— 那种断言换个写法就绕过去了。
这里是**真跑**：node 加载真模块，在 done 帧的每一个字节位置切一刀，喂进去看
done 到底出没出来。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / "frontend/src/utils/sseParser.js"
CASE_JSX = ROOT / "frontend/src/pages/cases/CaseDetail.jsx"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="没装 node")


def _run_node(script: str) -> dict:
    r = subprocess.run(["node", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, f"node 跑挂了：\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def _harness(body: str) -> str:
    return textwrap.dedent(f"""
        import {{ createSseParser }} from '{PARSER.as_posix()}'
        {body}
    """)


def test_done帧在任意位置被切开都要送达():
    """核心那条。在 done 帧每个字节位置切一刀，逐个验。

    切点覆盖全帧，所以「恰好切在 event: 和 data: 之间」这个要命的位置一定被跑到。
    """
    out = _run_node(_harness("""
        // 造一帧和线上同量级的：37 步 + 96 条流量，约 47KB
        const steps = Array.from({length: 37}, (_, i) => ({
          seq: i + 1, name: `第 ${i+1} 步 点击按钮「确认」`, status: 'passed', duration: 120,
        }))
        const captured = Array.from({length: 96}, (_, i) => ({
          method: 'GET', url: `http://192.168.51.108:5176/api/v1/services?page=${i}`,
          status: 200, responseBody: 'x'.repeat(300),
        }))
        const donePayload = {status: 'passed', durationMs: 13217, steps, capturedRequests: captured}
        const doneFrame = `event: done\\ndata: ${JSON.stringify(donePayload)}\\n\\n`
        const prefix = [1,2,3].map(i =>
          `event: step_start\\ndata: ${JSON.stringify({seq:i, name:'步骤'+i})}\\n\\n` +
          `event: step_end\\ndata: ${JSON.stringify({seq:i, status:'passed'})}\\n\\n`).join('')
        const stream = prefix + `event: finishing\\ndata: ${JSON.stringify({message:'正在收尾'})}\\n\\n` + doneFrame

        const failures = []
        let checked = 0
        // 只在 done 帧范围内切（前面的小帧另有用例覆盖），逐字节
        for (let cut = prefix.length; cut < stream.length; cut++) {
          const seen = []
          const p = createSseParser((ev, data) => seen.push([ev, data]))
          p.push(stream.slice(0, cut))
          p.push(stream.slice(cut))
          checked++
          const dones = seen.filter(([e]) => e === 'done')
          if (dones.length !== 1) { failures.push({cut, dones: dones.length}); continue }
          if (dones[0][1].steps.length !== 37 || dones[0][1].capturedRequests.length !== 96) {
            failures.push({cut, bad: 'payload 不完整'})
          }
        }
        console.log(JSON.stringify({frameBytes: doneFrame.length, checked, failures: failures.slice(0, 5),
                                    failCount: failures.length}))
    """))
    assert out["frameBytes"] > 40000, f"造的帧太小({out['frameBytes']})，跨不了分片就测不出问题"
    assert out["failCount"] == 0, (
        f"{out['checked']} 个切点里有 {out['failCount']} 个丢了 done —— "
        f"前几个：{out['failures']}"
    )


def test_逐字节喂也不能丢事件():
    """最极端的分片：一次只给一个字符。真实网络不会这么碎，但它能一次抓出
    所有「靠一次拿到整行/整帧」的隐含假设。"""
    out = _run_node(_harness("""
        const stream =
          `event: step_start\\ndata: ${JSON.stringify({seq:1, name:'打开页面'})}\\n\\n` +
          `event: finishing\\ndata: ${JSON.stringify({message:'正在收尾'})}\\n\\n` +
          `event: done\\ndata: ${JSON.stringify({status:'passed', steps:[{seq:1}]})}\\n\\n`
        const seen = []
        const p = createSseParser((ev, d) => seen.push(ev))
        for (const ch of stream) p.push(ch)
        console.log(JSON.stringify({seen}))
    """))
    assert out["seen"] == ["step_start", "finishing", "done"], out["seen"]


def test_没有事件名的data不当成上一条的():
    """事件名跨分片保留，但**不能跨帧粘着** —— 用完就得清，否则一条裸 data
    会被当成上一个事件重复投递一次（done 投两次 = 结果被覆盖两遍）。"""
    out = _run_node(_harness("""
        const seen = []
        const p = createSseParser((ev, d) => seen.push(ev))
        p.push(`event: done\\ndata: {"status":"passed"}\\n\\n`)
        p.push(`data: {"status":"failed"}\\n\\n`)      // 裸 data，没有 event 行
        console.log(JSON.stringify({seen}))
    """))
    assert out["seen"] == ["done"], f"裸 data 被当成了 done 重复投递：{out['seen']}"


def test_流断了没收到done时前端要收掉转圈():
    """第二层保险。解析修好了，但网络真断、后端真崩的时候还是收不到 done ——
    那时候必须自己把转圈收掉。永远转圈 = 用户看到的还是「一直这样」，
    而且比报错更糟：他不知道该重跑还是该等。"""
    src = CASE_JSX.read_text(encoding="utf-8")
    assert "sawDone" in src, "读循环没有「收没收到 done」这个判断"
    seg = src[src.index("function processChunk"):]
    seg = seg[:seg.index("processChunk()\n    }).catch") + 200] if "processChunk()\n    }).catch" in seg else seg[:2000]
    assert "if (!sawDone)" in seg, "流结束时没有对「没收到 done」做兜底"
    assert "setDebugRunning(false)" in seg, "兜底分支没有把转圈状态收掉"


def test_读循环里不许再声明事件名():
    """钉住根因写法本身。"""
    src = CASE_JSX.read_text(encoding="utf-8")
    body = src[src.index("function processChunk"):]
    body = body[:body.index("processChunk()")]
    assert "currentEvent" not in body, \
        "processChunk 里又出现了 currentEvent —— 事件名一旦回到读循环内部就会被分片清零"
