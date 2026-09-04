"""基于真实文件系统的只读视图.

version 只标识本次 ExecutionContext 使用的观察入口，不伪装成 OS 级快照。每次调用创建
新实例；分析读取过的脚本和可执行文件由 FileStateBinding 冻结身份与内容，并在 perform
前重验。glob 等动态集合则在审批后重新 prepare，不能靠一个时间戳声称文件系统已冻结。

realpath 一律解析: 受保护路径判定与目标集合封闭都依赖它, 字符串前缀匹配挡不住符号
链接与 macOS 的 /private 别名.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from charset_normalizer import from_bytes
from wcmatch import glob as wcglob

from forgecli.application.workspace.filesystem_view import (
    FileSystemView,
    PathFacts,
    PathKind,
)
from forgecli.infrastructure.workspace.ignore_oracle import ignore_predicate

__all__ = ["OsFileSystemView"]


class OsFileSystemView(FileSystemView):
    def __init__(self, *, version: str | None = None) -> None:
        self._version = version or f"fs-{time.time_ns()}"

    @property
    def version(self) -> str:
        return self._version

    def facts(self, path: str) -> PathFacts:
        target = Path(path)
        try:
            link_stat = target.lstat()
        except OSError:
            # 对象还不存在. realpath 仍然要算 —— 只是要靠**已存在的父目录**去算.
            # 早先这里直接回字面路径, 于是工作区里一个 `link -> ~/.ssh` 加上写入
            # `link/new_file` 就被判成工作区内路径, 而真正的写入会跟着父目录的
            # 符号链接落到 ~/.ssh 里.
            return PathFacts(
                path=path, realpath=_resolved_parent(target), kind=PathKind.MISSING
            )

        is_symlink = target.is_symlink()
        try:
            real = str(target.resolve(strict=False))
        except OSError:
            real = path
        link_target = None
        if is_symlink:
            try:
                link_target = str(target.readlink())
            except OSError:
                link_target = None
        try:
            # 身份、大小与 mtime 描述实际会被读取/执行的最终对象；链接本身另由
            # is_symlink/link_target 表达。混用 lstat 元数据和目标内容会让所有符号链接
            # 可执行文件都看起来像“短读”。
            object_stat = target.stat()
        except OSError:
            object_stat = link_stat

        return PathFacts(
            path=path,
            realpath=real,
            kind=_kind_of(target),
            size=object_stat.st_size,
            mtime_ns=object_stat.st_mtime_ns,
            file_identity=f"{object_stat.st_dev}:{object_stat.st_ino}",
            mode=object_stat.st_mode,
            is_symlink=is_symlink,
            link_target=link_target,
        )

    def read_text(self, path: str, *, max_bytes: int) -> str:
        return decode_text(self.read_bytes(path, max_bytes=max_bytes))

    def read_text_if_text(self, path: str, *, max_bytes: int) -> str | None:
        raw = self.read_bytes(path, max_bytes=max_bytes)
        if looks_binary(raw):
            return None
        return decode_text(raw)

    def read_bytes(self, path: str, *, max_bytes: int) -> bytes:
        try:
            with Path(path).open("rb") as handle:
                return handle.read(max_bytes)
        except OSError:
            return b""

    def list_dir(self, path: str) -> tuple[str, ...]:
        try:
            return tuple(sorted(entry.name for entry in Path(path).iterdir()))
        except OSError:
            return ()

    def expand_glob(
        self,
        pattern: str,
        *,
        root: str,
        max_results: int | None = None,
        skip_ignored: bool = True,
    ) -> tuple[str, ...]:
        candidate = Path(pattern)
        if candidate.is_absolute():
            # 绝对 glob: 从根锚点展开, 保留用户写的那一段作为 pattern.
            anchor = Path(candidate.anchor)
            relative = candidate.relative_to(anchor)
            return _walk_glob(anchor, str(relative), max_results, skip_ignored)
        return _walk_glob(Path(root), pattern, max_results, skip_ignored)

    def is_ignored(self, path: str, *, root: str) -> bool:
        normalized = path.replace("\\", "/")
        base = root.replace("\\", "/").rstrip("/")
        relative = (
            normalized[len(base) + 1 :]
            if normalized.startswith(f"{base}/")
            else normalized
        )
        return ignore_predicate(base)(relative)


def _resolved_parent(target: Path) -> str:
    """把不存在的对象锚到最近的已存在祖先上, 再拼回剩下的段.

    `Path.resolve(strict=False)` 已经会解析已存在的那一段, 这里额外兜住它抛错的情形
    (循环链接, 权限不足): 那时宁可返回字面路径也不能返回空.
    """
    try:
        return str(target.resolve(strict=False))
    except OSError:
        return str(target)


def looks_binary(raw: bytes) -> bool:
    """NUL 字节即判定二进制. 与 git, grep 和 ripgrep 用的是同一条判据.

    判它不是为了省时间, 是为了省额度: `search_text` 有 2000 个文件的扫描上限和 200 条
    命中上限, 一个 Maven 项目的 `target/` 里几千个 `.class` 解码成替换字符之后照样会
    产生"命中", 于是额度被喂给了噪音, 而"这个词不在代码里"这个结论建立在没扫到源码上.
    """
    return b"\x00" in raw[:8192]


def decode_text(raw: bytes) -> str:
    """先按 utf-8 试, 失败才去猜编码.

    原先是无条件 `decode("utf-8", errors="replace")`. 一个 GBK 编码的 Java 源文件在那种
    读法下变成一串替换字符, `search_text` 搜"当前数据源"命中 0 处, 然后如实告诉模型
    "这是确定的空结果" —— 静默漏掉, 没有任何一层会说话. 国内 Java 仓库里 GBK 源文件
    并不罕见.

    **顺序不能反.** charset-normalizer 是统计判断, 在短文件上会猜错 (实测 30 字节的
    GBK 片段被判成 cp949); 而绝大多数源文件本来就是 utf-8, 严格解码成功就是确定答案.
    所以只在 utf-8 解不通时才让它上场, 它猜错的代价此时也只是从一种乱码换成另一种.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    best = from_bytes(raw).best()
    if best is None:
        return raw.decode("utf-8", errors="replace")
    return str(best)


