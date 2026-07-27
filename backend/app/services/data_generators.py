"""场景变量数据生成器 —— 模板里 {{$fn}} / {{$fn:arg}} 的展开目录 + 引擎。

对标 apifox「数据生成器」：一条场景变量可以「部分固定 + 部分随机」，例如
    svc-{{$string:6}}-{{$city}}   →   svc-a1b2c3-上海
字面字符原样保留，{{$fn}} 在**执行期**用 Faker/随机展开。

为什么用 {{...}} 而不是 ${...}：接口步骤插值用的是 ${var}（api_test_runner._resolve_variables）。
场景变量先在这里展开成一个具体串，再交给步骤插值——两套语法分层，互不打架。

GENERATORS 目录同时给前端「快速插入」面板用（/generators 接口）与后端 expand_template 用，
一份定义，避免前后端漂移。
"""
from __future__ import annotations

import random
import re
import secrets
import string
import uuid as _uuid

from faker import Faker

# 名字/城市/地址/手机走中文，邮箱/URL/IP 仍是拉丁——zh_CN 都能出
_faker = Faker("zh_CN")

_TOKEN_RE = re.compile(r"\{\{\s*\$(\w+)(?::([^}]*))?\s*\}\}")


def _rand_alnum(n: int) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=max(1, n)))


def _gen_int(arg: str | None) -> str:
    lo, hi = 0, 9999
    if arg and "-" in arg:
        try:
            a, b = arg.split("-", 1)
            lo, hi = int(a), int(b)
        except ValueError:
            pass
    if lo > hi:
        lo, hi = hi, lo
    return str(random.randint(lo, hi))


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


# token -> 生成函数(arg, run_id)。arg 是 {{$fn:arg}} 里的 arg（无则 None）。
_GEN_FUNCS = {
    # 字符串 / UUID
    "string": lambda arg, rid: _rand_alnum(int(arg) if arg and arg.isdigit() else 8),
    "uuid": lambda arg, rid: _uuid.uuid4().hex,
    "rand": lambda arg, rid: secrets.token_hex(max(1, (int(arg) if arg and arg.isdigit() else 4)) // 2 + 1)[: (int(arg) if arg and arg.isdigit() else 4)],
    "runId": lambda arg, rid: rid,
    # 数值
    "int": lambda arg, rid: _gen_int(arg),
    "float": lambda arg, rid: str(round(random.uniform(0, 10000), 2)),
    # 单词 / 句子
    "word": lambda arg, rid: _faker.word(),
    "sentence": lambda arg, rid: _one_line(_faker.sentence()),
    # 姓名 / 个人资料
    "name": lambda arg, rid: _faker.name(),
    "firstName": lambda arg, rid: _faker.first_name(),
    "lastName": lambda arg, rid: _faker.last_name(),
    "company": lambda arg, rid: _faker.company(),
    "jobTitle": lambda arg, rid: _faker.job(),
    # 电话 / 手机
    "phone": lambda arg, rid: _faker.phone_number(),
    # 邮箱 / 网址 / IP / 域名 / 用户名
    "email": lambda arg, rid: _faker.email(),
    "url": lambda arg, rid: _faker.url(),
    "domain": lambda arg, rid: _faker.domain_name(),
    "ipv4": lambda arg, rid: _faker.ipv4(),
    "username": lambda arg, rid: _faker.user_name(),
    # 地址 / 区域
    "city": lambda arg, rid: _faker.city(),
    "address": lambda arg, rid: _one_line(_faker.address()),
    "province": lambda arg, rid: _faker.province() if hasattr(_faker, "province") else _faker.city(),
    # 日期 / 时间
    "date": lambda arg, rid: _faker.date(),
    "datetime": lambda arg, rid: _faker.iso8601(),
    "timestamp": lambda arg, rid: str(int(_faker.unix_time())),
}


def expand_template(tpl: str, run_id: str | None = None) -> str:
    """把模板里的 {{$fn}} / {{$fn:arg}} 展开成具体值。未知 token 原样保留。"""
    if not tpl or "{{" not in tpl:
        return tpl or ""
    rid = run_id or _uuid.uuid4().hex[:8]

    def _sub(m: re.Match) -> str:
        fn, arg = m.group(1), m.group(2)
        func = _GEN_FUNCS.get(fn)
        if not func:
            return m.group(0)  # 未知 token 不动，方便排错
        try:
            return str(func(arg, rid))
        except Exception:
            return m.group(0)

    return _TOKEN_RE.sub(_sub, tpl)


# 前端「快速插入」面板目录：分类 -> 若干生成器。token 是插入到模板里的文本。
GENERATORS = [
    {"category": "字符串 / UUID", "items": [
        {"token": "{{$string:8}}", "label": "随机字符串", "desc": "字母数字，:N 指定长度", "hasArg": True},
        {"token": "{{$uuid}}", "label": "UUID", "desc": "32 位十六进制", "hasArg": False},
        {"token": "{{$rand:4}}", "label": "短随机串", "desc": ":N 指定长度", "hasArg": True},
        {"token": "{{$runId}}", "label": "本次运行ID", "desc": "同一次执行内一致，可追溯本脚本造的数据", "hasArg": False},
    ]},
    {"category": "数值", "items": [
        {"token": "{{$int:1-9999}}", "label": "随机整数", "desc": ":min-max 指定区间", "hasArg": True},
        {"token": "{{$float}}", "label": "随机小数", "desc": "0~10000 两位小数", "hasArg": False},
    ]},
    {"category": "单词 / 句子", "items": [
        {"token": "{{$word}}", "label": "单词", "desc": "", "hasArg": False},
        {"token": "{{$sentence}}", "label": "句子", "desc": "", "hasArg": False},
    ]},
    {"category": "姓名 / 个人资料", "items": [
        {"token": "{{$name}}", "label": "姓名", "desc": "中文姓名", "hasArg": False},
        {"token": "{{$firstName}}", "label": "名", "desc": "", "hasArg": False},
        {"token": "{{$lastName}}", "label": "姓", "desc": "", "hasArg": False},
        {"token": "{{$company}}", "label": "公司名", "desc": "", "hasArg": False},
        {"token": "{{$jobTitle}}", "label": "职位", "desc": "", "hasArg": False},
    ]},
    {"category": "电话 / 手机", "items": [
        {"token": "{{$phone}}", "label": "手机号", "desc": "中国手机号", "hasArg": False},
    ]},
    {"category": "邮箱 / 网址 / IP", "items": [
        {"token": "{{$email}}", "label": "邮箱", "desc": "", "hasArg": False},
        {"token": "{{$url}}", "label": "网址", "desc": "", "hasArg": False},
        {"token": "{{$domain}}", "label": "域名", "desc": "", "hasArg": False},
        {"token": "{{$ipv4}}", "label": "IPv4", "desc": "", "hasArg": False},
        {"token": "{{$username}}", "label": "用户名", "desc": "", "hasArg": False},
    ]},
    {"category": "地址 / 区域", "items": [
        {"token": "{{$city}}", "label": "城市", "desc": "", "hasArg": False},
        {"token": "{{$address}}", "label": "详细地址", "desc": "", "hasArg": False},
        {"token": "{{$province}}", "label": "省份", "desc": "", "hasArg": False},
    ]},
    {"category": "日期 / 时间", "items": [
        {"token": "{{$date}}", "label": "日期", "desc": "YYYY-MM-DD", "hasArg": False},
        {"token": "{{$datetime}}", "label": "日期时间", "desc": "ISO8601", "hasArg": False},
        {"token": "{{$timestamp}}", "label": "时间戳", "desc": "秒级", "hasArg": False},
    ]},
]
