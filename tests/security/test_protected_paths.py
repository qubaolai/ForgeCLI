"""受保护路径的生成 (ADR-0014 §5.1).

这里锁的主要是一个真实缺陷: 原实现用 ``Path.home()`` 定位家目录, 而它读 HOME 环境变量.
``sudo`` 会把 HOME 重置成 root 的家目录, 于是 ``~/.ssh`` 这批 deny_read 条目整体指向
``/root/.ssh``, 真正要保护的那个用户的密钥**掉出保护清单** —— 偏偏那正是进程拿着 root
的时候, 两层保护同时失效且方向一致.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# 整个文件是 POSIX 专属: 断言里有 /usr /etc, 家目录也从口令数据库取.
pwd = pytest.importorskip("pwd", reason="口令数据库与 POSIX 路径断言只在类 Unix 上成立")

from forgecli.domain.security.protected_paths import (  # noqa: E402
    PathVerdict,
    ProtectedCategory,
    ProtectedPathPolicy,
    ProtectedRoot,
)
from forgecli.infrastructure.security.protected_paths_builder import (  # noqa: E402
    build_protected_path_policy,
)


def _real_home() -> str:
    """口令数据库里的家目录 —— 不受 HOME 影响, 正是被测函数该认的那个."""
    return pwd.getpwuid(os.getuid()).pw_dir


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.delenv("FORGE_CONFIG_DIR", raising=False)


# ---- HOME 被改写 ----


@pytest.mark.parametrize(
    "fake_home",
    ["/var/root", "/root", "/tmp/somewhere-else"],
    ids=["macos-sudo", "linux-sudo", "任意改写"],
)
def test_credentials_stay_protected_when_home_is_rewritten(
    fake_home: str, clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HOME 指向别处时, 真实用户的凭证仍然 deny_read."""
    monkeypatch.setenv("HOME", fake_home)
    policy = build_protected_path_policy()

    for probe in (".ssh/id_rsa", ".aws/credentials", ".gnupg/secring.gpg"):
        path = f"{_real_home()}/{probe}"
        assert policy.verdict_for_read(path) is PathVerdict.DENY_READ, path


