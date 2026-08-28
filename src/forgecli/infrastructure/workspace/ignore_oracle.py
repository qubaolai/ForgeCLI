"""这条路径要不要跳过 —— 问 git, 不自己列名单.

原先这里是一个写死的 19 项目录名集合 (`.git`, `node_modules`, `target`, `dist` ...) 加上
"只读工作区根那一份 .gitignore"的手写解析. 两件事都是在重新实现 git 已经做完的判断, 而且
都不完整:

- 名单认不出这个项目自己的生成目录. Go 的 `vendor/`, Elixir 的 `_build/`, 生成的
  protobuf 目录都不在里面. 而 `search_text` 有 2000 个文件的扫描上限, 额度被生成物
  吃光之后, "这个词不在代码里"这个结论建立在没扫到源码上, 并且会原样回给模型.
- git 在**每一层目录**都认 .gitignore, 还认 `.git/info/exclude` 与全局
  `core.excludesFile`. 只读根上那一份, 漏掉的部分同样是静默的.

`pygit2` 是 libgit2 的绑定, `Repository.path_is_ignored()` 就是 git 自己那套判断: 四种
来源全覆盖, 连 `.git/` 目录本身都算在内. 实测 2000 次调用 38ms, 走目录时逐条问得起.
进程内调用, 不起子进程 —— 那一点很要紧, 起子进程会把一次纯读取变成 EXECUTE_SHELL,
而检索是调用最频繁的动作.

## 不是 git 仓库的目录

`/add-dir` 加进来的目录常常不是仓库, 此时没有任何**权威**来源可问. ripgrep, ruff 与
black 在这种情况下各自写死一份名单: 这份名单是产品判断而不是事实, PyPI 上没有它的包.

这里一度是借 `watchfiles.DefaultFilter.ignore_dirs` 的 —— 借一份别人在维护的, 听着比自己
列一份体面. 但那是拿一个装着 Rust 二进制的文件监视器换 11 个字符串常量, 而且换来的名单
取舍还和我们相反 (监视器故意不忽略 `dist/` `target/` `build/`, 它要看见构建产物变化).
ADR-0040 决策 11 把这类依赖判为无消费者: 我们从不监视文件, 只是读它一个类属性.

所以名单写在下面. 它是 ADR-0040 说的 B 类非承重表 —— 漏一项只是多扫几个文件, 不会让
"这个词不在代码里"变成假结论, 方向是安全的.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

import pygit2

__all__ = ["ignore_predicate"]

# 这份名单是这份代码里仅剩的硬编码, 而且只在"目录不是 git 仓库"时才生效 —— 是仓库的话
# git 说了算, 一个字都用不上. 缩到这个范围是刻意的: 一份会漂的名单, 作用域越小越不疼.
#
# 前一组是版本控制, 工具缓存与依赖目录, 上游取舍普遍一致; 后一组是构建输出, 我们要跳过
# 它们是因为 search_text 有 2000 个文件的扫描上限, 额度被生成物吃光就等于没扫源码.
_FALLBACK_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".hypothesis",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        "out",
        ".next",
        "vendor",
    }
)


def ignore_predicate(root: str) -> Callable[[str], bool]:
    """返回一个"这条相对路径要不要跳过"的判断.

    入参是相对 root 的路径 (目录带尾斜杠), 与 gitwildmatch 的约定一致.
    """
    repository = _repository_at(root)
    if repository is None:
        return _fallback
    # libgit2 按**仓库根**解释路径, 而这次扫描的起点可能是仓库里的某个子目录
    # (search_text(path="src/forgecli") 是常态). 不补这段前缀的话, `src/x/.venv` 会被
    # 当成仓库根下的 `.venv` 去查 —— 查得到是巧合, 查不到是静默漏判.
    prefix = _prefix_within(repository, root)
    return lambda relative: _ask_git(repository, f"{prefix}{relative}")


@lru_cache(maxsize=32)
def _repository_at(root: str) -> pygit2.Repository | None:
    """root 所属的 git 仓库, 不是仓库就是 None.

    缓存是因为一次 prepare 里同一个 root 会被问很多次, 而打开仓库要读 .git 下若干文件.
    上限 32: 工作区根的数量是个位数, 留出余量即可.
    """
    try:
        discovered = pygit2.discover_repository(root)
    except (pygit2.GitError, ValueError, OSError):
        return None
    if discovered is None:
        return None
    try:
        return pygit2.Repository(discovered)
    except (pygit2.GitError, ValueError, OSError):
        return None


def _prefix_within(repository: pygit2.Repository, root: str) -> str:
    """从仓库工作树根到 root 的那一段, 带尾斜杠; root 就是仓库根时为空串."""
    workdir = (repository.workdir or "").rstrip("/")
    normalized = root.rstrip("/")
    if not workdir or normalized == workdir:
        return ""
    if normalized.startswith(f"{workdir}/"):
        return f"{normalized[len(workdir) + 1 :]}/"
    # root 在工作树之外 (符号链接指出去, 或 /add-dir 加了个无关目录). 仓库的规则对它
    # 不适用, 按不跳过处理.
    return ""


def _ask_git(repository: pygit2.Repository, relative: str) -> bool:
    """libgit2 的判断. 它按仓库根解释路径, 所以传进来的必须是仓库内相对路径.

    抛错时按"不跳过"处理: 少跳一个目录只是多扫一些文件, 而多跳一个是漏搜 —— 后者会让
    "这个词不在代码里"变成假结论, 前者不会.
    """
    try:
        return bool(repository.path_is_ignored(relative))
    except (pygit2.GitError, ValueError, OSError):
        return False


def _fallback(relative: str) -> bool:
    """不是 git 仓库时的判断: 借来的目录名单, 按**路径段**匹配.

    按段而不是子串: `target` 作为一段要跳过, 而 `src/targeting.py` 不该被误伤.
    """
    return any(part in _FALLBACK_DIRS for part in relative.split("/") if part)
