from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import emit_text
from forgecli.application.tools.resource_governor import ResourceLimits
from forgecli.domain.tool.hashing import digest_text
from forgecli.domain.tool.result import (
    ArtifactRef,
    ContentPart,
    ToolError,
    ToolResult,
    ToolResultStatus,
)
from forgecli.shared.json_schema import validate_json_schema


class _RecordingArtifacts(ArtifactStore):
    def __init__(self) -> None:
        self.data = ""

    def write(self, *, invocation_id: str, name: str, data: str) -> ArtifactRef:
        self.data = data
        return ArtifactRef(
            artifact_id=f"{invocation_id}:{name}",
            path="artifact.txt",
            size=len(data.encode("utf-8")),
            content_hash=digest_text(data),
        )

    def read(
        self,
        artifact_id: str,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> str:
        return self.data

    def exists(self, artifact_id: str) -> bool:
        return True

    def touch(self, artifact_id: str) -> None:
        return None

    def sweep(self, *, older_than_seconds: float) -> int:
        return 0


def test_json_schema_reports_every_violated_constraint_with_its_location() -> None:
    """断言的是"约束生效"与"指得回位置", 不是错误消息的具体措辞.

    原先这条用例查的是消息里有没有 `minLength` 这个词. 换成 jsonschema 之后消息变成
    "'x' is too short" —— 约束照样生效, 用例却红了. 查措辞等于把校验器的实现细节钉进
    用例, 而那正是这次换掉手写子集时最不该保留的东西.
    """
    schema = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 3},
            "name": {"type": "string", "minLength": 2, "maxLength": 4},
            "items": {"type": "array", "minItems": 1, "maxItems": 2},
        },
    }

    errors = validate_json_schema({"count": 4, "name": "x", "items": [1, 2, 3]}, schema)

    assert [error.split(":")[0] for error in errors] == ["$.count", "$.items", "$.name"]


def test_json_schema_enforces_pattern() -> None:
    """`pattern` 曾经被静默忽略.

    手写子集只覆盖 type/required/enum/长度/大小, 并明说"未覆盖的关键字被忽略". 于是
    schema 里写了 pattern 的人会以为它生效, 而不合法的参数直接进了 prepare —— 没有
    任何一层会说话. 这条用例钉住它不会再退回去.
    """
    schema = {"type": "object", "properties": {"kind": {"pattern": "^[a-z_]+$"}}}

    assert validate_json_schema({"kind": "fs_read"}, schema) == []
    assert validate_json_schema({"kind": "Not Valid"}, schema) != []


def test_json_schema_reports_an_invalid_schema_instead_of_passing_silently() -> None:
    """schema 本身写坏时不能静默放行 —— 放行等于这次调用完全没有校验."""
    errors = validate_json_schema({"a": 1}, {"type": "object", "required": "a"})

    assert errors and "schema 本身不合法" in errors[0]


def test_emit_text_limits_artifact_by_utf8_bytes() -> None:
    artifacts = _RecordingArtifacts()
    limits = ResourceLimits(
        timeout_seconds=1,
        max_inline_bytes=1,
        max_artifact_bytes=4,
    )

    emitted = emit_text(
        "你好世界",
        invocation_id="call-1",
        limits=limits,
        artifacts=artifacts,
    )

    assert artifacts.data == "你"
    assert len(artifacts.data.encode("utf-8")) <= 4
    assert emitted.artifacts[0].truncated is True
    assert emitted.bytes_out == len("你好世界".encode())


def test_failed_result_text_carries_the_error_message() -> None:
    """失败结果回给模型的文本里必须有失败原因.

    ``fs_apply_patch`` 的运行期失败只填 error 不填 content_parts, 而 content_parts 是
    模型唯一看得到的那一段. 日志里能查到 `[Errno 17] File exists`, 模型手里却是空串,
    于是它只能原样再试一次, 或者改用 shell 自己去摸文件系统.
    """
    result = ToolResult(
        invocation_id="inv-1",
        tool_name="fs_apply_patch",
        status=ToolResultStatus.TOOL_ERROR,
        error=ToolError(code="apply_failed", message="a.java: 已完成 1 处, 未回滚."),
    )

    assert result.text == "a.java: 已完成 1 处, 未回滚."


def test_failed_result_text_keeps_output_and_reason_apart() -> None:
    """有输出的失败两段都要留: 输出说"跑出了什么", error 说"为什么算失败"."""
    result = ToolResult(
        invocation_id="inv-1",
        tool_name="shell_run",
        status=ToolResultStatus.TOOL_ERROR,
        content_parts=(ContentPart(text="mvn: command not found"),),
        error=ToolError(code="shell_failed", message="退出码 127"),
    )

    assert result.text == "mvn: command not found\n退出码 127"
