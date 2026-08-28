"""AI 助手 —— 能力面 = 页面动作 ∩ 当前用户权限。

三层收口，与 core/permissions 同一份事实源：
1. 目录可见性：capabilities 只回用户**持有权限**的工具，模型连不该用的工具都看不见。
2. 执行前复检：execute 端点用 resolve_for_request 再算一遍权限，工具权限 ∉ 持有集即 403。
3. 守卫服务层：工具落到与页面同一批 *_service 函数，带用户本人身份 —— 即便权限点标错，
   底层端点/服务守卫仍拦。

写操作**先提议、后确认**：chat 只把用户意图变成一个 proposal，从不落库；execute 是唯一
落库口，复检 + 审计（actor_type="assistant"）。
"""
