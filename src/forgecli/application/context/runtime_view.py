"""当前运行事实的渲染: 请求的第 [4] 层 (ADR-0041 决策 1 / 决策 2).

## 为什么它进缓存前缀而不是请求尾部

这一层的内容每轮都**可能**变 (换档, `/add-dir`), 直觉上该放尾部, 免得一变就作废后面的
全部内容. 但它的变化源只有那两个, 而两者都会同时改变工具目录 —— `catalog_for(mode)`
一变, 排在整条请求最前面的 tool schema 就变了, 后面本来就全部作废.

也就是说: **它的变化是工具目录变化的子集.** 放进前缀的额外成本恰好为零; 放到尾部则是
每轮重建约四百 token, 换不来任何东西.

一个例外要守住: 每轮都变的时间戳绝不能进这里, 那会让前缀永远命中不了. 本阶段只有会话内
稳定的事实, 没有时间字段.

正文在 `application/prompt/templates/runtime/`, 不在这里拼字符串: ADR-0031 要求 Forge
撰写的, 会进模型上下文的文字全部住在那一个目录 —— "这几句彼此矛盾没有"只有并排放着才
看得出来, 而运行事实的措辞与提示词的措辞正是最容易互相矛盾的两处.
"""

from __future__ import annotations

from forgecli.application.context.runtime_facts import RuntimeFacts
from forgecli.application.prompt.template_renderer import render_runtime
from forgecli.domain.execution.fence import FencePolicy
from forgecli.domain.intents import SessionMode

__all__ = ["render_runtime_context"]


def render_runtime_context(
    facts: RuntimeFacts, *, mode: SessionMode, fence: FencePolicy | None
) -> str:
    """渲染这一轮的运行上下文."""
    return render_runtime(
        # 加载提示词模板: runtime_facts.md.j2
        "runtime_facts",
        platform=facts.platform,
        shell_kind=facts.shell_kind,
        working_directory=facts.working_directory,
        workspace_root=facts.workspace_roots[0],
        git_repository="yes" if facts.git_repository else "no",
        extra_roots=facts.workspace_roots[1:],
    )
