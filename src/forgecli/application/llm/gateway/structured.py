"""结构化输出的 schema 处理 (ADR-0011 §3.7).

三件纯函数式的事: 把 schema 写进提示词, 把回答剥成 JSON, 拿 schema 校验它。没有网络
调用, 没有缓存, 也不认识重试 —— 那些是网关的编排。

它们服务两条通道: 模型原生支持结构化输出时只用 `parse`; 不支持时先 `with_instruction`
把 schema 说给模型听, 再 `parse` 回来 (受控解析降级)。两条通道的**校验必须是同一份**,
否则"原生通过而降级不通过"这类差异会被读成模型能力问题。
"""

from __future__ import annotations

import json
from dataclasses import replace

from forgecli.application.prompt.template_renderer import render_notice
from forgecli.domain.model.request import ModelRequest, StructuredModelRequest
from forgecli.shared.json_schema import validate_json_schema

__all__ = ["parse", "strip_code_fence", "with_instruction"]


def with_instruction(
    base: ModelRequest, request: StructuredModelRequest
) -> ModelRequest:
    """把 schema 作为一段指令接在 system prompt 之后 (降级通道)。

    接在后面而不是替换: 前面那段是身份与安全边界, 换掉它等于为了拿一段 JSON 把模型的
    约束一起摘了。
    """
    schema_text = json.dumps(dict(request.schema), ensure_ascii=False, sort_keys=True)
    instruction = render_notice(
        "prompt.schema_instruction", name=request.schema_name, schema=schema_text
    )
    system_prompt = (
        f"{base.system_prompt}\n\n{instruction}" if base.system_prompt else instruction
    )
    return replace(base, system_prompt=system_prompt)


def parse(
    content: str, request: StructuredModelRequest
) -> tuple[object | None, list[str]]:
    """解析并校验一段回答, 返回 (数据, 错误列表)。

    不抛异常: 校验失败在降级通道里是要重试的正常情况, 而 `strict` 之下要不要升级成
    异常由网关决定 —— 这里只如实报告哪儿不合规。
    """
    try:
        data: object = json.loads(strip_code_fence(content))
    except json.JSONDecodeError as exc:
        return None, [f"输出不是合法 JSON: {exc}"]
    return data, validate_json_schema(data, request.schema)


def strip_code_fence(content: str) -> str:
    """容错剥离 ```json 代码块围栏（受控降级下模型偶发包裹）。"""
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