def test_startup_files_stay_protected_when_home_is_rewritten(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", "/var/root")
    policy = build_protected_path_policy()

    for probe in (".zshenv", ".bashrc", ".config/autostart"):
        path = f"{_real_home()}/{probe}"
        assert policy.verdict_for_write(path) is PathVerdict.DENY_WRITE, path


def test_the_rewritten_home_is_also_protected(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """两个家目录取并集, 不是二选一: 判断"哪个才是真的"会引入新的出错方式."""
    monkeypatch.setenv("HOME", str(tmp_path))
    policy = build_protected_path_policy()

    assert policy.verdict_for_read(f"{tmp_path}/.ssh/id_rsa") is PathVerdict.DENY_READ
    assert policy.verdict_for_read(f"{_real_home()}/.ssh/id_rsa") is (
        PathVerdict.DENY_READ
    )


def test_sudo_user_home_is_protected(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SUDO_USER 可以伪造, 但伪造只会让保护范围变大, 所以照收."""
    monkeypatch.setenv("HOME", "/var/root")
    monkeypatch.setenv("SUDO_USER", pwd.getpwuid(os.getuid()).pw_name)
    policy = build_protected_path_policy()

    assert policy.verdict_for_read(f"{_real_home()}/.ssh/id_rsa") is (
        PathVerdict.DENY_READ
    )


def test_a_bogus_sudo_user_is_ignored(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """查不到这个用户就跳过, 不能让一个假名字把生成过程搞崩."""
    monkeypatch.setenv("SUDO_USER", "no-such-user-hopefully-xyzzy")
    policy = build_protected_path_policy()

    assert policy.verdict_for_read(f"{_real_home()}/.ssh/id_rsa") is (
        PathVerdict.DENY_READ
    )


# ---- 退化值 ----


@pytest.mark.parametrize("degenerate", ["/", "", "   "])
def test_a_degenerate_home_does_not_protect_the_whole_machine(
    degenerate: str, clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """HOME=/ 会让每一次读取都被拒. 那不是更安全, 那是不可用."""
    monkeypatch.setenv("HOME", degenerate)
    policy = build_protected_path_policy(workspace_roots=(str(tmp_path),))

    assert policy.verdict_for_read(str(tmp_path / "src" / "main.py")) is (
        PathVerdict.ALLOWED
    )
    # 真实用户的凭证不因为这次退化而失去保护.
    assert policy.verdict_for_read(f"{_real_home()}/.ssh/id_rsa") is (
        PathVerdict.DENY_READ
    )


# ---- 其他用户的家目录不再顺手扫到系统目录 ----


@pytest.mark.parametrize("fake_home", ["/root", "/var/root"])
def test_system_dirs_keep_their_own_category_under_a_root_home(
    fake_home: str, clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """原实现扫的是 home 的父目录. root 的父目录是 / 或 /var, 于是 /usr /etc 被标成
    "其他用户的家目录" 并附带 deny_read —— 读个系统库都要拦, 而且因为同名条目后写覆盖
    先写, 它们原本"可读不可写"的分类被直接顶掉."""
    monkeypatch.setenv("HOME", fake_home)
    policy = build_protected_path_policy()

    for path in ("/usr/lib/libc.so", "/etc/hosts"):
        root = policy.classify(path)
        assert root is not None, path
        assert root.category is ProtectedCategory.PLATFORM_SYSTEM, path
        assert policy.verdict_for_read(path) is PathVerdict.ALLOWED, path
        assert policy.verdict_for_write(path) is PathVerdict.DENY_WRITE, path


# ---- 去重只能收紧 ----


def test_duplicate_declarations_can_only_tighten() -> None:
    """同一路径声明两次时, 后写的宽松规则不能把先写的严格规则顶掉."""
    policy = ProtectedPathPolicy(
        roots=(
            ProtectedRoot("/x", ProtectedCategory.CREDENTIAL_STORE, deny_read=True),
            ProtectedRoot("/x", ProtectedCategory.PLATFORM_SYSTEM, deny_read=False),
        )
    )
    assert policy.verdict_for_read("/x/secret") is PathVerdict.DENY_READ


# ---- 新增的保护路径 ----


@pytest.mark.parametrize(
    "subpath",
    [
        ".git-credentials",
        ".config/gcloud",
        ".azure",
        ".vault-token",
        ".pgpass",
        ".m2/settings.xml",
        ".password-store",
        ".mozilla",
        "Library/Application Support/Google/Chrome",
    ],
)
def test_credential_stores_deny_read(subpath: str, clean_env: None) -> None:
    policy = build_protected_path_policy()
    path = f"{_real_home()}/{subpath}/x"
    assert policy.verdict_for_read(path) is PathVerdict.DENY_READ


@pytest.mark.parametrize(
    "subpath",
    [
        # 连非交互 shell 都会读, 而 Agent 每条命令都是非交互 shell
        ".zshenv",
        ".bash_login",
        # Agent 会真的去跑这些程序, 它们启动时读自己的配置
        ".curlrc",
        ".wgetrc",
        ".gdbinit",
        # 登录自启
        ".config/autostart",
        "Library/LaunchAgents",
    ],
)
def test_auto_read_configs_deny_write(subpath: str, clean_env: None) -> None:
    """判据是"下一条命令会自动读它", 不是"能持久化" —— 它们让目标集合分析失真."""
    policy = build_protected_path_policy()
    assert policy.verdict_for_write(f"{_real_home()}/{subpath}") is (
        PathVerdict.DENY_WRITE
    )


@pytest.mark.parametrize(
    "subpath",
    [
        # 只在人自己启动编辑器时执行, 而且"帮我配一下 neovim"是合理请求
        ".vimrc",
        ".config/nvim/init.lua",
        ".emacs.d/init.el",
        # `pip install --user` 一类用户级安装的落点; 挡掉它等于逼用户去用 sudo
        ".local/bin/tool",
        "bin/tool",
        # 都是常规操作: 配镜像, 设 user.email, gh auth setup-git
        ".gitconfig",
        ".config/git/config",
        ".cargo/config.toml",
        ".config/pip/pip.conf",
    ],
)
def test_deliberate_omissions_stay_writable(subpath: str, clean_env: None) -> None:
    """受保护路径的写入是 Hard Deny, 不能批准也不能豁免.

    所以"符合收录判据"还不够, 还得"人也确实没有理由在这里写" —— 这几条都是人有理由写的.
    """
    policy = build_protected_path_policy()
    assert policy.verdict_for_write(f"{_real_home()}/{subpath}") is PathVerdict.ALLOWED


def test_pip_conf_stays_readable_but_pypirc_does_not(clean_env: None) -> None:
    """pip.conf 多数情况下只是个镜像地址, 挡掉读取会让 Agent 没法诊断安装问题;
    .pypirc 里躺的是上传 token, 两者不该同等对待."""
    policy = build_protected_path_policy()
    assert policy.verdict_for_read(f"{_real_home()}/.config/pip/pip.conf") is (
        PathVerdict.ALLOWED
    )
    assert policy.verdict_for_read(f"{_real_home()}/.pypirc") is PathVerdict.DENY_READ


def test_cargo_credentials_stay_protected_even_though_config_does_not(
    clean_env: None,
) -> None:
    """放开 .cargo/config.toml 不能连带放开同目录下的 credentials.toml."""
    policy = build_protected_path_policy()
    assert policy.verdict_for_read(f"{_real_home()}/.cargo/credentials.toml") is (
        PathVerdict.DENY_READ
    )


# ---- 既有行为不能被破坏 ----


def test_the_workspace_is_still_exempt(clean_env: None, tmp_path: Path) -> None:
    policy = build_protected_path_policy(workspace_roots=(str(tmp_path),))
    assert policy.verdict_for_write(str(tmp_path / "src" / "main.py")) is (
        PathVerdict.ALLOWED
    )


def test_forge_state_is_never_exempt(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """把 Forge 自己的目录 /add-dir 进来也不行: 能改自己状态的 Agent 能给自己发许可."""
    forge_home = tmp_path / ".forge"
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(forge_home))
    policy = build_protected_path_policy(workspace_roots=(str(forge_home),))

    assert policy.verdict_for_write(str(forge_home / "config.toml")) is (
        PathVerdict.DENY_WRITE
    )
    assert policy.verdict_for_read(str(forge_home / "state" / "rules" / "x.toml")) is (
        PathVerdict.DENY_READ
    )


def test_the_hash_changes_when_the_protected_set_changes(clean_env: None) -> None:
    """保护集合变了, 旧授权与旧审批必须全部失效 —— 这条靠哈希进执行画像来保证."""
    base = build_protected_path_policy()
    widened = ProtectedPathPolicy(
        roots=(*base.roots, ProtectedRoot("/x", ProtectedCategory.PLATFORM_SYSTEM))
    )
    assert base.protected_roots_hash != widened.protected_roots_hash


def test_the_archive_directory_stays_unreadable_through_the_normal_tools(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR-0032 决策 6.2 那条论证的另一半.

    artifact_read 之所以不用绕过任何东西, 是因为它的 ToolPlan 不声明路径; 而 fs_read
    与 shell_run 声明了, 所以它们指向归档目录时仍然是 Hard Deny —— 全量落盘之后这里
    躺着每一次工具调用的完整输出, 放开等于给了一条读取全部历史的旁路.
    """
    monkeypatch.setenv("FORGE_CONFIG_DIR", str(tmp_path / "forge"))
    policy = build_protected_path_policy(
        workspace_roots=(str(tmp_path / "forge"),),
    )
    archive = str(tmp_path / "forge" / "state" / "artifacts" / "ab" / "abcd.txt")

    assert policy.verdict_for_read(archive) is PathVerdict.DENY_READ
    assert policy.verdict_for_write(archive) is PathVerdict.DENY_WRITE
