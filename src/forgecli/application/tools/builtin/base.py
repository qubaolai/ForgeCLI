"""内置工具的共用零件.

只放**机制**: schema 校验, 路径规范化, 输出溢写, 可执行文件定位. 任何"要不要做"的判断
都不在这里 —— 那是安全模块的事, 而且 scripts/check_arch.py 会拦住往这里 import 策略.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import NamedTuple

from forgecli.application.tools.artifact_store import ArtifactStore
from forgecli.application.tools.resource_governor import (
    ResourceGovernor,
    ResourceLimits,
)
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathFacts
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.hashing import digest
from forgecli.domain.tool.plan import WorkspaceScope
from forgecli.domain.tool.result import ArtifactRef, ContentPart, ResultProvenance
from forgecli.domain.tool.spec import ToolSpec
from forgecli.shared.json_schema import validate_json_schema

__all__ = [
    "MAX_GLOB_CANDIDATES",
    "EmittedText",
    "emit_text",
    "int_or",
    "limit_depth",
    "read_capability",
    "resolve_target",
    "validate_arguments",
    "path_state_token",
]


# glob 展开的候选上限. 三个工具 (fs_find, search_text, code_definitions) 走同一条
# 展开路径, 上限必须是同一个 —— 各自留一份副本时, 为性能调低其中一个的人不会知道
# 另外两个还停在旧值.
MAX_GLOB_CANDIDATES = 10_000


def int_or(raw: object, fallback: int) -> int:
    """schema 已经限死了取值范围, 这里只是把 `object` 收窄回 int."""
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else fallback


def limit_depth(
    matches: Sequence[str], *, root: str, depth: int | None = None
) -> tuple[str, ...]:
    """把展开结果收窄到 root 下 depth 层以内.

    原先这个函数还兼管忽略规则 (`filter_globbed`), 而那份规则是在展开**之后**才用上的
    —— 于是 `.venv` 与 `.git` 会先把 `MAX_GLOB_CANDIDATES` 的额度吃光, 一次覆盖整个
    仓库的展开报"结果不完整", 而漏掉的恰好是源码. 忽略规则已经下沉到
    `FileSystemView.expand_glob` 的 `exclude`, 在遍历时剪枝; 这里只剩深度这一件事,
    因为深度是**展开之后**才算得出来的相对层数.

    过滤放在 prepare 而不是 perform: 目标集合要在裁决之前就封闭, 被过滤掉的路径根本
    不该出现在 read_paths 里 —— 否则安全侧会为一堆我们压根不打算读的文件做判断.
    """
    if depth is None:
        return tuple(matches)
    kept: list[str] = []
    normalized_root = root.replace("\\", "/").rstrip("/")
    prefix = normalized_root + "/"
    for path in matches:
        normalized_path = path.replace("\\", "/")
        relative = (
            normalized_path[len(prefix) :]
            if normalized_path.startswith(prefix)
            else normalized_path
        )
        segments = tuple(part for part in relative.split("/") if part)
        if len(segments) > depth:
            continue
        kept.append(path)
    return tuple(kept)


def validate_arguments(
    spec: ToolSpec, arguments: Mapping[str, object]
) -> PreparationError | None:
    """按 ToolSpec.input_schema 强制校验入参 (ADR-0004 §3)."""
    errors = validate_json_schema(dict(arguments), spec.input_schema)
    if not errors:
        return None
    return PreparationError(
        code=PreparationErrorCode.INVALID_INPUT,
        message="; ".join(errors[:5]),
    )


def path_state_token(facts: PathFacts) -> str:
    """生成执行前可复核的路径状态令牌。

    工具计划与实际写入之间可能隔着审批。只记路径不够：期间文件可能已被用户或另一个
    进程替换。令牌故意只含可稳定序列化的事实，perform 必须在副作用前重新读取并比较。
    """
    return digest(
        {
            "exists": facts.exists,
            "realpath": facts.realpath,
            "kind": facts.kind.value,
            "is_symlink": facts.is_symlink,
            "link_target": facts.link_target,
            "file_identity": facts.file_identity,
            "size": facts.size,
            "mtime_ns": facts.mtime_ns,
            "mode": facts.mode,
        }
    )


def resolve_target(
    raw: object, context: ExecutionContext, *, field_path: str = "path"
) -> tuple[str, PathFacts] | PreparationError:
    """把入参里的路径规范化为绝对路径并读取身份事实.

    只读: 不创建目录, 不跟随写入. 路径不存在时给结构化错误而不是异常, 因为"文件不存在"
    是模型可以自己改正的普通失败, 不该走安全裁决.
    """
    if not isinstance(raw, str) or not raw.strip():
        return PreparationError(
            code=PreparationErrorCode.INVALID_INPUT,
            message="路径必须是非空字符串",
            field_path=field_path,
        )
    absolute = context.resolve(raw)
    facts = context.filesystem.facts(absolute)
    if not facts.exists:
        return PreparationError(
            code=PreparationErrorCode.TARGET_NOT_FOUND,
            message=f"路径不存在: {absolute}",
            field_path=field_path,
        )
    return absolute, facts


def read_capability(scope: WorkspaceScope) -> Capability:
    """读取哪一档路径决定声明哪种能力.

    工作区内与用户显式 /add-dir 授权过的目录算 WORKSPACE_READ; 其余是 EXTERNAL_READ,
    它在 full_access 之外的模式都要人类确认 —— 读凭证文件走的就是这条路径.
    """
    if scope is WorkspaceScope.OUTSIDE:
        return Capability.EXTERNAL_READ
    return Capability.WORKSPACE_READ


class EmittedText(NamedTuple):
    """emit_text 的产出.

    三项一起返回而不是让调用方各算各的: bytes_out 漏填的后果是终端进度行上每个工具都
    显示 `0 字节`, 而这条线正是用户判断"工具到底有没有拿回内容"的唯一依据. 见过
    fs_read 读完一个 Java 文件显示 0 字节, 模型却在回答里引用了里面的代码.

    bytes_out 是**截断前**的完整字节数, 不是 parts 里那一段. 前者回答"这次产出了多少",
    后者只是塞进上下文的部分 —— 溢写进 artifact 的内容不该因此从计数里消失.
    """

    parts: tuple[ContentPart, ...]
    artifacts: tuple[ArtifactRef, ...]
    bytes_out: int
    # 这次输出的归档位置与大小 (ADR-0032). 知道来源路径的工具再用 replace 补上
    # source_path / source_state —— emit_text 只看得见一段文本, 看不见它从哪来.
    provenance: ResultProvenance = ResultProvenance()


def emit_text(
    text: str,
    *,
    invocation_id: str,
    limits: ResourceLimits,
    artifacts: ArtifactStore | None,
    artifact_name: str = "output",
) -> EmittedText:
    """回填模型的内容片段 + 溢写产物.

    超过阈值时**不静默截断**: 回填部分带显式截断标记与产物 id, 完整内容落 artifact.
    """
    encoded = text.encode("utf-8")
    total = len(encoded)
    truncated = total > limits.max_inline_bytes
    inline = (
        encoded[: limits.max_inline_bytes].decode("utf-8", errors="ignore")
        if truncated
        else text
    )
    if artifacts is None:
        return EmittedText(
            (ContentPart(text=inline, truncated=truncated),),
            (),
            total,
        )
    # 全量落盘 (ADR-0032 决策 6): 没超阈值的也要存. 一级降级要把 transcript 里的正文
    # 换成引用, 而那要求内容确实在某处 —— 只存溢出部分的话, 占最多数的那批中小输出
    # 根本没得降. max_inline_bytes 从此只决定回填多少, 不决定存不存.
    stored, artifact_truncated = ResourceGovernor.clamp(text, limits.max_artifact_bytes)
    ref = artifacts.write(invocation_id=invocation_id, name=artifact_name, data=stored)
    if artifact_truncated:
        ref = replace(ref, truncated=True)
    # **artifacts 与 provenance 说的不是同一件事**, 所以只有截断时才进 artifacts:
    #
    # - ``artifacts`` 的意思是"你看到的输出被切了, 剩下的在这里". 它进审计 payload,
    #   也进终端那行"N 个产物". 全量落盘之后要是把每次调用都算进去, 读一个 20 行的
    #   文件也会显示"1 个产物" —— 而用户看这个数字, 正是想知道有没有东西被切掉.
    # - ``provenance.artifact_id`` 的意思是"完整内容留了一份, 压缩时可以拿它顶替正文".
    #   它没有展示方, 只有 application/context 读.
    return EmittedText(
        (
            ContentPart(
                text=inline,
                truncated=truncated,
                artifact_id=ref.artifact_id,
            ),
        ),
        (ref,) if truncated else (),
        total,
        ResultProvenance(artifact_id=ref.artifact_id, byte_size=total),
    )


def joined(lines: Sequence[str]) -> str:
    return "\n".join(lines)
