"""统一调用错误类型（ADR-0011 §12）。

所有 provider 抛出的错误都由 gateway 归一化成下面这组类型。它们继承
shared.errors.ForgeError，但与 config 层的 ConfigError / UnknownProvider /
InvalidModelRef 分属不同分支，命名不混用：config 层错误描述「配置/写入非法」，
这里描述「一次模型调用的运行时失败」。

每个错误都带安全上下文 provider / model / request_id，绝不携带凭证或完整
prompt/response；message 也只放安全摘要，可直接展示或入日志（脱敏由上层再保证）。
"""

from __future__ import annotations

from forgecli.shared.errors import ForgeError


class ModelGatewayError(ForgeError):
    """LLM 调用网关错误根基类。携带安全上下文，不含 secret。"""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.request_id = request_id


class ModelUnavailableError(ModelGatewayError):
    """供应商不可用 / 连接失败，且无可用候选。"""


class ModelAuthError(ModelGatewayError):
    """认证失败（key 无效 / 过期 / 权限不足），所有候选凭证均失败。"""


class ModelRateLimitError(ModelGatewayError):
    """触发限流 / 配额（429）。retry_after 为供应商建议的冷却秒数（若有）。"""

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider, model=model, request_id=request_id)
        self.retry_after = retry_after


class ModelTimeoutError(ModelGatewayError):
    """调用超时；可按策略有限重试，仍失败则抛出本错误。"""


class ModelContextOverflowError(ModelGatewayError):
    """输入超出模型上下文窗口；优先触发 compact，仍超限则抛出。"""


class ModelBadRequestError(ModelGatewayError):
    """请求构造非法 / provider schema 不兼容（多为 bug），不应 fallback 掩盖。"""


class ModelProviderInternalError(ModelGatewayError):
    """供应商内部错误（5xx 等）。"""


class ModelResponseParseError(ModelGatewayError):
    """结构化输出 schema 校验失败 / 响应无法解析。"""


class MalformedToolCallError(ModelResponseParseError):
    """模型产出的工具调用本身不可用：参数不是完整 JSON，或混进了工具调用 markup。

    与父类分家是因为**责任方不同**，而责任方决定该怎么收场：

    - ModelResponseParseError 还覆盖「provider 的 HTTP 响应压根不是 JSON」这种传输层
      故障。那种情况告诉模型「你的输出格式坏了」是在冤枉它，重发同一条请求也不会好转。
    - 这一条是模型自己的输出坏了。它改得了，前提是有人告诉它坏在哪 —— 所以调用方可以
      追加一条纠错消息重试，而不是直接判本轮死刑。

    「不替模型补参数」和「不告诉模型它错了」是两件事，这个类型让上层能只做前一件。
    """


class ModelCancelledError(ModelGatewayError):
    """调用被取消（用户 Ctrl-C / 上层中止 / 超时取消）。"""