def _literal_prefix(pattern: str) -> tuple[str, str]:
    """把 pattern 切成 (不含通配符的前缀目录, 余下的 pattern).

    存在的理由是一次实测: 分析 `sed` 脚本里的 `/api/orders/**` 时, 展开层拿它当目标
    候选去 glob, 而这里从 `/` 开始 `os.walk` 整块磁盘 —— 一次裁决耗时 183 秒
    (forge-20260904-133236 的 step 51), 同一会话另一条 `python3 -c` 耗时 99 秒. 模型和
    用户都只会看到"卡住了", 没有任何一层会说是 glob 在扫全盘.

    前缀里的每一段都是确定的目录名, 直接并进遍历起点即可; `/api/orders` 不存在时
    `os.walk` 立刻收工, 而不是把整棵树走完再逐条比对.

    最后一段永远留给 pattern: 整条 pattern 都不含通配符时 (`docs/readme.md`), 起点是
    它的父目录, 匹配的是文件名 —— 而不是把它整条当成起点去遍历一个文件.

    `.` 与 `..` 不并入: 它们要么无意义, 要么会把起点带出调用方给的 root, 而 root 正是
    这次展开的边界.
    """
    segments = pattern.replace("\\", "/").split("/")
    absorbed = 0
    for segment in segments[:-1]:
        if segment in ("", ".", "..") or any(char in segment for char in "*?["):
            break
        absorbed += 1
    if absorbed == 0:
        return "", pattern
    return "/".join(segments[:absorbed]), "/".join(segments[absorbed:])


def _walk_glob(
    base: Path, pattern: str, max_results: int | None, skip_ignored: bool
) -> tuple[str, ...]:
    """自己走目录而不是用 `Path.glob`, 只为了一件事: 剪枝发生在遍历时.

    `Path.glob` 会把 `.venv` 与 `node_modules` 整棵走完再把结果交出来, 调用方此时才有
    机会丢掉它们 —— 而额度在那之前就已经用光了. `os.walk` 允许原地改写 dirs 来砍掉整棵
    子树, 被砍掉的目录连 stat 都不会发生.

    两件判断各自交给它该去的地方:

    - **"这条路径要不要跳过"** 问 `ignore_oracle` —— git 仓库里就是 libgit2 自己那套
      判断, 覆盖每一层 .gitignore, .git/info/exclude 与全局 excludesFile.
    - **"这条路径中不中用户给的 glob"** 交给 `wcmatch.globmatch`. 它的 `*` 不跨 `/`,
      `**` 跨整棵树, 与 bash 和 `Path.glob` 一致.

    遍历起点按 `_literal_prefix` 下沉到 pattern 的确定前缀, 但**忽略判断仍然按调用方
    给的 base 提问**: 判据是"这条路径在这个仓库里要不要跳过", 换个起点重新提问会让
    libgit2 拿到一条它无法归位的相对路径. 起点是性能, base 是语义, 两者不能混。

    排序固定: 目录遍历顺序在不同文件系统上不一样, 而顺序一变 plan_hash 就变, 上一次
    批准也就绑不住这一次.
    """
    limit = max_results if max_results is not None and max_results >= 0 else None
    if limit == 0:
        return ()
    flags = wcglob.GLOBSTAR | wcglob.DOTGLOB
    collected: list[str] = []
    root = str(base)
    ignored = ignore_predicate(root) if skip_ignored else None
    absorbed, remainder = _literal_prefix(pattern)
    start = os.path.join(root, *absorbed.split("/")) if absorbed else root
    outer = f"{absorbed}/" if absorbed else ""
    try:
        walker = os.walk(start, followlinks=False)
        for current, directories, files in walker:
            relative_dir = os.path.relpath(current, start)
            inner = "" if relative_dir == "." else f"{relative_dir}/"
            if ignored is not None:
                # 原地改写才有效: os.walk 读的就是这个列表, 换成新列表它看不见.
                # 砍掉一个目录, 它整棵子树连 stat 都不会发生.
                directories[:] = [
                    name
                    for name in directories
                    if not ignored(f"{outer}{inner}{name}/")
                ]
            for name in (*directories, *files):
                entry = f"{inner}{name}"
                if ignored is not None and ignored(f"{outer}{entry}"):
                    continue
                if not wcglob.globmatch(entry, remainder, flags=flags):
                    continue
                collected.append(os.path.join(start, entry))
                if limit is not None and len(collected) >= limit:
                    return tuple(sorted(collected))
    except (OSError, ValueError):
        return tuple(sorted(collected))
    return tuple(sorted(collected))


def _kind_of(target: Path) -> PathKind:
    # 顺序有意义: symlink 先按最终目标归类, 链接本身的身份由 is_symlink 字段表达.
    if target.is_dir():
        return PathKind.DIRECTORY
    if target.is_file():
        return PathKind.FILE
    return PathKind.OTHER
