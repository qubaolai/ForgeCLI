"""改工作区的三个内置工具: fs.create_file, fs.edit_file, fs.delete (ADR-0004 §14).

**新建与修改是两个工具, 不是一个工具的两种模式.** 这一版之前只有 fs.write_patch, 新建
文件靠"把 old_string 传空串"表达 —— 一个没写在名字上的隐藏约定. 实际发生的事是: 模型
要建新文件, 在工具表里找不到叫"建文件"的东西, 于是自己编了一个 `fs.write_file` 调过去,
拿回"工具未注册", 转头改用 shell 去写. 工具名就是模型的检索键, 名字里没有的能力等于
不存在.

分成两个之后各自的前置条件是硬的, 而且方向相反: create 要求目标**不存在**, edit 要求
目标**存在**. 两条都在 prepare 阶段判定并给出指向另一个工具的错误信息, 模型一次就能
改对.

写入顺序由 ADR-0015 §8.1 规定, 一步都不能提前:

    校验当前对象身份 -> 保存 preimage -> checkpoint ARMED -> 原子写入 -> 记录 postimage

工具本身**不建立**恢复事务 —— 那是协调器在签发授权之前做的事. 工具只在
`plan.normalized_input` 里拿到已经 ARMED 的事务句柄. 这样"没有恢复保障就不给授权"这条
规则由协调器统一保证, 而不是指望每个写工具自觉.

这三个工具用**结构化路径**参数, 所以能在 prepare 里给出封闭的目标集合. 这正是它们与
shell.run 的区别: 谁能证明目标集合, 谁就负责冻结它.

封闭程度不同, spec 的声明必须跟着 prepare 的实际产出走: create 与 edit 永远只碰一个
路径, 所以是 STATIC; fs.delete 删目录时要把它展开成逐个文件, 产出 FORGE_EXPANDED,
所以必须声明 EXPANDABLE (ADR-0004 §4).
"""

from __future__ import annotations

import re
from abc import abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from forgecli.application.tools.builtin.base import validate_arguments
from forgecli.application.tools.builtin.text_edit import Alignment, MatchOutcome, locate
from forgecli.application.tools.tool import Tool, ToolInvocationRequest
from forgecli.application.workspace.execution_context import ExecutionContext
from forgecli.application.workspace.filesystem_view import PathKind
from forgecli.domain.tool.capability import Capability
from forgecli.domain.tool.errors import PreparationError, PreparationErrorCode
from forgecli.domain.tool.plan import (
    ContentPreview,
    DeclarationConfidence,
    PlanEffects,
    TargetResolution,
    ToolPlan,
    WorkspaceScope,
)
from forgecli.domain.tool.result import (
    ContentPart,
    ToolError,
    ToolMetrics,
    ToolResult,
    ToolResultStatus,
)
from forgecli.domain.tool.spec import (
    TargetDeclarationAbility,
    ToolSpec,
)
from forgecli.shared.cancellation import CancelToken

__all__ = ["CreateFileTool", "DeleteTool", "EditFileTool"]

_CREATE_SPEC = ToolSpec(
    name="fs.create_file",
    version="1",
    title="新建文件",
    description=(
        "新建一个文件并写入完整内容. 目标必须尚不存在 —— 已存在时调用失败, "
        "修改已有文件请用 fs.edit_file. 父目录会自动创建."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_WRITE, Capability.EXTERNAL_WRITE}
    ),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=15.0,
)

_EDIT_SPEC = ToolSpec(
    name="fs.edit_file",
    version="1",
    title="精确替换文件片段",
    description=(
        "把已有文件里的 old_string 替换成 new_string. "
        "old_string 要照抄文件内容, 且在文件中唯一出现 —— 命中多处请扩大片段, "
        "或传 replace_all=true 明确要全部替换. "
        "行尾空白, 换行符与整块统一的缩进偏移会自动对齐, "
        "但缩进方式不同 (制表符与空格) 会失败并回给你文件里的原文. "
        "要在文件里新增内容, 就把附近一段已有内容作为 old_string, "
        "再在 new_string 里连同新内容一起写出来. "
        "新建文件请用 fs.create_file. 替换前会保存原内容以便撤销."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_WRITE, Capability.EXTERNAL_WRITE}
    ),
    target_declaration_ability=TargetDeclarationAbility.STATIC,
    default_timeout_seconds=15.0,
)

