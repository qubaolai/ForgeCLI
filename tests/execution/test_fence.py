"""围栏策略与 Provider 的行为 (ADR-0030 决策 1 / 3 / 4).

这一组的重点不是"代码跑通了", 而是**围栏真的拦得住**. 所以 Provider 那几条不是断言
返回值, 而是真的起一个子进程去做被禁止的操作, 确认它失败且没有留下痕迹 —— 一个只
检查 argv 拼对了的测试, 在 profile 语法写错时会全绿.
"""

from __future__ import annotations

import platform
import subprocess
import tempfile
from pathlib import Path

import pytest

from forgecli.application.tools.command_executor import CommandRequest
from forgecli.domain.execution.fence import FencePolicy, fence_for
from forgecli.domain.intents import SessionMode
from forgecli.infrastructure.execution.local_command_executor import (
    LocalCommandExecutor,
)
from forgecli.infrastructure.execution.sandbox.none import NoSandboxProvider
from forgecli.infrastructure.execution.sandbox.seatbelt import SeatbeltProvider
from forgecli.infrastructure.execution.sandbox.selection import select_provider
from forgecli.infrastructure.execution.sandbox.wsl2 import (
    Wsl2Provider,
    windows_to_wsl_path,
)
from forgecli.infrastructure.execution.sandboxed_command_executor import (
    SandboxedCommandExecutor,
)

on_macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="Seatbelt 只在 macOS 上"
)


# ---- 模式编译 ----


def test_plan_mode_has_no_writable_workspace_root() -> None:
    policy = fence_for(SessionMode.PLAN, workspace_roots=("/ws",))
    assert policy.writable_roots == ()
    assert policy.read_only is True


def test_instance_temp_root_does_not_make_plan_mode_writable() -> None:
    """临时目录可写, 但 plan 档仍然是只读的.

    这两件事必须分开: 并进 writable_roots 之后 read_only 会永远是 False, 而 plan 档的
    全部意义就是那个 True.
    """
    policy = fence_for(
        SessionMode.PLAN, workspace_roots=("/ws",), instance_temp_root="/tmp/inst"
    )
    assert policy.read_only is True
    assert "/tmp/inst" in policy.all_writable


@pytest.mark.parametrize(
    ("mode", "writable", "network", "automatic_shell"),
    [
        (SessionMode.PLAN, (), False, False),
        (SessionMode.ACCEPT_EDITS, ("/ws",), False, False),
        (SessionMode.AUTO, ("/ws",), False, True),
        (SessionMode.FULL_ACCESS, ("/ws",), True, True),
    ],
)
def test_each_mode_compiles_to_its_own_boundary(
    mode: SessionMode,
    writable: tuple[str, ...],
    network: bool,
    automatic_shell: bool,
) -> None:
    policy = fence_for(mode, workspace_roots=("/ws",))
    assert policy.writable_roots == writable
    assert policy.network_allowed is network
    assert policy.automatic_shell is automatic_shell


def test_readonly_roots_stay_readable_but_not_writable() -> None:
    """不带 --write 的 /add-dir: 仍是工作区根, 但不进可写集合."""
    policy = fence_for(
        SessionMode.AUTO,
        workspace_roots=("/ws", "/ref"),
        readonly_roots=("/ref",),
    )
    assert policy.writable_roots == ("/ws",)


def test_policy_hash_changes_with_any_field() -> None:
    base = FencePolicy(writable_roots=("/ws",))
    assert base.policy_hash != FencePolicy(writable_roots=("/other",)).policy_hash
    assert (
        base.policy_hash
        != FencePolicy(writable_roots=("/ws",), network_allowed=True).policy_hash
    )
    assert (
        base.policy_hash
        != FencePolicy(writable_roots=("/ws",), automatic_shell=True).policy_hash
    )


# ---- Provider 行为 ----


def test_no_sandbox_provider_reports_nothing_blocked() -> None:
    """如实报告"什么都拦不住". 假装围住了比没有围栏危险得多."""
    report = NoSandboxProvider().self_test()
    assert report.confined is False
    assert report.outside_write_blocked is False


