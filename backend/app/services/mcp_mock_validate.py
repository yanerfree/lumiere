"""MCP Mock 的入参定义 → JSON Schema，以及三档松紧的校验。

两件事分开看：

  · **JSON Schema** 是给对面客户端看的（`tools/list` 里的 inputSchema）。
    类型、必填、枚举、范围都写在里面 —— 这是原来那版最缺的东西：
    老实现把每个参数都 `exec` 成 `str = ""`，于是页面上标的 integer/array
    **纯属装饰**，客户端拿到的永远是一串可选字符串。

  · **校验** 是我们自己做的。fastmcp 只对 `FunctionTool`（从函数签名推出来的那种）
    自动校验，我们用的是自定义 Tool 子类，它**不会**替我们校验 —— 这正好，
    否则「宽松」「不校验」两档根本做不出来。
"""
from __future__ import annotations

from typing import Any

# 页面上能选的参数类型。object/array 收 JSON。
PARAM_TYPES = ("string", "integer", "number", "boolean", "array", "object")

VALIDATE_MODES = ("strict", "loose", "off")

_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}

_ZERO: dict[str, Any] = {
    "string": "", "integer": 0, "number": 0, "boolean": False, "array": [], "object": {},
}


def normalize_param(raw: Any) -> dict | None:
    """把一条参数定义洗干净。洗不出名字的丢掉（返回 None）。

    兼容老格式：老的 `params` 是 `{"branch_id": "string"}` 这种字典，
    调用方会先摊成 `{"name": k, "type": v}` 再送进来。
    """
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    ptype = raw.get("type")
    if ptype not in PARAM_TYPES:
        ptype = "string"
    out: dict[str, Any] = {
        "name": name.strip(),
        "type": ptype,
        "required": bool(raw.get("required", False)),
        "description": str(raw.get("description") or ""),
    }
    for key in ("default", "enum", "minimum", "maximum", "minLength", "maxLength", "itemsType"):
        val = raw.get(key)
        if val is not None and val != "":
            out[key] = val
    if isinstance(out.get("enum"), str):
        # 页面上枚举是一行逗号分隔填进来的
        out["enum"] = [s.strip() for s in out["enum"].split(",") if s.strip()]
    if not isinstance(out.get("enum", []), list):
        out.pop("enum", None)
    return out


def normalize_params(raw: Any) -> list[dict]:
    """列表 / 老字典 / None 都收，统一出列表。"""
    if raw is None:
        return []
    items: list[Any]
    if isinstance(raw, dict):
        # 老格式 {"branch_id": "string", ...}
        items = [{"name": k, "type": v if isinstance(v, str) else "string"} for k, v in raw.items()]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in items:
        p = normalize_param(item)
        if p is None or p["name"] in seen:
            continue
        seen.add(p["name"])
        out.append(p)
    return out


def build_schema(params: Any, validate_mode: str = "strict") -> dict:
    """参数定义 → JSON Schema（草案 2020-12，MCP 客户端认的那种）。"""
    props: dict[str, dict] = {}
    required: list[str] = []
    for p in normalize_params(params):
        node: dict[str, Any] = {"type": p["type"]}
        if p.get("description"):
            node["description"] = p["description"]
        if p.get("enum"):
            node["enum"] = p["enum"]
        if p.get("default") is not None:
            node["default"] = p["default"]
        for jk, pk in (("minimum", "minimum"), ("maximum", "maximum"),
                       ("minLength", "minLength"), ("maxLength", "maxLength")):
            if p.get(pk) is not None:
                node[jk] = p[pk]
        if p["type"] == "array" and p.get("itemsType") in PARAM_TYPES:
            node["items"] = {"type": p["itemsType"]}
        props[p["name"]] = node
        if p["required"]:
            required.append(p["name"])
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    # 只有严格档才封死多余参数。宽松/不校验留着 additionalProperties 不写，
    # 等于默认允许 —— 让客户端自己也能看出这个服务收不收多余字段。
    if validate_mode == "strict":
        schema["additionalProperties"] = False
    return schema


