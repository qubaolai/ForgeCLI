"""根据模型调用用途解析出 llm 调用方式
对话&任务处理: 流式
上下文压缩: 非流式
内部摘要: 非流式
分类、校验、结构化提取: 结构化
"""
from __future__ import annotations

from enum import Enum

from forgecli.domain.model.origin import RequestOrigin


class ModelTransportMode(Enum):
    """llm模型调用方式"""
    STREAM = "stream" # 流式
    COMPLETE = "complete" # 非流式
    COMPLETE_STRUCTURED = "complete_structured" # 结构化


class ModelTransportPolicy:
    def resolve(self, origin: RequestOrigin) -> ModelTransportMode:
        match origin:
            case (
                RequestOrigin.TITLE
                | RequestOrigin.COMPACT 
                | RequestOrigin.SUMMARY 
                | RequestOrigin.STRUCTURED_CLASSIFICATION
            ):
                return ModelTransportMode.COMPLETE
            case _:
                return ModelTransportMode.STREAM
