# 接口场景自测

改「用例 → 接口」这块之后跑这两个。**要先起后端 8756 和前端 5173。**

```bash
bash backend/scripts/selftest/selftest_api_scenario.sh   # 接口场景整模块 16 项
python3 backend/scripts/selftest/selftest_neighbors.py   # 共用层出口 14 项（在仓库根跑）
python3 backend/scripts/selftest/scan_overflow.py        # 全站横向溢出（28 页 × 2 视口）

LUMIERE_PROJ=<项目UUID> \
python3 backend/scripts/selftest/qa_dropped_no_anchor.py # QA 评审抽屉「丢了几条」三档渲染
```

`qa_dropped_no_anchor.py` 验的是**「没查」「查过没丢」「丢了 N 条」三档不许合并**。
它拦下 `reviews` 响应把 `droppedNoAnchor` 换成三档喂给真页面 —— 不造数据跑真评审，
是因为**「这个键不存在」的存量结论在真库里造不出来**：新代码跑过一次就一定带上它。
这脚本自己假绿过一次，值得抄走：响应外面套着 `{"data": {"reviews": [...]}}`，
拦截函数在最外层找 `reviews`，**一条都没改到**，三档于是全渲染成「没查」那一档 ——
**「注入没生效」和「第一档渲染正确」在页面上长得一模一样**。所以它现在递归认对象
（带 `domain` + `result` 的就是一条评审），并把「拦到几次 / 改了几条」打进每行输出：
改了 0 条就不可能是「渲染对了」。

`scan_overflow.py` 是**布局类 bug 的兜底**：找"把父容器撑破"的元素。
这类 bug 源码里长得跟正常代码一模一样（根因是 CSS 计算结果，典型是 flex 子项
`min-width:auto` 不肯缩），**grep 找不出来，只能渲染出来量**。
改任何布局、加任何可能很长的文案（环境名带 URL、用例标题、文件路径）之后跑一遍。

为什么要有它们：接口场景是**共用资产** —— 用例详情、计划执行、测试报告、分支复制
四处都在消费它。2026-08-15 下线「接口测试」模块时，只验改动点没发现分支复制已经被
改崩（属性不存在 + 撞非空约束），是这两个脚本跑出来的。详见
`docs/cc-platform-loop-spec.md` §11.8。

两条纪律，写在这儿免得下次又踩：

- **断言到内容，不看状态码**。改完步骤要读回来比对，不能只看 PUT 返回 200。
- **断言写错的表现和功能坏了一模一样**，方向却相反（假绿 / 假红）。红了先回去看
  断言对不对，再下"功能坏了"的结论 —— 这两个脚本自己就各错过一次。
