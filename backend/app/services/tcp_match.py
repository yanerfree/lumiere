"""TCP Mock 匹配规则：校验 + 匹配（纯函数，便于单测）"""
from __future__ import annotations

import re

MATCH_MODES = ("exact", "contains", "regex", "hex", "any")


def normalize_hex(pattern: str) -> str:
    """容忍 0x 前缀、空格、逗号、冒号；返回小写连续十六进制串"""
    s = re.sub(r"0x", "", pattern or "", flags=re.I)
    return re.sub(r"[\s,:\-]", "", s).lower()


def validate_match(mode: str, pattern: str) -> str | None:
    """返回错误文案；合法返回 None"""
    if mode not in MATCH_MODES:
        return f"不支持的匹配模式：{mode}（可选：精确 / 包含 / 正则 / 十六进制 / 任意内容）"
    if mode == "any":
        return None
    if not (pattern or "").strip():
        return "匹配规则不能为空（要匹配所有内容请选「任意内容」）"
    if mode == "regex":
        try:
            re.compile(pattern)
        except re.error as e:
            return f"正则表达式有误：{e}"
    elif mode == "hex":
        h = normalize_hex(pattern)
        if not h or len(h) % 2 or not re.fullmatch(r"[0-9a-f]+", h):
            return "十六进制规则有误：只能含 0-9 A-F，且字节数要完整（如 FF FE 00 01）"
    return None


def is_match(mode: str, pattern: str, data: bytes) -> bool:
    text = data.decode("utf-8", errors="replace").strip()
    pattern = pattern or ""
    if mode == "any":
        return True
    if mode == "exact":
        return text == pattern.strip()
    if mode == "contains":
        return pattern.strip() != "" and pattern.strip() in text
    if mode == "hex":
        return data.hex() == normalize_hex(pattern)
    if mode == "regex":
        try:
            return re.search(pattern, text) is not None
        except re.error:
            return False
    return False
