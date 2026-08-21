"""PolicyContext: 一次裁决的策略侧输入 (ADR-0013 §13).

用户意图是**约束**, 不是无限授权: "运行测试"可以说明测试脚本与任务相关, 但不能自动
授权生产部署, 远程写入或读取凭证. 因此上下文里带的是意图摘要与 turn 身份, 供分类器
判断"意图是否匹配", 而不是一个"用户已经同意"的布尔位.

execution_profile_hash 与 policy_version 一并绑定: 二者任一变化, 旧裁决, 旧缓存和
待审批请求全部失效 (ADR-0014 §4.1).
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.execution.fence import FencePolicy
from forgecli.domain.intents import SessionMode
from forgecli.domain.security.vocabulary import POLICY_VERSION
from forgecli.domain.tool.hashing import digest

__all__ = ["PolicyContext"]


@dataclass(frozen=True)
class PolicyContext:
    mode: SessionMode
    session_id: str
    turn_id: str
    execution_profile_hash: str
    # 学习规则绑定它: workspace 范围的规则不能跨项目命中.
    workspace_id: str = ""
    user_intent_summary: str = ""
    policy_version: str = POLICY_VERSION
    # 非交互环境下 ASK 不能转 Allow, 只能保持 pending 或取消 (ADR-0013 §4).
    interactive: bool = True
    # 本次执行的围栏边界, 由 mode 编译 (ADR-0030 决策 4). None = 没有边界信息.
    fence: FencePolicy | None = None
    # 围栏是不是真的立起来了 —— 来自 Provider 的行为自测, 不是"装了就算".
    # 边界存在但没被强制时, 需要围栏才能自动放行的能力落回 ASK (ADR-0030 决策 5).
    confined: bool = False

    @property
    def intent_scope_hash(self) -> str:
        """意图范围哈希, 进风险缓存键: 换了任务就不该复用上一个任务的分析结论."""
        return digest(
            {
                "turn_id": self.turn_id,
                "user_intent_summary": self.user_intent_summary,
                "mode": self.mode,
            }
        )
