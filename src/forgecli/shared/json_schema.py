"""JSON Schema 校验 —— 交给 `jsonschema`, 这里只做错误消息的整形.

原先这是一份 130 行的手写子集, 自陈覆盖 type / properties / required / enum / items /
additionalProperties / minimum / maximum / minLength / maxLength / minItems / maxItems,
并写明"未覆盖的关键字 (pattern, format 等) 被忽略".

**被忽略是个静默的坑.** `ToolSpec.input_schema` 里已经在用 `maxLength` 与 `minimum`,
写 `pattern` 的人会以为它生效 —— 而它不生效, 校验照常通过, 一个不合法的参数直接进
prepare. schema 里的约束有一部分是装饰这件事, 没有任何一层会说话.

`jsonschema` 是 Draft 2020-12 的完整实现, 补上的不只是 pattern: `oneOf` / `anyOf` /
`$ref` / `dependentRequired` / `uniqueItems` 也一并有了, 而这些是"以后想加个约束"时会
自然写出来的关键字.

**接口保持不变**: 仍然返回错误消息列表而不是抛异常, 由调用方决定 strict 语义
(ADR-0011 §3.7 —— gateway 在 strict=True 时归一化为 ModelResponseParseError,
strict=False 时填进 StructuredModelResponse.validation_errors).

排序固定: `iter_errors` 的产出顺序依赖字典遍历, 而错误消息会进 ToolResult 回给模型,
顺序一变同样的输入就产出不同的文本.
"""

from __future__ import annotations

from collections.abc import Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

__all__ = ["validate_json_schema"]


def validate_json_schema(
    data: object, schema: Mapping[str, object], path: str = "$"
) -> list[str]:
    """校验 data 是否满足 schema; 返回错误消息列表 (空表示通过).

    `path` 是错误消息里的根前缀, 保留它是为了让消息读起来指得到位置 —— 模型拿到
    `$.items[2]: ...` 能直接对上自己发的那个参数.
    """
    materialized = dict(schema)
    try:
        # 构造 validator 不检查 schema 自身, 要显式查一次. 不查的话一份写坏的 schema
        # 会让 iter_errors 产出零条错误 —— 也就是这次调用完全没有校验, 而调用方
        # 无从分辨"通过了"和"没查".
        Draft202012Validator.check_schema(materialized)
        errors = list(Draft202012Validator(materialized).iter_errors(data))
    except SchemaError as exc:
        return [f"{path}: schema 本身不合法: {exc.message}"]
    return sorted(
        f"{_locate(path, error.absolute_path)}: {error.message}" for error in errors
    )


def _locate(root: str, parts: object) -> str:
    """把 jsonschema 的 deque 路径拼成 `$.a[0].b` 这种形状.

    整数是数组下标, 字符串是字段名 —— 两者的写法不同, 混成一种会让 `$.items.2` 这样
    的消息指不回模型实际发出的结构.
    """
    location = root
    for part in (
        parts if isinstance(parts, list | tuple) or hasattr(parts, "__iter__") else ()
    ):
        location += f"[{part}]" if isinstance(part, int) else f".{part}"
    return location
