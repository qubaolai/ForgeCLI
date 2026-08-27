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


def test_json_schema_enforces_declared_numeric_and_length_limits() -> None:
    schema = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 3},
            "name": {"type": "string", "minLength": 2, "maxLength": 4},
            "items": {"type": "array", "minItems": 1, "maxItems": 2},
        },
    }

    errors = validate_json_schema({"count": 4, "name": "x", "items": [1, 2, 3]}, schema)

    assert any("maximum" in error for error in errors)
    assert any("minLength" in error for error in errors)
    assert any("maxItems" in error for error in errors)


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
