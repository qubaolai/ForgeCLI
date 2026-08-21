"""受保护路径与目录授权的已修复绕过.

两条都不是"少拦一次": 前者把工作区设成主目录就解除了凭证保护, 后者让 `/add-dir` 的
只读授权实际可写.
"""

from __future__ import annotations

import os
from pathlib import Path

from forgecli.domain.security.protected_paths import (
    PathVerdict,
    ProtectedCategory,
    ProtectedPathPolicy,
    ProtectedRoot,
)
from forgecli.infrastructure.workspace.os_filesystem_view import OsFileSystemView


def _policy(home: str, *, exemptions: tuple[str, ...]) -> ProtectedPathPolicy:
    return ProtectedPathPolicy(
        roots=(
            ProtectedRoot(
                f"{home}/.ssh", ProtectedCategory.CREDENTIAL_STORE, deny_read=True
            ),
            ProtectedRoot(f"{home}/.zshrc", ProtectedCategory.STARTUP_CONFIG),
            ProtectedRoot(f"{home}/src/forge", ProtectedCategory.FORGE_RUNTIME),
        ),
        workspace_exemptions=exemptions,
    )


def test_a_home_directory_workspace_does_not_unprotect_credentials() -> None:
    """把 `~` 当工作区打开时, 豁免只能覆盖 FORGE_RUNTIME, 不能覆盖凭证与启动配置.

    早先 classify() 一命中豁免就整体返回 None, 于是 `~/.ssh` 与 `~/.zshrc` 全部变成
    未受保护 —— 一条为"用 Forge 开发 Forge"准备的豁免顺手关掉了所有保护.
    """
    home = "/home/dev"
    policy = _policy(home, exemptions=(home,))

    assert policy.verdict_for_read(f"{home}/.ssh/id_rsa") is PathVerdict.DENY_READ
    assert policy.verdict_for_write(f"{home}/.ssh/authorized_keys") is (
        PathVerdict.DENY_WRITE
    )
    assert policy.verdict_for_write(f"{home}/.zshrc") is PathVerdict.DENY_WRITE


def test_the_forge_runtime_exemption_still_works() -> None:
    """豁免存在的唯一理由: editable 安装把包目录指到源码仓库, 用 Forge 开发 Forge."""
    home = "/home/dev"
    policy = _policy(home, exemptions=(f"{home}/src/forge",))
    assert policy.verdict_for_write(f"{home}/src/forge/pyproject.toml") is (
        PathVerdict.ALLOWED
    )


def test_an_exemption_does_not_leak_to_a_sibling_category() -> None:
    """豁免命中的是 FORGE_RUNTIME, 但同一路径下若另有凭证规则, 后者仍然生效."""
    home = "/home/dev"
    policy = ProtectedPathPolicy(
        roots=(
            ProtectedRoot(f"{home}/src/forge", ProtectedCategory.FORGE_RUNTIME),
            ProtectedRoot(
                f"{home}/src/forge/.secrets",
                ProtectedCategory.CREDENTIAL_STORE,
                deny_read=True,
            ),
        ),
        workspace_exemptions=(f"{home}/src/forge",),
    )
    assert policy.verdict_for_read(f"{home}/src/forge/.secrets/token") is (
        PathVerdict.DENY_READ
    )


def test_a_missing_file_resolves_through_a_symlinked_parent(tmp_path: Path) -> None:
    """写入不存在的 `link/new`, 而 `link -> secrets`: realpath 必须指向真实目标.

    早先 facts() 在 lstat 失败后直接回字面路径, 于是判定看到的是工作区内的路径, 而真正
    的写入会跟着父目录的符号链接落到别处.
    """
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    os.symlink(secrets, workspace / "link")

    facts = OsFileSystemView().facts(str(workspace / "link" / "new_file"))

    assert facts.kind.value == "missing"
    assert facts.realpath == str((secrets / "new_file").resolve())


def test_a_missing_file_under_a_real_parent_keeps_its_own_path(tmp_path: Path) -> None:
    """没有符号链接时结果不变 —— 修复不该改变正常路径的判定."""
    facts = OsFileSystemView().facts(str(tmp_path / "plain.txt"))
    assert facts.realpath == str((tmp_path / "plain.txt").resolve())


def test_symlink_metadata_describes_the_content_that_will_be_read(
    tmp_path: Path,
) -> None:
    target = tmp_path / "real-tool"
    target.write_bytes(b"executable-content")
    link = tmp_path / "tool"
    os.symlink(target, link)

    facts = OsFileSystemView().facts(str(link))

    assert facts.is_symlink is True
    assert facts.realpath == str(target)
    assert facts.size == len(b"executable-content")
    assert facts.file_identity == f"{target.stat().st_dev}:{target.stat().st_ino}"
