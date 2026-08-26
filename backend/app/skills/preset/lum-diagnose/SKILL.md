---
name: lum-diagnose
description: 分析测试失败原因，3 分类仲裁 + 可行动的修复建议
version: 1
tools:
  - lum_get_failed_scenarios
  - lum_get_case
  - lum_list_cases
---

# 失败诊断 Skill

## Step 1 — 收集失败信息

1. 调用 `lum_get_failed_scenarios` 获取报告中的失败用例
2. 收集每个失败用例的：步骤、前置条件、预期结果、实际错误

## Step 2 — 三分类仲裁

对每个失败用例判断根因分类：

### script_bug（脚本问题）
- 脚本本身有 Bug（定位器错误、断言写错、数据准备不足）
- 输出：具体哪里错了 + 修复代码片段

### system_bug（系统 Bug）
- 被测系统的真实缺陷（接口返回异常、逻辑错误）
- 输出：Bug 报告模板（标题、复现步骤、实际/预期、严重程度）

### env_issue（环境问题）
- 测试环境配置问题（服务未启动、数据库连接失败、网络超时）
- 输出：环境检查清单 + 修正建议

## Step 3 — 输出诊断报告

输出 JSON：
```json
{
  "diagnoses": [
    {
      "caseTitle": "用例标题",
      "verdict": "script_bug | system_bug | env_issue",
      "confidence": 0.85,
      "summary": "一句话概述",
      "evidence": ["证据1", "证据2"],
      "fixSuggestion": "具体修复建议"
    }
  ],
  "summary": {
    "total": 5,
    "scriptBug": 2,
    "systemBug": 2,
    "envIssue": 1
  }
}
```