_DELETE_SPEC = ToolSpec(
    name="fs.delete",
    version="2",
    title="删除文件或目录",
    description=(
        "删除一个工作区文件或目录. 删除前会保存原内容以便撤销. "
        "删除非空目录必须显式传 recursive=true."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "recursive": {"type": "boolean"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
    output_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    declared_capabilities=frozenset(
        {Capability.WORKSPACE_DELETE, Capability.EXTERNAL_WRITE}
    ),
    # EXPANDABLE 而不是 STATIC: 删目录时 prepare 会把它展开成逐个文件并给出
    # FORGE_EXPANDED, 而 STATIC 只允许 STATIC —— 声明成 STATIC 会让协调器的
    # _check_declaration 把每一次目录删除都 fail closed (ADR-0004 §14.5).
    target_declaration_ability=TargetDeclarationAbility.EXPANDABLE,
    default_timeout_seconds=15.0,
)


class _WriteTool(Tool):
    """create 与 edit 的共同部分: 算出最终内容, 然后按同一个形状产出计划.

    两者的差别只在"最终内容怎么来"和"目标该不该存在", 其余 —— 目标解析, 归属判定,
    展示内容, 计划形状 —— 必须逐字一致. 抄两份的话, 迟早有一份忘了带
    content_previews, 而那一份的审批界面上用户看不到文件会变成什么样.
    """

    def __init__(self, writer: Callable[[str, str], None]) -> None:
        self._write = writer

    @abstractmethod
    def _content_for(
        self,
        arguments: Mapping[str, object],
        *,
        target: str,
        exists: bool,
        context: ExecutionContext,
    ) -> _Edit | PreparationError:
        """这次调用最终要写进文件的完整内容, 或者一个说明写不成的错误."""

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(self.spec, request.arguments)
        if invalid is not None:
            return invalid
        raw = request.arguments.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="path 必须是非空字符串",
                field_path="path",
            )
        absolute = context.resolve(raw)
        facts = context.filesystem.facts(absolute)
        if facts.exists and facts.kind is not PathKind.FILE:
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"目标已存在且不是普通文件: {absolute}",
                field_path="path",
            )
        # 不存在的目标用字面绝对路径: realpath 对不存在的对象没有意义.
        target = facts.realpath if facts.exists else absolute
        edit = self._content_for(
            request.arguments, target=target, exists=facts.exists, context=context
        )
        if isinstance(edit, PreparationError):
            return edit
        scope = context.scope_for_write(target)
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=self.spec.name,
            spec_hash=self.spec.spec_hash,
            # 替换在 prepare 阶段就算完, 计划里存的是**最终内容**. 这样裁决与审批绑定
            # 的是"文件会变成什么样", 而不是一段还要再解释一次的替换意图; 执行阶段也
            # 不必重读文件, 少一个 TOCTOU 窗口.
            normalized_input=MappingProxyType(
                {"path": target, "content": edit.content, "note": edit.note}
            ),
            # 审批界面要逐字展示"文件会变成什么样". 内容本身已由 normalized_input 绑定,
            # 这里只是把它交出来 —— 工具层不能依赖安全模块, 所以要走一个中立结构.
            content_previews=(_preview(target, edit.content),),
            capabilities=frozenset({_write_capability(scope)}),
            effects=PlanEffects(write_paths=(target,)),
            target_resolution=TargetResolution.STATIC,
            workspace_scope=scope,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        path = str(plan.normalized_input["path"])
        content = str(plan.normalized_input["content"])
        try:
            self._write(path, content)
        except OSError as exc:
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=self.spec.name,
                status=ToolResultStatus.TOOL_ERROR,
                error=ToolError(code="write_failed", message=str(exc)),
            )
        # 做过容差的话必须说出来: 模型手里那份 old_string 与文件并不一致, 不告诉它,
        # 它下一次还会照着自己那份去拼, 而下一次未必还落在容差范围内.
        note = str(plan.normalized_input.get("note", ""))
        written = f"已写入 {path} ({len(content)} 字符, {len(content.splitlines())} 行)"
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=self.spec.name,
            status=ToolResultStatus.OK,
            content_parts=(
                ContentPart(text=f"{written}\n{note}" if note else written),
            ),
            # 报**写进去了多少**而不是回给模型的那几个字. 漏填的话进度行上每次写文件都
            # 显示"0 字节", 而这条线正是用户判断这次调用到底干了什么的唯一依据.
            metrics=ToolMetrics(bytes_out=len(content.encode("utf-8"))),
        )