def test_no_sandbox_provider_passes_argv_through() -> None:
    argv = NoSandboxProvider().wrap(("/bin/echo", "hi"), FencePolicy())
    assert argv == ("/bin/echo", "hi")


@on_macos
def test_seatbelt_self_test_confirms_all_three_boundaries() -> None:
    provider = SeatbeltProvider()
    if not provider.available():
        pytest.skip("sandbox-exec 不可用")
    report = provider.self_test()
    assert report.failures == ()
    assert report.confined is True


@on_macos
def test_seatbelt_blocks_write_outside_workspace_for_real() -> None:
    provider = SeatbeltProvider()
    if not provider.available():
        pytest.skip("sandbox-exec 不可用")
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        escaped = Path(tmp) / "escaped.txt"
        policy = FencePolicy(writable_roots=(str(workspace),))
        argv = provider.wrap(("/bin/sh", "-c", f"echo x > {escaped}"), policy)
        completed = subprocess.run(argv, capture_output=True, timeout=15, check=False)
        assert completed.returncode != 0
        assert not escaped.exists(), "围栏外的文件竟然落地了"


@on_macos
def test_seatbelt_allows_write_inside_workspace() -> None:
    """反向确认. 少了这条, 一个"什么都拦"的坏 profile 会被当成完美围栏."""
    provider = SeatbeltProvider()
    if not provider.available():
        pytest.skip("sandbox-exec 不可用")
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        target = workspace / "inside.txt"
        policy = FencePolicy(writable_roots=(str(workspace),))
        argv = provider.wrap(("/bin/sh", "-c", f"echo ok > {target}"), policy)
        completed = subprocess.run(argv, capture_output=True, timeout=15, check=False)
        assert completed.returncode == 0
        assert target.read_text().strip() == "ok"


@on_macos
def test_seatbelt_profile_uses_allow_default_with_targeted_deny() -> None:
    """profile 形态是实测定下来的 (ADR-0030 实测记录第二节).

    `(deny default)` 与 `(deny file-read*)` + 系统 allowlist 两种形态都会让子进程
    SIGABRT. 这条钉住形态, 免得有人"顺手收紧一下"把整个围栏改成起不来.
    """
    profile = SeatbeltProvider().profile_for(FencePolicy(writable_roots=("/ws",)))
    assert "(allow default)" in profile
    assert "(deny default)" not in profile
    assert "(deny file-write*)" in profile
    assert "(deny network*)" in profile


def test_selection_returns_a_provider_whose_report_matches_its_name() -> None:
    """名字与能力必须一致: 装了 bwrap 但 userns 被关掉时, 名字对而围栏不存在."""
    provider, report = select_provider()
    assert report.provider == provider.name


def test_selection_on_unknown_platform_falls_back_to_no_sandbox() -> None:
    provider, report = select_provider(system="Plan9")
    assert provider.name == "none"
    assert report.confined is False


# ---- 执行接缝 ----


def test_executor_fails_closed_when_the_policy_is_missing() -> None:
    """漏传策略是装配错误, 不是"没策略就不围"."""
    executor = SandboxedCommandExecutor(LocalCommandExecutor(), NoSandboxProvider())
    outcome = executor.run(
        CommandRequest(
            argv=("/bin/echo", "hi"),
            cwd=".",
            environment={},
            timeout_seconds=5,
            max_output_bytes=1024,
        )
    )
    assert outcome.exit_code is None
    assert outcome.failure is not None
    assert "围栏策略缺失" in outcome.failure


