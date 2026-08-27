"""命令影响范围推导 (domain.security.shell.effects).

这个模块唯一要紧的性质是**默认值的方向**: 不认识的命令归 UNPROVEN, 而不是归只读.
早先归只读, 于是 `java -jar x.jar`, `sed -i`, `chmod 777` 都被判成"只读了那个文件",
连恢复点都不会建立.
"""

from __future__ import annotations

from pytest import mark

from forgecli.application.security.analyzers.shell_effects import effects_of
from forgecli.domain.security.shell.command_plan import ShellKind
from forgecli.domain.security.shell.effects import EffectKind, effect_kind_of
from forgecli.domain.security.shell.expansion import expand_targets
from forgecli.domain.security.shell.parser import parse_command
from forgecli.domain.tool.plan import PlanEffects


def _first(raw: str) -> EffectKind:
    plan = parse_command(raw, ShellKind.POSIX, cwd="/w")
    return effect_kind_of(plan.units[0])


@mark.parametrize(
    ("raw", "expected"),
    [
        # 曾经全部被判成只读.
        ("chmod 777 README.md", EffectKind.WRITE),
        ("chown me README.md", EffectKind.WRITE),
        ("ln -s /etc/passwd link", EffectKind.WRITE),
        ("tar -xzf pkg.tgz", EffectKind.WRITE),
        ("unzip pkg.zip", EffectKind.WRITE),
        # 确定只读的仍然只读 —— 修复不该把一切都打成未知.
        ("ls -al src", EffectKind.READ),
        ("cat README.md", EffectKind.READ),
        ("grep -rn foo src", EffectKind.READ),
        ("wc -l README.md", EffectKind.READ),
        ("stat README.md", EffectKind.READ),
        # 会跑任意代码.
        ("java -jar build/app.jar", EffectKind.RUNS_CODE),
        ("osascript -e 'do shell script \"id\"'", EffectKind.RUNS_CODE),
        ("docker run alpine id", EffectKind.RUNS_CODE),
        ("pytest -k smoke", EffectKind.RUNS_CODE),
        # 删除与移动.
        ("rm -rf build", EffectKind.DELETE),
        ("mv a b", EffectKind.MOVE),
    ],
)
def test_effect_kind(raw: str, expected: EffectKind) -> None:
    assert _first(raw) is expected


@mark.parametrize(
    ("raw", "expected"),
    [
        # 同一个命令按参数分流: 这是"取决于参数"的那一类, 不能整条归到某一边.
        ("sed s/a/b/ README.md", EffectKind.READ),
        ("sed -i s/a/b/ README.md", EffectKind.WRITE),
        ("sed -i.bak s/a/b/ README.md", EffectKind.WRITE),
        # 聚合写法同样成立.
        ("sed -ni p README.md", EffectKind.WRITE),
        ("sed --in-place s/a/b/ README.md", EffectKind.WRITE),
        ("sort README.md", EffectKind.READ),
        ("sort -o out.txt README.md", EffectKind.WRITE),
    ],
)
def test_conditional_writers_follow_their_flags(raw: str, expected: EffectKind) -> None:
    assert _first(raw) is expected


@mark.parametrize(
    ("raw", "expected"),
    [
        ("git status", EffectKind.READ),
        ("git log --oneline", EffectKind.READ),
        ("git diff --stat", EffectKind.READ),
        ("git commit -m x", EffectKind.WRITE),
        ("git checkout main", EffectKind.WRITE),
        ("git clean -fd", EffectKind.WRITE),
        # 裸 git 不该被当成只读: 没有子命令时按写处理.
        ("git", EffectKind.WRITE),
    ],
)
def test_git_splits_by_subcommand(raw: str, expected: EffectKind) -> None:
    """git 两面都是. 复用只读子命令这份既有知识, 而不是把整个 git 归到某一边."""
    assert _first(raw) is expected


def test_an_unknown_command_is_unproven_not_read_only() -> None:
    assert _first("some-vendor-tool --deploy") is EffectKind.UNPROVEN


def test_an_unproven_command_leaves_the_target_set_open() -> None:
    """封闭的语义是"这就是全部目标". 连它写不写都不知道时, 不能声称已封闭.

    这条决定了它拿不到普通 ALLOW 直写, 也无法沉淀成学习规则.
    """
    plan = parse_command("some-vendor-tool out.txt", ShellKind.POSIX, cwd="/w")
    result = expand_targets(
        plan,
        resolve=lambda path: f"/w/{path}" if not path.startswith("/") else path,
        glob=lambda _: (),
        home="/home/dev",
    )

    assert result.closed is False
    assert any("影响范围无法推导" in reason for reason in result.reasons)


def test_a_known_reader_keeps_the_target_set_closed() -> None:
    plan = parse_command("cat README.md", ShellKind.POSIX, cwd="/w")
    result = expand_targets(
        plan,
        resolve=lambda path: f"/w/{path}" if not path.startswith("/") else path,
        glob=lambda _: (),
        home="/home/dev",
    )
    assert result.closed is True


def _effects(raw: str) -> PlanEffects:
    plan = parse_command(raw, ShellKind.POSIX, cwd="/w")
    resolve = lambda path: path if path.startswith("/") else f"/w/{path}"  # noqa: E731
    expanded = expand_targets(
        plan, resolve=resolve, glob=lambda _: (), home="/home/dev"
    )
    return effects_of(plan, expanded.targets, "/home/dev", resolve=resolve)


def test_a_discard_redirect_is_not_a_workspace_write() -> None:
    """`2>/dev/null` 不是"往工作区外写文件".

    留在目标集合里的后果是围栏判它越界, 于是 shell 里最常见的一种写法在 auto 模式下
    每次都要人点头 —— 真实日志里模型两次想查 maven 装在哪, 都停在这里.
    """
    effects = _effects("ls -a . 2>/dev/null")

    assert effects.write_paths == ()


def test_a_real_device_write_still_shows_up_as_a_target() -> None:
    effects = _effects("echo x > /dev/disk0")

    assert effects.write_paths == ("/dev/disk0",)
