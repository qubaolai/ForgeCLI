"""可执行文件身份 (ADR-0013 §5.1).

**所有 Allow 都必须绑定可执行文件身份, 而不是命令名字符串.** 理由是一句话就能说清的:
工作区里放一个叫 `cat` 的文件, 或者改掉 `venv/bin/python`, 命令名完全没变, 跑起来的却
是 Agent 自己写的代码.

因此身份至少要包含绝对 realpath, 文件标识, 内容哈希, 以及"这个文件 Agent 自己能不能
改". 最后一项决定了它能不能命中普通 Allow: 可写目录里的 executable, shim 和解释器一律
按 EXECUTE_SCRIPT 走内容分析, 不能因为名字和系统工具一样就继承系统工具的授权.

解释器身份也不足以证明运行时不变: Python 的 `.pth` / `sitecustomize`, Node 的 shim 与
package 入口都能改变行为, 所以 runtime_config_hash 与 dependency_hash 一并绑定.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from forgecli.domain.tool.hashing import digest

__all__ = ["ExecutableIdentity", "TrustZone"]


class TrustZone(Enum):
    """可执行文件所在位置的信任级别."""

    SYSTEM = "system"  # 受控 PATH 里的系统目录
    TOOLCHAIN = "toolchain"  # 用户显式配置的工具链 (虚拟环境等), 可写
    WORKSPACE = "workspace"  # 工作区内, Agent 可写
    UNKNOWN = "unknown"

    @property
    def agent_writable(self) -> bool:
        return self in (TrustZone.TOOLCHAIN, TrustZone.WORKSPACE, TrustZone.UNKNOWN)


@dataclass(frozen=True)
class ExecutableIdentity:
    requested_token: str
    absolute_path: str
    realpath: str
    file_identity: str
    size: int
    mtime_ns: int
    content_hash: str
    content_complete: bool
    trust_zone: TrustZone
    interpreter_chain: tuple[str, ...] = ()
    runtime_config_hash: str | None = None
    dependency_hash: str | None = None

    @property
    def writable_by_agent(self) -> bool:
        return self.trust_zone.agent_writable

    @property
    def eligible_for_plain_allow(self) -> bool:
        """能不能凭命令语义命中普通 Allow.

        Agent 可写的位置一律不行: `./cat`, 被改过的 `venv/bin/python` 和
        `node_modules/.bin/*` 必须按脚本执行分析, 而不是继承同名系统命令的授权.
        """
        return not self.writable_by_agent and self.content_complete

    @property
    def identity_hash(self) -> str:
        """裁决与执行绑定同一个值. 执行前重算, 不一致即授权失效."""
        return digest(
            {
                "realpath": self.realpath,
                "file_identity": self.file_identity,
                "size": self.size,
                "mtime_ns": self.mtime_ns,
                "content_hash": self.content_hash,
                "content_complete": self.content_complete,
                "interpreter_chain": self.interpreter_chain,
                "runtime_config_hash": self.runtime_config_hash,
                "dependency_hash": self.dependency_hash,
            }
        )

    @classmethod
    def unresolved(cls, token: str) -> ExecutableIdentity:
        """找不到可执行文件时的保守身份: 未知信任区, 不具备普通 Allow 资格."""
        return cls(
            requested_token=token,
            absolute_path="",
            realpath="",
            file_identity="",
            size=0,
            mtime_ns=0,
            content_hash="",
            content_complete=False,
            trust_zone=TrustZone.UNKNOWN,
        )