@on_macos
def test_executor_applies_the_fence_end_to_end() -> None:
    provider = SeatbeltProvider()
    if not provider.available():
        pytest.skip("sandbox-exec 不可用")
    executor = SandboxedCommandExecutor(LocalCommandExecutor(), provider)
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "ws"
        workspace.mkdir()
        escaped = Path(tmp) / "escaped.txt"
        outcome = executor.run(
            CommandRequest(
                argv=("/bin/sh", "-c", f"echo x > {escaped}"),
                cwd=str(workspace),
                environment={"PATH": "/usr/bin:/bin"},
                timeout_seconds=15,
                max_output_bytes=65536,
                fence=FencePolicy(writable_roots=(str(workspace),)),
            )
        )
        assert outcome.exit_code != 0
        assert not escaped.exists()


# ---- WSL2 (Windows 上的围栏路径) ----


@pytest.mark.parametrize(
    ("windows", "wsl"),
    [
        ("C:\\Users\\x\\proj", "/mnt/c/Users/x/proj"),
        ("D:/data/repo", "/mnt/d/data/repo"),
        ("c:\\a\\b\\c", "/mnt/c/a/b/c"),
        # 已经是 Linux 形态的原样返回: 再翻一次会得到 /mnt//home/...
        ("/home/u/proj", "/home/u/proj"),
        ("", ""),
    ],
)
def test_windows_paths_translate_into_the_wsl_mount_layout(
    windows: str, wsl: str
) -> None:
    assert windows_to_wsl_path(windows) == wsl


def test_an_untranslatable_path_is_not_guessed_at() -> None:
    """UNC 路径翻不了就原样给出去, 让 bwrap 报错.

    猜一个路径比报错危险 —— 猜错的那个可能正好是个存在的目录, 于是被 bind 成可写.
    """
    assert windows_to_wsl_path("\\\\server\\share\\x") == "//server/share/x"


def test_wsl_wrapper_refuses_an_argv_it_does_not_recognise() -> None:
    """形状对不上就抛错, 不硬拼.

    拼错的后果是命令在**没有围栏**的 WSL 里跑起来, 而调用方以为它被围住了 ——
    这比拒绝执行糟得多.
    """
    provider = Wsl2Provider(executable="C:\\Windows\\System32\\wsl.exe")
    with pytest.raises(OSError, match="argv 形如"):
        provider.wrap(("cmd.exe", "/C", "dir"), FencePolicy())


def test_wsl_wrapper_inserts_bubblewrap_with_translated_paths() -> None:
    provider = Wsl2Provider(executable="C:\\Windows\\System32\\wsl.exe")
    policy = FencePolicy(
        writable_roots=("C:\\proj",),
        denied_read_paths=("C:\\Users\\x\\.ssh",),
        network_allowed=False,
    )
    argv = provider.wrap(
        ("C:\\Windows\\System32\\wsl.exe", "-e", "/bin/bash", "-c", "ls"), policy
    )

    assert argv[0] == "C:\\Windows\\System32\\wsl.exe"
    assert argv[1] == "-e"
    assert argv[2] == "bwrap"
    assert "--bind" in argv
    assert "/mnt/c/proj" in argv
    assert "/mnt/c/Users/x/.ssh" in argv
    assert "--unshare-net" in argv
    assert "--unshare-pid" in argv
    # 内层命令原样保留在 -- 之后.
    assert argv[argv.index("--") + 1 :] == ("/bin/bash", "-c", "ls")


def test_full_access_leaves_the_network_alone_in_wsl() -> None:
    provider = Wsl2Provider(executable="wsl.exe")
    argv = provider.wrap(
        ("wsl.exe", "-e", "/bin/sh", "-c", "curl x"),
        FencePolicy(writable_roots=("C:\\proj",), network_allowed=True),
    )
    assert "--unshare-net" not in argv


def test_selection_on_windows_without_wsl2_falls_back_to_unconfined() -> None:
    """用户选定的兜底: WSL2 没装就是 UNCONFINED, 跨栏一律 ASK.

    不退化成"用静态分析补偿" —— 那条路本 ADR 明确否决过 (决策 5).
    """
    provider, report = select_provider(system="Windows")
    # 本机是 macOS, 必然没有 wsl.exe.
    assert provider.name == "none"
    assert report.confined is False
