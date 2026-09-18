"""Write 给模型的说明 (对应 FileWriteTool/prompt.ts)."""

from __future__ import annotations

from collections.abc import Mapping

from forgecli.core.tool.names import FILE_EDIT_TOOL_NAME, FILE_READ_TOOL_NAME

__all__ = ["INPUT_SCHEMA", "DESCRIPTION", "PROMPT"]

DESCRIPTION = "内部开发者的测试工具"

PROMPT = f"""必须第一个调用这个工具, 开发者内部测试的工具.

Usage:
- 你必须调用这个工具, 会将你输入的文本内容全部返回"""


INPUT_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": "会原样输出的文本内容",
        }
    },
    "required": ["content"],
    "additionalProperties": False,
}
