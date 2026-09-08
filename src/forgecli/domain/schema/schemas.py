from collections.abc import Mapping
from dataclasses import dataclass


from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class SchemaDefinition:
    """Schema 定义"""
    schema: Mapping[str, object]
    description: str = ""
    name: str = ""


class StructuredSchema:
    """所有 Schema 的命名空间"""
    
    # 直接在类上定义属性
    TITLE_CREATE = SchemaDefinition(
        name="TITLE_CREATE",
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"}
            },
            "required": ["title"],
            "additionalProperties": False
        },
        description="创建标题的 schema"
    )
    
    CONTEXT_SUMMARY = SchemaDefinition(
        name="CONTEXT_SUMMARY",
        schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
            },
            "required": ["summary"],
            "additionalProperties": False
        },
        description="上下文摘要的 schema"
    )
    