def _coerce(value: Any, ptype: str) -> tuple[bool, Any]:
    """宽松档的类型转换。转得动返回 (True, 新值)，转不动返回 (False, 原值)。"""
    if isinstance(value, _PY_TYPES[ptype]) and not (ptype != "boolean" and isinstance(value, bool)):
        return True, value
    try:
        if ptype == "string":
            return True, value if isinstance(value, str) else str(value)
        if ptype == "integer":
            return True, int(value)
        if ptype == "number":
            return True, float(value)
        if ptype == "boolean":
            if isinstance(value, str):
                return True, value.strip().lower() in ("1", "true", "yes", "y", "on")
            return True, bool(value)
        if ptype == "array":
            import json
            if isinstance(value, str):
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return True, parsed
            return False, value
        if ptype == "object":
            import json
            if isinstance(value, str):
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return True, parsed
            return False, value
    except Exception:  # noqa: BLE001
        return False, value
    return False, value


def validate_arguments(params: Any, arguments: dict | None, mode: str) -> tuple[list[str], dict]:
    """按松紧档校验实参。返回 (错误列表, 收拾干净的实参)。

    · strict —— 缺必填 / 类型不符 / 不在枚举 / 越界 / **多传没声明的参数**，都算错。
    · loose  —— 类型能转就转（"3" → 3）；缺必填补零值；枚举和范围只放过不拦；
                多传的原样留着。**只有转不动的类型才算错。**
    · off    —— 一个字都不校验，原样收下。

    ⚠ 错误信息里一律带上参数名。「参数校验失败」这种不带名字的提示，
      对面客户端拿到之后只能挨个试 —— 那比不校验还糟。
    """
    args = dict(arguments or {})
    if mode == "off":
        return [], args

    defs = normalize_params(params)
    errors: list[str] = []
    known = {p["name"] for p in defs}

    if mode == "strict":
        for extra in sorted(set(args) - known):
            errors.append(f"参数 {extra} 不在这个工具的参数表里（严格校验不收多余参数）")

    for p in defs:
        name, ptype = p["name"], p["type"]
        if name not in args or args[name] is None:
            if p["required"]:
                if mode == "strict":
                    errors.append(f"缺少必填参数 {name}")
                else:
                    args[name] = p.get("default", _ZERO[ptype])
            elif p.get("default") is not None:
                args.setdefault(name, p["default"])
            continue

        value = args[name]
        ok_type = isinstance(value, _PY_TYPES[ptype])
        # Python 里 True 也是 int，别让布尔混进 integer/number
        if ok_type and ptype in ("integer", "number") and isinstance(value, bool):
            ok_type = False
        if not ok_type:
            if mode == "strict":
                errors.append(f"参数 {name} 应该是 {ptype}，收到的是 {type(value).__name__}")
                continue
            converted, value = _coerce(value, ptype)
            if not converted:
                errors.append(f"参数 {name} 是 {type(value).__name__}，转不成 {ptype}")
                continue
            args[name] = value

        if mode != "strict":
            continue
        if p.get("enum") and value not in p["enum"]:
            errors.append(f"参数 {name} 只能是 {'/'.join(map(str, p['enum']))} 之一，收到 {value}")
        if p.get("minimum") is not None and isinstance(value, (int, float)) and value < p["minimum"]:
            errors.append(f"参数 {name} 不能小于 {p['minimum']}，收到 {value}")
        if p.get("maximum") is not None and isinstance(value, (int, float)) and value > p["maximum"]:
            errors.append(f"参数 {name} 不能大于 {p['maximum']}，收到 {value}")
        if p.get("minLength") is not None and isinstance(value, str) and len(value) < p["minLength"]:
            errors.append(f"参数 {name} 至少 {p['minLength']} 个字符，收到 {len(value)} 个")
        if p.get("maxLength") is not None and isinstance(value, str) and len(value) > p["maxLength"]:
            errors.append(f"参数 {name} 最多 {p['maxLength']} 个字符，收到 {len(value)} 个")

    return errors, args
