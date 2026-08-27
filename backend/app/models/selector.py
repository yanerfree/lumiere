import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class ProjectSelector(Base):
    """项目级选择器登记表 —— UI 脚本别再把选择器写成字面量。

    **为什么要有这张表。** 脚本是一条一条独立存在 `scripts` 表里的（每条只 import
    `playwright.sync_api`），选择器就散在各条正文里当字符串。实测 18 条 UI 脚本里
    125 处 `page.locator(...)`，用到 testid 的只有 4 条，其余大量是**样式类**：
    `.card.card-pad` / `button.btn.sm.primary` / `span.chip.sm` / `.ant-modal`。
    样式类是给人看好看的，改版随手就变（antd 从 v5 到 v6 类名整批换过），
    而前端改一个类名，这边要**逐条打开改 18 遍**——改漏了当场不报错，
    等某次回归红了才发现，那时已经分不清是产品坏了还是脚本过期了。

    登记之后：脚本里写 `${SEL:用例列表.新建按钮}`，前端改名只改这张表一行。
    机制和文案词典（`project_i18n_messages` + `${键|中文}`）是同一套 ——
    执行前在源码文本上替换，本地渲染出来的文件也是同一份。

    **kind 是稳定性等级，不是分类标签。** testid/id/role 语言无关且是给测试用的，
    改动会被前端当成契约变更；style 是最脆的一档，登记时会警告。

    ── status='gap' 这一档最要紧 ──

    抄自 uag-qa 的一条纪律（`ui/support/selectors.ts` 头部）:
    **缺 testid 时正确做法是给前端补 testid 并提 MR,不是在这里写脆弱选择器。**
    他们真这么干了：先出缺口清单 `docs/qa/ui-testid-gap.md`，再自己往被测产品前端
    提 MR（!56 / !57，后者补了 8 个 testid + 2 个语义属性），MR 合了**回来把 UI 用例
    写掉**，清单上的 ⬜ 转 ✅。

    没有 gap 这一档的话，「前端没给抓手」只会变成一句口头的"以后再说"，
    然后永远没有以后 —— 因为它不在任何队列里，不出现在任何数字上，
    **不写这条用例是零成本的**。gap 行让它留痕：进 `lum_next_duty` 的待办、
    被 blocked_cases 指名道姓地记着哪几条用例卡在这儿。
    补齐之后（selector 填上、status 转 active）那几条用例又会自己冒出来提醒
    「回来写 UI」，直到真的推了脚本才消失 —— 自清，不用谁记得手动关。

    key:           引用名，脚本里写 `${SEL:这个名字}`。建议 `模块.元素` 两段式
    selector:      真正的选择器；status='gap' 时可以为空（还没有抓手）
    kind:          testid / id / role / semantic / structure / text / style
    status:        active（能用）/ gap（缺抓手，等前端补）
    gap_note:      缺什么、等谁补、MR 提了没有 —— status='gap' 时必填
    blocked_cases: 卡在这条缺口上的用例编号 ["TC-XXX-00012", ...]
    """
    __tablename__ = "project_selectors"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_selector_project_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="style",
                                      server_default="style")
    module: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active",
                                        server_default="active")
    gap_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_cases: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual",
                                        server_default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
