"""被执行的脚本正文快照 (ADR-0027, ADR-0030 决策 7).

**这不是"分析脚本做了什么".** ADR-0030 之后正文的风险模式匹配整个删掉了 —— 脚本跑在
围栏里, 不必读它的正文找危险. 留下来的只有一件事: **记住分析当时读到的是哪一份正文.**

三个下游都要它, 而且都不能自己再读一次文件 (那就是又开一个 TOCTOU 窗口):

- 审批界面逐字展示;
- 学习规则绑内容哈希 —— 少了它, `bash deploy.sh` 学到的永久放行只绑命令行那一串字,
  deploy.sh 随后改成什么都照样命中. 一条永久放行 + 一个可自由改写的文件, 正好是
  "批准过的脚本"这种类别式授权最危险的形态;
- 审计记录被拦下的是哪段代码.

围栏替代不了这三件事: 它管的是子进程能碰到什么, 不管"批准的和跑起来的是不是同一份".
"""

from __future__ import annotations

from dataclasses import dataclass

from forgecli.domain.tool.hashing import digest_text

__all__ = ["ScriptSnapshot"]


@dataclass(frozen=True)
class ScriptSnapshot:
    """一份被读取过的脚本正文快照.

    origin 区分 inline / heredoc / file / encoded: "命令里写着的一行"与"磁盘上那个
    文件"对用户是完全不同的两件事, 界面必须说清.
    """

    language: str
    origin: str
    source: str
    path: str | None = None

    @property
    def content_hash(self) -> str:
        return digest_text(self.source)
