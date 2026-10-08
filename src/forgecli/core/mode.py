"""会话输入的来源枚举: 这一行是谁给的.

模式不再住在这里: ADR-0055 之后模式是 `core/permissions.py` 的 `PermissionMode`. 
早先这里还有 ``SessionMode`` (隔离档 x 审批档两轴) 与它的预设表, 随权限模块重写一起删除.

设计约束: 不可变, 值相等, 无副作用; 取值会落盘 (origin 进 user_message 事件), 因此
不可随意改写.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["InputOrigin"]


class InputOrigin(Enum):
    """这一行输入是谁给的。

    人类说的话与程序合成的话必须分得开: 计划评审的"同意并执行"会以本轮的名义再入一次
    turn, 那句话是**程序**给的 (plan_review), 不能算用户又说了一遍。

    默认值是 PROGRAM: 新增一个调用方时, 忘记传参的后果是少算一次人类输入, 不是多算。

    早先还有一档 ``TTY_USER``, 承载人工 Shell 的特权 (ADR-0017 §2)。人工 Shell 随终端
    入口一起删除之后, 没有任何代码路径能再产出它, 所以它也不该继续留在这张表里 —— 一个
    永远取不到的权限档只会让读的人以为它还在用。老 events.jsonl 里可能留着
    ``"tty_user"`` 这个字符串, 但没有任何地方把它解析回枚举。ADR-0045 把终端入口加了
    回来, 但它取的是 ``CLI_USER``: 那一档只标"这句话是谁说的", 不带任何权限。
    """

    # 经本地 Web 控制面的认证用户。
    WEB_USER = "web_user"
    # 经终端交互式会话的用户 (ADR-0045)。与 WEB_USER 分开记, 是因为它进 events.jsonl:
    # 事后回看一条会话时, "这句话是在浏览器里说的还是在终端里说的"是查不回来的事实,
    # 合成一档就等于把它丢了。两者在裁决与提示词上没有任何差别 —— 早先 TTY_USER 承载的
    # 人工 Shell 特权不随本档回来。
    CLI_USER = "cli_user"
    PROGRAM = "program"