class CreateFileTool(_WriteTool):
    """新建文件. 目标已存在即失败, 不覆盖."""

    @property
    def spec(self) -> ToolSpec:
        return _CREATE_SPEC

    def _content_for(
        self,
        arguments: Mapping[str, object],
        *,
        target: str,
        exists: bool,
        context: ExecutionContext,
    ) -> _Edit | PreparationError:
        if exists:
            # 不允许覆盖已存在的文件. 允许了, 这个工具就成了"全文覆盖"的后门 —— 模型
            # 只要没读全文件就能把它整段换掉, 而丢掉的那部分没人看得见.
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message=(
                    f"{target} 已存在, fs.create_file 只用于新建. "
                    "要修改它请用 fs.edit_file 给出待替换的片段; "
                    "确实要整体重写请先 fs.delete 再新建."
                ),
                field_path="path",
            )
        return _Edit(str(arguments.get("content", "")))


class EditFileTool(_WriteTool):
    """替换已有文件里的一个片段."""

    @property
    def spec(self) -> ToolSpec:
        return _EDIT_SPEC

    def _content_for(
        self,
        arguments: Mapping[str, object],
        *,
        target: str,
        exists: bool,
        context: ExecutionContext,
    ) -> _Edit | PreparationError:
        return _apply_replacement(arguments, target, exists, context)


class DeleteTool(Tool):
    def __init__(self, remover: Callable[[str], None]) -> None:
        self._remove = remover

    @property
    def spec(self) -> ToolSpec:
        return _DELETE_SPEC

    def prepare(
        self, request: ToolInvocationRequest, context: ExecutionContext
    ) -> ToolPlan | PreparationError:
        invalid = validate_arguments(_DELETE_SPEC, request.arguments)
        if invalid is not None:
            return invalid
        raw = request.arguments.get("path")
        if not isinstance(raw, str) or not raw.strip():
            return PreparationError(
                code=PreparationErrorCode.INVALID_INPUT,
                message="path 必须是非空字符串",
                field_path="path",
            )
        absolute = context.resolve(raw)
        facts = context.filesystem.facts(absolute)
        if not facts.exists:
            return PreparationError(
                code=PreparationErrorCode.TARGET_NOT_FOUND,
                message=f"路径不存在: {absolute}",
                field_path="path",
            )
        if facts.kind is PathKind.OTHER:
            # 设备, FIFO, socket: 删它们的后果与删普通文件完全不同, 不在本工具范围内.
            return PreparationError(
                code=PreparationErrorCode.TARGET_UNREADABLE,
                message=f"不是普通文件或目录, 拒绝删除: {facts.realpath}",
                field_path="path",
            )

        recursive = bool(request.arguments.get("recursive", False))
        if facts.kind is PathKind.DIRECTORY:
            targets = _files_under(context, facts.realpath)
            if targets and not recursive:
                # 目标集合封闭了才知道要删多少. 不给显式 recursive 就停在这里, 而不是
                # 让一个写错的路径把整棵树带走.
                return PreparationError(
                    code=PreparationErrorCode.INVALID_INPUT,
                    message=(
                        f"{facts.realpath} 是非空目录 ({len(targets)} 个文件), "
                        "删除它需要显式传 recursive=true"
                    ),
                    field_path="recursive",
                )
        else:
            targets = (facts.realpath,)

        # 目标集合是**逐个文件**而不是那一个目录路径: 恢复层按路径存 preimage, 审批
        # 界面按路径列清单. 只报一个目录名, 两边都不知道到底动了什么.
        scope = context.scope_for_write_all(targets or (facts.realpath,))
        return ToolPlan(
            plan_id=request.invocation_id,
            tool_name=_DELETE_SPEC.name,
            spec_hash=_DELETE_SPEC.spec_hash,
            normalized_input=MappingProxyType(
                {
                    "path": facts.realpath,
                    "directory": facts.kind is PathKind.DIRECTORY,
                    "files": targets,
                }
            ),
            capabilities=frozenset({_delete_capability(scope)}),
            effects=PlanEffects(delete_paths=targets),
            # 目录展开由 Forge 完成且已封闭, 不是工具静态声明的.
            target_resolution=(
                TargetResolution.FORGE_EXPANDED
                if facts.kind is PathKind.DIRECTORY
                else TargetResolution.STATIC
            ),
            workspace_scope=scope,
            execution_context=context.to_ref(),
            declaration_confidence=DeclarationConfidence.DECLARED,
        )

    def perform(
        self,
        plan: ToolPlan,
        context: ExecutionContext,
        cancel: CancelToken | None = None,
    ) -> ToolResult:
        path = str(plan.normalized_input["path"])
        try:
            self._remove(path)
        except OSError as exc:
            return ToolResult(
                invocation_id=plan.plan_id,
                tool_name=_DELETE_SPEC.name,
                status=ToolResultStatus.TOOL_ERROR,
                error=ToolError(code="delete_failed", message=str(exc)),
            )
        return ToolResult(
            invocation_id=plan.plan_id,
            tool_name=_DELETE_SPEC.name,
            status=ToolResultStatus.OK,
            content_parts=(ContentPart(text=f"已删除 {path}"),),
        )


