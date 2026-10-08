"""Tool 协议 (ADR-0054 决策 1).

一个工具要回答的全部问题都在这里, 缺省值与 `buildTool()` 的 `TOOL_DEFAULTS` 同:
并发不安全, 非只读, 非破坏性, `check_permissions` 缺省 allow —— 安全来自每个真实
工具覆盖它, 不来自框架. 框架 (tools/execution.py) 只按固定顺序问, 并把回答变成
模型看得到的 tool_result.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from forgecli.common.cancellation import CancelToken
from forgecli.core.app_state import AppState
from forgecli.core.message import ChatMessage, ToolResultBlock
from forgecli.core.permissions import PermissionResult, allow
from forgecli.core.tool.file_state import FileStateCache
from forgecli.core.tool.observer import ToolRunObserver
from forgecli.core.tool.tool_call import ToolSchema
from forgecli.human_interaction.service import HumanPromptService

__all__ = [
    "DEFAULT_MAX_RESULT_SIZE_CHARS",
    "CanUseToolFn",
    "InterruptBehavior",
    "ProgressCallback",
    "PromptOptions",
    "SearchOrRead",
    "Tool",
    "ToolProgress",
    "ToolResult",
    "ToolUseContext",
    "ToolUseOptions",
    "ValidationResult",
]

# 结果落盘阈值的上限 (对应 constants/toolLimits.ts). 工具自己声明的更小就取更小的.
DEFAULT_MAX_RESULT_SIZE_CHARS = 50_000


class InterruptBehavior(Enum):
    CANCEL = "cancel"
    BLOCK = "block"


@dataclass(frozen=True)
class SearchOrRead:
    """这次调用是搜索 / 读取 / 列目录的哪一种, 界面据此折叠展示."""

    is_search: bool = False
    is_read: bool = False
    is_list: bool = False


@dataclass(frozen=True)
class ValidationResult:
    """`validate_input` 的返回. ok 为假时 message 直接回给模型."""

    ok: bool
    message: str = ""
    error_code: int = 0
    meta: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ToolResult:
    """`call` 的返回. data 的形状由每个工具自己定, 由 map_tool_result_to_block 翻译."""

    data: object
    new_messages: tuple[ChatMessage, ...] = ()
    # 工具产出了需要人裁决的东西 (plan_write 交出一份计划), 本轮到此为止 (ADR-0023).
    await_user_decision: bool = False


@dataclass(frozen=True)
class ToolProgress:
    tool_use_id: str
    text: str


ProgressCallback = Callable[[ToolProgress], None]


@dataclass(frozen=True)
class PromptOptions:
    """`prompt()` 与 `description()` 拿到的环境: 当前工具池与权限上下文的读取器."""

    tools: tuple[Tool, ...] = ()
    get_app_state: Callable[[], AppState] | None = None


@dataclass
class ToolUseOptions:
    tools: tuple[Tool, ...]
    non_interactive: bool = False
    read_only: bool = False
    debug: bool = False
    main_loop_model: str = ""


@dataclass
class ToolUseContext:
    """执行上下文总线 (对应 ToolUseContext), 一次 turn 内共用."""

    options: ToolUseOptions
    abort: CancelToken
    read_file_state: FileStateCache
    messages: tuple[ChatMessage, ...]
    get_app_state: Callable[[], AppState]
    set_app_state: Callable[[AppState], None]
    cwd: str
    environment: Mapping[str, str]
    # 项目的根 (启动时的 cwd 与 /add-dir 之外的固定根). 额外目录在权限上下文里.
    workspace_roots: tuple[str, ...]
    session_id: str
    turn_id: str
    prompts: HumanPromptService
    observer: ToolRunObserver
    # 文件历史: Edit / Write 落盘前调它备份原件.
    track_file_edit: Callable[[str], None]
    # 超长结果落盘的目录.
    tool_results_dir: str
    # 项目内部路径 (计划文件目录等): 权限层视为可读可写.
    internal_paths: tuple[str, ...] = ()
    agent_id: str = ""
    tool_use_id: str = ""


# 权限裁决的入口: (tool, input, context, parent_message, tool_use_id) -> 决定.
CanUseToolFn = Callable[
    ["Tool", Mapping[str, object], ToolUseContext, ChatMessage | None, str],
    PermissionResult,
]


class Tool(ABC):
    """工具的公共协议 (对应 Tool.ts 的 Tool 类型).

    子类必须实现 prompt、call 和 map_tool_result_to_block, 并声明工具名与输入
    schema. 其余方法提供默认行为, 按工具需要覆盖. 实例还须由组合根加入工具池,
    仅继承本类不会自动注册.

    执行链依次进行 schema 校验、语义校验、补充可观测入参、权限裁决、执行和
    结果转换. 除 call 外的查询与展示方法应尽量无副作用; 权限检查不能代替实际
    执行时的边界检查, 安全属性声明也不会自动实现底层操作的取消、隔离或同步保护.
    """

    name: str = ""
    aliases: tuple[str, ...] = ()
    search_hint: str = ""
    input_schema: Mapping[str, object] = MappingProxyType({})
    output_schema: Mapping[str, object] | None = None
    strict: bool = False
    # 超过这个字符数就落盘 (ADR-0054 决策 5). 无穷大 = 永不落盘.
    max_result_size_chars: int = DEFAULT_MAX_RESULT_SIZE_CHARS

    # ---- 给模型的说明 ----

    def description(self, input: Mapping[str, object]) -> str:
        """返回面向用户的简短说明, 与面向模型的完整 prompt 区分.

        input 是本次调用的参数, 子类可据此描述具体操作. 默认忽略 input,
        使用空 PromptOptions 调用 prompt, 取去掉首尾空白后的第一行; 因此
        prompt 应支持无上下文调用并返回非空文本. 本方法不执行工具操作.
        """
        return self.prompt(PromptOptions()).strip().splitlines()[0]

    @abstractmethod
    def prompt(self, options: PromptOptions) -> str:
        """返回模型可见的工具使用说明; 子类必须实现.

        options 提供当前工具池及可选的应用状态读取器, 可用于说明工具间的配合
        或当前限制. 返回文本会成为 ToolSchema.description, 应说明何时使用、
        参数语义与关键限制. 不要执行操作或产生副作用; 工具 schema 会被缓存,
        不应在说明中加入时间戳或每次调用才确定的状态.
        """

    def to_model_schema(self, options: PromptOptions) -> ToolSchema:
        """将工具声明转换为发送给模型的 ToolSchema.

        options 原样传给 prompt. 返回值包含规范工具名、完整说明和 input_schema
        的只读顶层副本; 内层对象仍共享, 不是深拷贝. 本方法不执行参数校验,
        也不把 aliases、output_schema 或 strict 字段写入当前 ToolSchema.
        """
        return ToolSchema(
            name=self.name,
            description=self.prompt(options),
            parameters=MappingProxyType(dict(self.input_schema)),
        )

    # ---- 运行控制 ----

    def is_enabled(self) -> bool:
        """声明工具当前是否启用, 默认 True.

        内置工具池组装时调用; 返回 False 的工具不进入可用池, 模型也看不到它的
        schema. 可根据功能开关或依赖是否就绪覆盖, 不应在此执行昂贵初始化.
        启用不等于授权, deny 规则仍会独立过滤工具.
        """
        return True

    def interrupt_behavior(self) -> InterruptBehavior:
        """声明期望的中断策略, 默认 BLOCK (等待当前调用结束).

        CANCEL 表示工具设计上支持取消. 当前执行链尚未读取此声明, 不会因此
        自动打断线程或网络请求; 实际取消须在 call 中检查 context.abort,
        停止底层操作并抛出 CancelledError. 覆盖本方法不能替代取消实现.
        """
        return InterruptBehavior.BLOCK

    def requires_user_interaction(self) -> bool:
        """声明工具是否必须由用户参与完成, 默认 False.

        例如提问工具应返回 True. 当工具自身的权限检查返回 ask 时, 此声明使
        bypass 模式仍保留询问, auto 分类器也不能替用户作答. 返回 True 本身
        不会创建交互卡片, 还须由工具的权限结果或执行逻辑发起交互.
        """
        return False

    # ---- 安全属性, 缺省全部保守 ----

    def is_concurrency_safe(self, input: Mapping[str, object]) -> bool:
        """判断给定 input 的调用能否与其他安全调用并发执行, 默认 False.

        调度器在 schema 校验后查询, 将连续返回 True 的调用放入同一并发批次.
        子类须确认不会竞争共享可变状态或破坏操作顺序, 不能仅凭“只读”推断.
        判断应快速且无副作用; 校验失败或本方法抛异常时, 调度器按不安全处理.
        """
        return False

    def is_read_only(self, input: Mapping[str, object]) -> bool:
        """判断给定 input 的操作是否只读, 默认 False.

        只读执行约束会在权限裁决后检查此值, False 会阻止调用. 返回 True 仍须
        满足子 Agent 工具白名单及其他权限规则, 不会自动获得访问授权. 同一工具
        的不同操作可分别判断, 例如 Bash 仅对能确认无写入的命令返回 True.
        """
        return False

    def is_destructive(self, input: Mapping[str, object]) -> bool:
        """判断给定 input 是否涉及覆盖、删除等破坏性操作, 默认 False.

        返回值写入权限交互卡片的 destructive 字段, 供用户了解操作风险.
        这是展示用的风险声明, 不会自行触发拒绝或审批; 实际限制须在权限检查
        与执行逻辑中落实. False 不代表操作无副作用或可安全并发.
        """
        return False

    def is_search_or_read_command(
        self, input: Mapping[str, object]
    ) -> SearchOrRead | None:
        """声明本次 input 属于搜索、读取或列目录中的哪些类别.

        返回 SearchOrRead 可同时标记多个类别; 默认 None 表示没有分类声明.
        此接口为展示分类预留, 当前执行链尚未消费它. 它不是只读证明, 不影响
        权限或并发调度; 相关约束分别由 is_read_only 和 is_concurrency_safe 表达.
        """
        return None

    def get_path(self, input: Mapping[str, object]) -> str | None:
        """提取本次 input 对应的主要文件系统路径, 默认 None.

        使用文件权限辅助函数的工具通过此方法提供待检查路径; 相对路径与 ~
        由权限层结合 context.cwd 解析. None 不代表放行: 读权限辅助函数按 cwd
        检查, 写权限辅助函数转为询问. 多路径操作应自行检查所有目标, 不能只
        依赖这个单路径接口. 本方法不负责访问文件或验证路径存在性.
        """
        return None

    def check_permissions(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> PermissionResult:
        """返回工具自身针对本次操作的权限意见, 不直接执行或弹出审批.

        input 是补过派生字段的可观测参数, context 提供工作目录、权限状态等.
        返回 PermissionResult, 可表达 allow、deny、ask 或 passthrough, 并附带
        原因、建议及更新后的参数. 默认 allow(updated_input=input), 与 buildTool
        一致; 涉及路径、命令等访问边界的子类应覆盖.

        最终决定由 security/permissions.py 结合整工具规则与当前模式得出,
        不是本方法单独决定. 本方法抛异常时, 权限层先按 passthrough 处理,
        不直接返回 allow; 后续仍由模式和规则决定放行或转为询问.
        """
        return allow(updated_input=input)

    # ---- 校验与规范化 ----

    def validate_input(
        self, input: Mapping[str, object], context: ToolUseContext
    ) -> ValidationResult:
        """在 schema 校验之后、权限裁决之前验证参数的业务语义.

        input 是调用参数的副本, context 可用于检查路径、配置等环境条件.
        默认返回 ValidationResult(ok=True). 无效输入应返回 ok=False 及模型
        可以据此纠正调用的 message; 抛出的异常也会被执行链转成校验错误.
        此阶段尚未授权, 不应执行目标操作或修改参数; 派生字段另由 backfill
        方法补充. 校验成功后, 执行时仍需考虑外部状态已变化的情况.
        """
        return ValidationResult(ok=True)

    def backfill_observable_input(self, input: dict[str, object]) -> None:
        """原地补充供权限裁决和事件展示使用的派生字段, 默认不做任何事.

        input 是通过校验后的参数的浅拷贝, 例如可补充规范路径. 只修改顶层字段
        或替换内层对象, 避免通过共享的嵌套对象修改原始参数. 必须幂等, 不执行
        实际操作. 返回 None, 调用方读取修改后的字典.

        call 默认仍收到原始参数; 只有权限结果提供了不同于此可观测副本的
        updated_input, 执行链才改用那份参数. 因而不要依赖此方法向 call 注入
        必需参数, 执行所需的规范化应在工具自身落实.
        """
        return None

    def inputs_equivalent(
        self, first: Mapping[str, object], second: Mapping[str, object]
    ) -> bool:
        """判断 first 与 second 是否表示同一个操作, 默认比较字典内容.

        子类可忽略不影响操作语义的字段, 但不能忽略路径、写入内容等会改变
        目标或效果的信息. 比较应无副作用. 当前执行链尚未调用本接口,
        权限 updated_input 是否替换原参数仍使用字典相等判断.
        """
        return dict(first) == dict(second)

    # ---- 展示 ----

    def user_facing_name(self, input: Mapping[str, object]) -> str:
        """返回本次 input 对应的用户可见名称, 默认使用规范工具名 name.

        权限卡片将其用作标题和展示字段. 子类可按操作类型提供更易读的名称,
        但不应包含大段参数或敏感信息. 此名称只用于展示, 不改变模型调用名、
        工具查找或权限规则匹配所用的 name 与 aliases.
        """
        return self.name

    def get_activity_description(self, input: Mapping[str, object]) -> str | None:
        """返回本次 input 的简短活动描述, 例如“正在读取某个文件”.

        用于校验完成事件和权限交互展示, 调用时工具尚未执行, 不能描述成已经
        成功完成. 默认 None 表示不提供; 执行事件回退到工具名, 权限卡片使用
        空文本. 应从参数生成描述, 避免读取大文件或触发网络请求.
        """
        return None

    def get_tool_use_summary(self, input: Mapping[str, object]) -> str | None:
        """返回本次调用的紧凑摘要, 用于权限卡片和执行完成事件.

        input 是调用参数, 不包含执行结果; 可概括目标路径或操作, 不应推断
        成功状态、结果数量等只有输出才能确定的信息. 默认 None 表示不提供,
        调用方使用空文本. 与较完整的活动描述相比, 摘要应便于列表中快速浏览.
        """
        return None

    def to_auto_classifier_input(self, input: Mapping[str, object]) -> object:
        """将 input 投影为 auto 模式安全分类器可阅读的紧凑内容.

        返回字符串或可 JSON 序列化的对象, 应保留判断风险所需的目标与操作;
        例如 Bash 返回命令, Edit 返回路径与写入内容 (ADR-0058 决策 3).
        默认空字符串表示不将该工具调用写入分类器记录, 不等于权限放行.

        有安全含义的工具应覆盖. 历史调用的参数可能不符合当前 schema, 因此
        实现应容忍字段缺失或类型变化; 若抛异常, 分类器编码逻辑会回退到原始
        参数. 不要把凭证等无助于判断操作意图的信息加入返回值.
        """
        return ""

    # ---- 执行 ----

    @abstractmethod
    def call(
        self,
        input: Mapping[str, object],
        context: ToolUseContext,
        can_use_tool: CanUseToolFn,
        parent_message: ChatMessage | None,
        on_progress: ProgressCallback | None = None,
    ) -> ToolResult:
        """执行一次已通过校验和权限裁决的调用; 子类必须实现.

        input 为执行链选定的参数: 通常是原始参数, 也可能是权限裁决更新后的
        参数. context 提供取消信号、会话状态、文件状态、交互服务与结果目录等,
        且 tool_use_id 已设为本次调用 ID. can_use_tool 是进一步裁决所需的入口,
        可用于执行中才发现的额外操作, 不必重复审批已授权的同一次操作.
        parent_message 是触发调用的父消息, 可能为 None. on_progress 非空时
        可报告带有当前 tool_use_id 的 ToolProgress; 当前执行链将进度写入日志.

        返回 ToolResult, data 交给 map_tool_result_to_block 转为模型结果,
        new_messages 可追加消息, await_user_decision 可要求本轮等待用户决定.
        发现 context.abort 已取消时应清理资源并抛 CancelledError; 其他执行
        异常由执行链转为错误结果. 长耗时操作须自行支持取消、超时与资源关闭,
        框架不会强制终止内部阻塞操作.
        """
        ...

    @abstractmethod
    def map_tool_result_to_block(
        self, output: object, tool_use_id: str
    ) -> ToolResultBlock:
        """把 call 返回的 ToolResult.data 转为模型可见的结果块; 必须实现.

        output 是工具自定义的输出对象, tool_use_id 是对应的调用 ID. 返回
        ToolResultBlock 时须用该 ID 填充 tool_call_id, 将输出转成文本 content,
        并为业务失败设置 is_error=True, 避免被执行链记录为成功.

        本方法只负责转换, 不重新执行操作. 超长文本由后续 persist_if_needed
        统一落盘并生成预览, 无须在此重复实现. 当前转换阶段不在 call 的异常
        捕获范围内, 因此应覆盖工具实际可能返回的输出形状, 避免格式转换异常
        使整个执行流程退出.
        """
        ...
