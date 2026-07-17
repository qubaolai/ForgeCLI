"""结构化输出的最小 JSON Schema 校验（ADR-0011 §3.7）。

标准库实现的 JSON Schema 子集，覆盖内部结构化任务（plan update、risk
classification、title、summary）常用的约束：

    type（object/array/string/number/integer/boolean/null）、properties、
    required、enum、items、additionalProperties=false。

未覆盖的关键字（pattern、format、min/max 等）被忽略——校验做「必要而非充分」
判定：违反已支持关键字一定报错，未支持关键字不误报。返回错误列表而非抛异常，
由 gateway 决定 strict 语义（strict=True 归一化为 ModelResponseParseError，
strict=False 填入 StructuredModelResponse.validation_errors）。
"""

from __future__ import annotations

from collections.abc import Mapping

_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def validate_json_schema(
    data: object, schema: Mapping[str, object], path: str = "$"
) -> list[str]:
    """校验 data 是否满足 schema 子集；返回错误消息列表（空表示通过）。"""
    errors: list[str] = []

    expected_type = schema.get("type")
    if isinstance(expected_type, str) and not _check_type(data, expected_type):
        errors.append(f"{path}: 期望类型 {expected_type}，实际 {type(data).__name__}")
        return errors  # 类型不符时后续结构性检查无意义

    enum = schema.get("enum")
    if isinstance(enum, list) and data not in enum:
        errors.append(f"{path}: 取值不在 enum 允许集合内: {data!r}")

    if isinstance(data, dict):
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if name not in data:
                    errors.append(f"{path}: 缺少必填字段 {name!r}")
        for name, value in data.items():
            sub_schema = properties.get(name)
            if isinstance(sub_schema, Mapping):
                errors.extend(
                    validate_json_schema(value, sub_schema, path=f"{path}.{name}")
                )
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: 不允许额外字段 {name!r}")

    if isinstance(data, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(data):
                errors.extend(
                    validate_json_schema(item, items, path=f"{path}[{index}]")
                )

    return errors


def _check_type(data: object, expected: str) -> bool:
    if expected == "number":
        return not isinstance(data, bool) and isinstance(data, int | float)
    if expected == "integer":
        return not isinstance(data, bool) and isinstance(data, int)
    py_type = _TYPE_CHECKS.get(expected)
    if py_type is None:
        return True  # 未知类型关键字：不误报
    if expected in {"object", "array", "string"} and isinstance(data, bool):
        return False
    return isinstance(data, py_type)