_MAX_SOURCE_BYTES = 4 * 1024 * 1024
_EOL = re.compile(r"\r\n|\n|\r")


@dataclass(frozen=True)
class _Edit:
    """要写进文件的完整内容, 外加一句"我做了什么容差"."""

    content: str
    note: str = ""


def _apply_replacement(
    arguments: Mapping[str, object],
    target: str,
    exists: bool,
    context: ExecutionContext,
) -> _Edit | PreparationError:
    """把 old_string -> new_string 算成最终文件内容.

    唯一性是硬约束而不是便利检查: 模型给的 old_string 命中多处时, 它想改的几乎总是其中
    一处. 默认替换全部会静默改掉另外几处, 默认替换第一处则取决于文件顺序 —— 两种都是
    "看起来成功了, 其实改错了". 所以命中多处直接失败, 让模型补足上下文或显式说明要全改.

    逐字符对不上时不直接判失败, 先按行对齐 (见 builtin/text_edit.py): 行尾空白, CRLF 与
    整块统一的缩进偏移都不改变代码含义, 却足以让比对失败, 而模型看不出差在哪.
    """
    old = str(arguments.get("old_string", ""))
    new = str(arguments.get("new_string", ""))
    replace_all = bool(arguments.get("replace_all", False))

    if not old:
        return PreparationError(
            code=PreparationErrorCode.INVALID_INPUT,
            message=(
                "old_string 不能为空: fs.edit_file 只做片段替换. "
                "新建文件请用 fs.create_file."
            ),
            field_path="old_string",
        )
    if old == new:
        # 放行等于消耗一次审批去写回一模一样的内容, 而模型会把"成功"当成"改动生效了".
        return PreparationError(
            code=PreparationErrorCode.INVALID_INPUT,
            message="old_string 与 new_string 完全相同, 这次替换不会改变任何内容.",
            field_path="new_string",
        )

    if not exists:
        return PreparationError(
            code=PreparationErrorCode.TARGET_NOT_FOUND,
            message=f"文件不存在: {target}. 新建文件请用 fs.create_file.",
            field_path="path",
        )

    source = context.filesystem.read_text(target, max_bytes=_MAX_SOURCE_BYTES)
    outcome = locate(source, old)
    if not outcome.spans:
        return _not_found(target, source, old, outcome)
    if len(outcome.spans) > 1 and not replace_all:
        return PreparationError(
            code=PreparationErrorCode.INVALID_INPUT,
            message=(
                f"old_string 在 {target} 中出现了 {len(outcome.spans)} 次. "
                "请扩大片段使其唯一, 或者传 replace_all=true 明确要全部替换."
            ),
            field_path="old_string",
        )

    written = _shift(_retype_endings(new, outcome.ending), outcome.alignment)
    if written is None:
        return PreparationError(
            code=PreparationErrorCode.INVALID_INPUT,
            message=(
                f"old_string 的缩进与 {target} 差一段公共前缀, 但 new_string 里有几行"
                "去不掉这段前缀, 对齐之后会缩进错乱. 请照抄文件里的缩进重写这次替换."
            ),
            field_path="new_string",
        )
    content = _splice(source, outcome.spans, written)
    return _Edit(content, _note_for(outcome))


