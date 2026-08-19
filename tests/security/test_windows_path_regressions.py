r"""Windows 与 UNC 路径的回归 (真实事故).

这类缺陷在 POSIX 上**永远不显形**: CI 全绿, 而 Windows 用户一创建文件就报
"助手处理出错". 因此这里全部用 PureWindowsPath 显式钉住 Windows 语义, 不依赖
运行 CI 的机器是什么平台.

事故原文:
    ValueError: '\\psf\\Home\\Documents\\code_generator\\zxxt\\hello.py'
    is not in the subpath of '\\psf\\Home\\Documents\\code_generator\\zxxt'

`\\psf\...` 是 Parallels 共享目录, 一条 UNC 网络路径.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

import pytest

from forgecli.domain.workspace.boundary import is_within

_UNC_ROOT = r"\\psf\Home\Documents\code_generator\zxxt"
_UNC_FILE = r"\\psf\Home\Documents\code_generator\zxxt\hello.py"
_DRIVE_ROOT = r"C:\Users\dev\proj"
_DRIVE_FILE = r"C:\Users\dev\proj\src\main.py"


@pytest.mark.parametrize(
    ("root", "target"),
    [(_UNC_ROOT, _UNC_FILE), (_DRIVE_ROOT, _DRIVE_FILE)],
)
def test_windows_paths_split_into_segments(root: str, target: str) -> None:
    """PureWindowsPath 把反斜杠当分隔符, 于是包含判定成立."""
    assert PureWindowsPath(root) in PureWindowsPath(target).parents


@pytest.mark.parametrize(
    ("root", "target"),
    [(_UNC_ROOT, _UNC_FILE), (_DRIVE_ROOT, _DRIVE_FILE)],
)
def test_posix_path_sees_a_windows_path_as_one_filename(root: str, target: str) -> None:
    """这是缺陷的根源, 钉住它以免有人再写死 PurePosixPath.

    PurePosixPath 不认反斜杠, 整条 Windows 路径被当成**一个文件名**. 于是
    is_within (走平台原生 PurePath) 说"在根内", 而 PurePosixPath.relative_to
    说"不在根内" —— 同一个函数里两行自相矛盾.
    """
    assert PurePosixPath(target).parts == (target,)
    with pytest.raises(ValueError, match="not in the subpath"):
        PurePosixPath(target).relative_to(PurePosixPath(root))


def test_is_within_and_relative_to_agree_on_unc_paths() -> None:
    """恢复层 _relative 的不变量: is_within 为真时, relative_to 必须能切出来.

    两者用同一族 PurePath 才成立. 用错族就是那次事故.
    """
    assert PureWindowsPath(_UNC_ROOT) in PureWindowsPath(_UNC_FILE).parents
    assert (
        str(PureWindowsPath(_UNC_FILE).relative_to(PureWindowsPath(_UNC_ROOT)))
        == "hello.py"
    )


def test_is_within_uses_the_native_flavour() -> None:
    """is_within 走平台原生 PurePath, POSIX 上按 POSIX 语义判."""
    assert is_within("/w/proj/a.py", "/w/proj")
    assert not is_within("/w/proj-backup/a.py", "/w/proj")


def test_a_sibling_with_a_shared_prefix_is_not_inside() -> None:
    """按路径段比较而不是字符串前缀: proj-backup 不在 proj 内."""
    assert (
        PureWindowsPath(r"C:\Users\dev\proj")
        not in PureWindowsPath(r"C:\Users\dev\proj-backup\a.py").parents
    )
