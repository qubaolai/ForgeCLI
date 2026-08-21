from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.builtin.base import emit_text
from forgecli.application.tools.resource_governor import ResourceLimits
from forgecli.domain.tool.hashing import digest_text
from forgecli.domain.tool.result import ArtifactRef
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

    def read(self, artifact_id: str) -> str:
        return self.data


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