def _retype_endings(new: str, ending: str) -> str:
    """把替换文本的换行换成文件在用的那种.

    模型永远写 `\n`. 直接贴进一个 CRLF 文件, 改动的那几行就成了 LF —— 文件混着两种换行,
    git diff 里整块都算改过. 只在走过按行对齐时才做: 逐字符命中说明模型给的片段与文件
    逐字节相同, 它写的换行本来就是对的.
    """
    if not ending or ending == "\n":
        return new
    return new.replace("\r\n", "\n").replace("\n", ending)


def _shift(new: str, alignment: Alignment) -> str | None:
    """把 old_string 上观察到的缩进偏移原样施加到 new_string.

    必须施加, 不能原样贴: 模型是用同一套缩进写出 old 和 new 的, old 少了一层公共缩进,
    new 就同样少一层. 只对齐匹配位置而把 new 原样写进去, 产出的是缩进错乱的代码.
    """
    if not alignment.shifted:
        return new
    shifted: list[str] = []
    for line in _EOL.split(new):
        moved = alignment.apply(line)
        if moved is None:
            return None
        shifted.append(moved)
    # 用 zip 把原来的换行原样接回去: split 丢掉的是哪一种, 拼回去就得是哪一种.
    endings = _EOL.findall(new)
    return "".join(
        piece + (endings[index] if index < len(endings) else "")
        for index, piece in enumerate(shifted)
    )


def _splice(source: str, spans: tuple[tuple[int, int], ...], written: str) -> str:
    """从后往前替换, 免得前面的替换把后面的偏移挪了."""
    content = source
    for start, stop in sorted(spans, reverse=True):
        content = content[:start] + written + content[stop:]
    return content


def _note_for(outcome: MatchOutcome) -> str:
    """做过哪些容差, 如实说给模型听."""
    reasons: list[str] = []
    if outcome.alignment.add:
        missing = len(outcome.alignment.add)
        reasons.append(f"old_string 比文件少了 {missing} 个字符的缩进")
    if outcome.alignment.drop:
        extra = len(outcome.alignment.drop)
        reasons.append(f"old_string 比文件多了 {extra} 个字符的缩进")
    if outcome.line_ending_differs:
        reasons.append("换行符不同")
    if not reasons:
        return ""
    return (
        f"注意: {', '.join(reasons)}, 已按文件里的原样对齐后替换. "
        "你手里那份内容与文件并不一致, 后续再改这个文件前请重新读一遍."
    )


def _not_found(
    target: str, source: str, old: str, outcome: MatchOutcome
) -> PreparationError:
    if outcome.quoted:
        # 引原文而不是描述差异: 描述完模型仍然要猜是几个空格还是制表符, 而它刚猜错过.
        return PreparationError(
            code=PreparationErrorCode.INVALID_INPUT,
            message=(
                f"在 {target} 中找不到 old_string —— 忽略空白之后能对上一处, "
                "但缩进方式不一致 (制表符与空格, 或块内相对缩进不同), "
                "按偏移硬贴会写出缩进错乱的代码. 文件里那一段逐字符是:\n"
                f"{outcome.quoted}\n"
                "请照抄上面这几行 (去掉行号与竖线) 作为 old_string 再试."
            ),
            field_path="old_string",
        )
    detail = f" {outcome.conflict}." if outcome.conflict else _miss_hint(source, old)
    return PreparationError(
        code=PreparationErrorCode.INVALID_INPUT,
        message=(
            f"在 {target} 中找不到 old_string. 它必须与文件内容逐字符一致, "
            f"包括缩进与换行.{detail}"
        ),
        field_path="old_string",
    )


