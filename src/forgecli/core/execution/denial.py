"""从命令输出里认出"这次失败是围栏拦的".

围栏在子进程那一侧**没有干净的信号**: Seatbelt 把拒绝写进系统日志, 子进程只拿到 EPERM;
bubblewrap 干脆让路径不存在. 命令自己报出来的就是一句 `Permission denied`, 与"这个文件
本来就只读"长得一模一样.

所以这里是**启发式**, 而且它的方向必须说清楚:

- 它只用来**丰富一条提议**, 从不用来放行任何东西. 认出来了, 用户看到一个填好路径的
  授权提议; 认不出来, 用户看到的还是原始输出 —— 少一条线索, 不多一条通路.
- 因此漏判的代价是"提议没出现", 不是"边界被绕过". 表可以不全, 这是 ADR-0040 说的
  B 类开放表.

为什么值得做: 日志里模型拿到 `policy_denied` 之后连发五条命令满机器找 maven, 每条都是
一次四万 token 的模型调用. 它读不出"命令不存在"和"命令被拿掉了"的区别, 而这个区别决定
了下一步该做什么完全不同的事.

## 本地化

环境现在继承启动 shell, 所以 `LANG` 是用户的. `zh_CN.UTF-8` 下 coreutils 会说"权限不够"
而不是 "Permission denied". 中文那几条一并收进来, 但按定义仍然补不完 —— 见上面那条方向:
补不完只影响提议出不出得来.
"""

from __future__ import annotations

import re

__all__ = ["DENIAL_SIGNATURES", "blocked_paths"]

# 命中其中一条才在这一行上找路径. 只看整行里有没有这些字样, 不解析命令的输出格式 ——
# 每个工具的措辞都不一样, 而它们共用的只有底层 errno 的那句话.
DENIAL_SIGNATURES: tuple[str, ...] = (
    "Permission denied",
    "Operation not permitted",
    "Read-only file system",
    "permission denied",
    "operation not permitted",
    "read-only file system",
    "权限不够",
    "不允许的操作",
    "只读文件系统",
)

# 带引号的路径优先: `touch: cannot touch '/a b/c': Permission denied` 里空格是路径的一
# 部分, 按空白切词会把它切碎.
_QUOTED = re.compile(r"""['"`]([/\\][^'"`]*)['"`]""")
# 裸路径: 到空白, 冒号或逗号为止. 结尾的标点剥掉 —— `mkdir: /x/y: Permission denied`
# 里那个冒号属于消息格式, 不属于路径.
_BARE = re.compile(r"(?<![\w'\"`])(/[^\s'\"`,]+)")
_TRAILING = ":,.;)]}'\"`"


def blocked_paths(output: str) -> tuple[str, ...]:
    """输出里被拒绝的绝对路径, 按出现顺序去重.

    只回**绝对**路径: 相对路径没有工作区上下文就无法判断它落在哪, 而调用方要拿它去和
    围栏的可写集合比对. 拿一个 `build/` 去比, 比出来的结论没有意义.
    """
    found: list[str] = []
    for line in output.splitlines():
        if not any(mark in line for mark in DENIAL_SIGNATURES):
            continue
        for path in _paths_in(line):
            if path not in found:
                found.append(path)
    return tuple(found)


def _paths_in(line: str) -> list[str]:
    quoted = [match.group(1) for match in _QUOTED.finditer(line)]
    if quoted:
        return [_trim(path) for path in quoted]
    return [_trim(match.group(1)) for match in _BARE.finditer(line)]


def _trim(path: str) -> str:
    return path.rstrip(_TRAILING)
