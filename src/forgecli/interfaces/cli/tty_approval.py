"""终端审批界面 (ADR-0016 §8.4, 修订 ADR-0013 §14 的默认呈现).

HITL 是用户决定是否授权 Forge 执行动作的交互面, **不是内部安全分析报告**. 信息层级固定,
不受展示密度影响:

    模式与工作区 -> 原始命令 -> 完整脚本内容 -> 完整目标集合 -> deny / always / once

砍掉的是 plan_hash, target_set_hash, 风险标签, 执行画像和失效字段 —— 它们是授权重验的
内部绑定项, 对人做决定没有帮助, 却会把真正要读的东西淹掉. **目标集合不在砍掉之列**:
用户批准的是效果清单, 不是命令字符串, `rm *.log` 展开成 3 个还是 300 个文件必须可见.

选择方式是**上下键 + 回车**, 不是让用户敲数字 —— 与斜杠命令菜单一致. 数字键仍然直选,
照顾记得住"3 是拒绝"的人. 没有真终端时回退到读一行 (那时只能敲数字), 但那条路不该是常态.

选项语义:

- `[1] once` 只批准当前请求, 且是**默认项**: 直接回车等于选它.
- `[2] always` 只创建**工作区范围**的学习规则 (ApprovalScope.WORKSPACE), 文案必须写出
  "本工作区内" —— 让用户以为自己批的是全局, 比不给这个选项更糟.
- `[3] deny` 拒绝执行.

回车取默认项, 但**只有回车**才走这条路. Esc, Ctrl-C, EOF 和任何认不出来的输入一律按 deny
处理: 它们要么说明没有人在回答, 要么说明用户想说的不是这三个里的任何一个 —— 把它们当成
"他按了回车所以是同意"就是凭空造出一次授权. 非交互环境更是直接 PENDING.

ApprovalScope.ALWAYS 与 SESSION 不由这里产出; 需要它们时走专门的规则管理界面.
Mandatory Ask 只给 once 与 deny.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from forgecli.application.security.approval_service import ApprovalService
from forgecli.domain.security.approval import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalView,
)
from forgecli.domain.security.vocabulary import ApprovalScope
from forgecli.interfaces.cli.tty.select import (
    SelectOption,
    SelectUnavailable,
    select_one,
)
from forgecli.interfaces.cli.tty.tty import stdin_is_tty

__all__ = ["TtyApprovalService", "render_approval_view"]

# 单行展示上限. 超长命令换行而不是截断: 截掉的那一段往往正是用户会拒绝的理由.
_WRAP_AT = 100

# 正文来源的中文标签. inline 与 file 对用户是完全不同的两件事: 一段写在命令里的代码,
# 与磁盘上一个可能刚被改过的文件.
_ORIGIN_LABELS = {
    "inline": "命令内联",
    "heredoc": "heredoc 正文",
    "file": "脚本文件",
    "encoded": "base64 解码后",
    "unresolved": "入口无法解析",
}


def _safe(text: str) -> str:
    """剥掉控制序列再交给 Rich.

    命令与路径都来自不可信输入. ANSI/OSC 能重画屏幕, 把"删除 300 个文件"藏到看不见的
    地方; Rich markup 里的方括号则会被当成样式标签吃掉, 让展示的命令与真正执行的不同.
    """
    stripped = "".join(
        char for char in text if char == "\t" or char >= " " and char != "\x7f"
    )
    return escape(stripped)


def render_approval_view(
    console: Console, view: ApprovalView, *, interactive: bool = False
) -> None:
    """按固定层级打印确认视图. 与输入分开, 便于快照测试."""
    roots = ", ".join(view.workspace_roots) or "(无)"
    console.print(f"\n模式 {_safe(view.mode)} · 工作区 {_safe(roots)}\n")
    console.print(f"[bold]? 是否允许 Forge 执行[/bold]：{_safe(view.raw_command)}\n")

    _render_content(console, view)

    if view.consequential:
        # 纯读取时不列: 命令与脚本已经逐字看过了. 一旦沾上写 / 删 / 网络 / 外部副作用
        # 或目标没封闭, 清单必须出现 —— 那些是命令字符串看不出来的后果.
        _render_targets(console, view)
    if not interactive:
        # 非交互回退路径才自己打选项; 交互时由 select_one 渲染并接管按键.
        _render_options(console, view)


def _render_content(console: Console, view: ApprovalView) -> None:
    """逐字展示要跑的代码与要写的内容.

    这一节不能省, 也不能只给路径: 用户批准的是"会发生什么", 而 `python3 deploy.py` 这
    七个字里没有任何能让人做判断的信息. 同理 `fs.apply_patch README.md` —— 要看的是
    文件会变成什么样.
    """
    for snapshot in view.script_snapshots:
        origin = _ORIGIN_LABELS.get(snapshot.origin, snapshot.origin)
        where = f" ({snapshot.path})" if snapshot.path else ""
        console.print(f"要执行的 {_safe(snapshot.language)} 代码 · {origin}{where}：\n")
        _render_body(console, snapshot.source)
    for preview in view.content_previews:
        suffix = " (已截断)" if preview.truncated else ""
        console.print(f"{_safe(preview.path)} 将变成{suffix}：\n")
        _render_body(console, preview.content)


def _render_body(console: Console, body: str) -> None:
    for line in body.splitlines() or [""]:
        console.print(f"    {_safe(line)}")
    console.print()


def _render_targets(console: Console, view: ApprovalView) -> None:
    # 目标集不封闭时, 每个数字都只是已知下限, 必须这么标出来.
    #
    # 不标的后果是这一行**在说假话**: `find . -exec rm {} \;` 的删除目标由运行期产生,
    # 清单是空的, 于是最显眼的一行写着"删除 0" —— 一条会删文件的命令, 用户读到的是它
    # 什么都不删. 而这一行正是他做决定的依据.
    prefix = "" if view.closed else "≥"
    counts = " · ".join(f"{label} {prefix}{count}" for label, count in view.counts)
    console.print(f"目标 {counts}" if counts else "目标 (无)")
    if not view.closed:
        # 目标集不封闭: 显示保守上界与未知原因, 不能当成普通审批直接放行
        # (ADR-0013 §6.2).
        console.print("上面的数字是已知下限, 实际影响范围可能更大")
        console.print(f"未封闭原因：{_safe(view.unresolved_reason or '')}")
        console.print("执行前会建立 checkpoint, 事后可以 /undo")
    for group in view.target_groups:
        if not group.paths:
            continue
        suffix = "" if view.closed else ", 可能不止"
        console.print(f"{group.label} ({group.count} 项{suffix}):")
        for path in group.paths:
            # 全量列出, 不省略. 条目多时终端自己滚, 不由这里替用户决定看哪几条.
            console.print(f"  {_safe(_wrap(path))}")
    console.print()


def _wrap(text: str) -> str:
    if len(text) <= _WRAP_AT:
        return text
    chunks = [text[at : at + _WRAP_AT] for at in range(0, len(text), _WRAP_AT)]
    return "\n    ".join(chunks)


def _render_options(console: Console, view: ApprovalView) -> None:
    for index, (key, label) in enumerate(_options(view), start=1):
        console.print(f"[{index}] {key:<6} {label}")
    console.print()


def _options(view: ApprovalView) -> tuple[tuple[str, str], ...]:
    """once 在首位且是默认; always 只在策略允许学习授权时出现, deny 永远在.

    always 的措辞随内容变: 执行脚本时学的是"这份脚本内容", 普通命令时学的是"这条
    规范化命令". 两者的绑定事实不同, 文案含糊会让用户以为批的范围比实际大.
    """
    subject = "脚本" if view.script_snapshots else "命令"
    options = [("once", "仅允许本次执行（默认）")]
    if ApprovalScope.WORKSPACE in view.allowed_scopes:
        options.append(("always", f"本工作区内以后遇到相同{subject}直接允许"))
    options.append(("deny", "拒绝执行"))
    return tuple(options)


class TtyApprovalService(ApprovalService):
    def __init__(self, console: Console) -> None:
        self._console = console

    def request(self, approval: ApprovalRequest) -> ApprovalResponse:
        if not stdin_is_tty():
            # 非交互环境: 保持 pending. 绝不能因为"没人能回答"就默认放行.
            return ApprovalResponse(
                outcome=ApprovalOutcome.PENDING,
                approval_id=approval.approval_id,
                note="非交互环境无法审批, 请求保持 pending",
            )
        # 直接用请求里那份视图: 允许哪几个范围由协调器按学习规则算好, 并已由
        # ApprovalRequest 校验过 (Mandatory Ask 只能 once). 界面再算一遍等于同一条
        # 规则存两份, 而两份一旦漂移, 界面给出的选项会与重验接受的范围对不上.
        render_approval_view(self._console, approval.view, interactive=True)
        return self._ask(approval, approval.view)

    def _ask(self, approval: ApprovalRequest, view: ApprovalView) -> ApprovalResponse:
        options = _options(view)
        try:
            chosen = select_one(
                self._console,
                tuple(
                    SelectOption(key=key, label=key, detail=label)
                    for key, label in options
                ),
            )
        except SelectUnavailable:
            # 回退到读一行. 选项此时还没被打出来 (交互路径由 select_one 负责渲染),
            # 得先打出来 —— 否则就是在问"请选择 [1/2/3]"却没说 1/2/3 是什么.
            _render_options(self._console, view)
            return self._resolve(approval, options, self._typed(len(options)))
        if chosen is None:
            # Esc / Ctrl-C: 用户主动退出选择, 按拒绝处理.
            return self._denied(approval, "用户取消, 按拒绝处理")
        return self._resolve(approval, options, chosen.key)

    def _typed(self, count: int) -> str:
        """没有真终端时的回退: 读一行. EOF 返回空串, 由 _resolve 落到 deny."""
        keys = "/".join(str(number) for number in range(1, count + 1))
        try:
            return self._console.input(f"请选择 [{keys}]：").strip().lower()
        except EOFError:
            return "\x00"  # 认不出来的输入 -> deny, 与"直接回车取默认"区分开

    def _resolve(
        self,
        approval: ApprovalRequest,
        options: tuple[tuple[str, str], ...],
        answer: str,
    ) -> ApprovalResponse:
        keys = [key for key, _ in options]
        chosen = ""
        if answer == "\x00":
            return self._denied(approval, "输入已结束, 按拒绝处理")
        if not answer:
            # 只有真正的空输入 (用户看着界面按了回车) 才取默认项.
            chosen = keys[0]
        elif answer.isdigit() and 1 <= int(answer) <= len(keys):
            chosen = keys[int(answer) - 1]
        elif answer in keys:
            chosen = answer
        # 认不出来的输入不是"再问一次", 更不是"那就按默认吧": 用户想说的显然不是这三个
        # 里的任何一个, 猜错的代价不对称, 所以落到 deny.
        if chosen == "once":
            return ApprovalResponse(
                outcome=ApprovalOutcome.APPROVED,
                approval_id=approval.approval_id,
                scope=ApprovalScope.ONCE,
            )
        if chosen == "always":
            return ApprovalResponse(
                outcome=ApprovalOutcome.APPROVED,
                approval_id=approval.approval_id,
                scope=ApprovalScope.WORKSPACE,
            )
        return self._denied(
            approval, "用户拒绝" if chosen == "deny" else "无法识别的输入, 按拒绝处理"
        )

    @staticmethod
    def _denied(approval: ApprovalRequest, note: str) -> ApprovalResponse:
        return ApprovalResponse(
            outcome=ApprovalOutcome.DENIED,
            approval_id=approval.approval_id,
            note=note,
        )
