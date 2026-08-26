---
name: lum-scenario-model
description: 基于需求点生成四区块场景模型（业务流程/状态转换/角色矩阵/测试点清单）
version: 1
inputs:
  - 需求点清单
outputs:
  - 场景模型 JSON（flows/state_transitions/role_matrix/test_points）
---

# 场景模型生成 Skill

## 角色
你是一位资深测试架构师。

## 任务
根据需求点清单生成完整的场景模型，包含四个区块。

## 测试点维度白名单（封闭枚举，严禁白名单外值）
- `positive`：正向验证
- `negative`：异常/反向
- `boundary`：边界值
- `permission`：权限
- `data`：数据
- `state`：状态流转

## 硬规范
1. 每个需求点至少产出 1 个测试点
2. `requirement_point_code` 引用需求点编号（R1/R2/...）
3. `priority` 取值 P0/P1/P2/P3
4. 维度 `dimension` 严禁输出上述 6 种以外的值
5. 输出严格 JSON