def _miss_hint(source: str, old: str) -> str:
    """连按行对齐都没命中时, 指出**最可能的原因**.

    只说找不到, 模型唯一能做的就是换个写法再试一次 —— 而它没有任何证据能看出差在哪.
    判断顺序按"改起来最省事"排: 先看空白, 再定位行号, 最后才让它把文件重读一遍.
    """
    if old != old.strip() and old.strip() and old.strip() in source:
        return " 去掉首尾空白后能命中: 多半是首尾的换行或缩进多带了一点."
    lines = old.splitlines()
    first = lines[0].strip() if lines else ""
    hits = [
        index + 1
        for index, line in enumerate(source.splitlines())
        if _shares_head(line.strip(), first)
    ]
    if hits:
        listed = ", ".join(str(item) for item in hits[:5])
        more = " 等" if len(hits) > 5 else ""
        return (
            f" 第 {listed}{more} 行与 old_string 的首行开头相同, "
            "可以先读这一段再重试."
        )
    return " 先用 fs.read_file 读回当前内容, 再照抄其中一段作为 old_string."


_HEAD_PROBE = 12
_HEAD_FLOOR = 4


def _shares_head(line: str, first: str) -> bool:
    """两行是不是同一行的两个版本.

    双向判断: 模型既可能比文件多带了尾部 (顺手补了注释), 也可能少带一截. 只按一个方向
    比, 另一半情况就报不出行号, 而那正是最常见的一种 —— 模型凭记忆复述了一行代码.
    """
    if len(line) < _HEAD_FLOOR or len(first) < _HEAD_FLOOR:
        return False
    return line.startswith(first[:_HEAD_PROBE]) or first.startswith(line[:_HEAD_PROBE])


def _files_under(context: ExecutionContext, root: str) -> tuple[str, ...]:
    """递归列出目录下的全部文件 (不含目录本身).

    走 FileSystemView 而不是直接 os.walk: 展开必须基于这次调用冻结的那一份视图, 否则
    "审批时看到的清单"与"执行时真删的东西"可能不是一回事.
    """
    found: list[str] = []
    stack = [root]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            # 目录里有指回上层的符号链接时不至于转圈.
            continue
        seen.add(current)
        for name in context.filesystem.list_dir(current):
            child = f"{current}/{name}"
            facts = context.filesystem.facts(child)
            if facts.kind is PathKind.DIRECTORY:
                stack.append(facts.realpath or child)
            elif facts.exists:
                found.append(facts.realpath or child)
    return tuple(sorted(found))


def _write_capability(scope: WorkspaceScope) -> Capability:
    return (
        Capability.EXTERNAL_WRITE
        if scope is WorkspaceScope.OUTSIDE
        else Capability.WORKSPACE_WRITE
    )


def _delete_capability(scope: WorkspaceScope) -> Capability:
    return (
        Capability.EXTERNAL_WRITE
        if scope is WorkspaceScope.OUTSIDE
        else Capability.WORKSPACE_DELETE
    )


# 单份展示内容的上限. 超了截断并标注 —— 审批界面滚不完 10 MB, 而"已截断"这三个字本身
# 就是用户需要知道的信息: 他看到的不是全部.
_MAX_PREVIEW_CHARS = 64 * 1024


def _preview(path: str, content: str) -> ContentPreview:
    if len(content) <= _MAX_PREVIEW_CHARS:
        return ContentPreview(path=path, content=content)
    return ContentPreview(
        path=path, content=content[:_MAX_PREVIEW_CHARS], truncated=True
    )
