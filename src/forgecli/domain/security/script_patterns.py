"""脚本内容的确定性信号提取 (ADR-0013 §8).

纯函数: 输入脚本文本, 输出 ScriptFacts. 不读盘 (内容由调用方收集), 不调 LLM.

规则按语言分组, 但输出统一归一到同一份能力事实 —— 下游只关心"有没有子进程", 不关心
那是 `subprocess.run` 还是 `child_process.spawn`.

正则不是解析器. 它会漏 (拼接出来的字符串), 也会误报 (注释里的 eval). 因此:

- 命中硬性信号 -> 可以直接 DENY (误报的代价是一次拒绝).
- 没命中 -> **不能**据此放行, 只是把 confidence 抬高一点, 剩下的交给分类器.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from forgecli.domain.security.script_facts import ScriptFacts, ScriptSignal
from forgecli.domain.tool.hashing import digest_text

__all__ = ["analyze_script_source"]

_Rule = tuple[re.Pattern[str], str, str]


def _rule(pattern: str, code: str, detail: str, flags: int = 0) -> _Rule:
    return re.compile(pattern, flags), code, detail


# 跨语言都算硬性危险的信号.
_HARD_RULES: tuple[_Rule, ...] = (
    _rule(r"\bsudo\b|\bdoas\b|\bpkexec\b", "privilege_escalation", "提权执行"),
    _rule(
        r"rm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(?:/|~|\$HOME)\s*(?:$|['\"\s])",
        "destructive_wipe",
        "递归删除根或家目录",
    ),
    _rule(
        r"\.ssh/id_|\.aws/credentials|\.netrc|id_rsa|id_ed25519|/etc/shadow",
        "credential_exfiltration",
        "访问凭证文件",
    ),
    _rule(
        r"(?:urlopen|requests\.get|fetch|curl|wget)[^\n]{0,80}"
        r"(?:exec|eval|system|Popen|spawn)",
        "download_and_execute",
        "下载后直接执行",
    ),
    _rule(
        r"/dev/tcp/|socket\.SOCK_STREAM[^\n]{0,80}exec", "reverse_shell", "反弹 Shell"
    ),
    _rule(
        r"\.git/hooks/|\.bashrc|\.zshrc|\.bash_profile|crontab|LaunchAgents",
        "startup_config_write",
        "写入启动配置或 Git hook",
    ),
)

_PYTHON_RULES: tuple[_Rule, ...] = (
    _rule(
        r"\bsubprocess\.|os\.system\(|os\.popen\(|os\.execv",
        "child_process",
        "启动子进程",
    ),
    _rule(r"shell\s*=\s*True", "shell_true", "subprocess 使用 shell=True"),
    _rule(
        r"\beval\(|\bexec\(|__import__\(|importlib\.import_module",
        "dynamic_execution",
        "动态求值",
    ),
    _rule(r"\bopen\([^)]*['\"][wax]", "file_write", "写文件"),
    _rule(r"\bopen\([^)]*['\"]r", "file_read", "读文件"),
    _rule(
        r"\bos\.remove\(|shutil\.rmtree\(|os\.unlink\(|Path\([^)]*\)\.unlink",
        "file_delete",
        "删除文件",
    ),
    _rule(r"\brequests\.|urllib|httpx|aiohttp|socket\.socket", "network", "网络访问"),
    _rule(r"os\.chmod\(|os\.chown\(|os\.setuid", "privilege_change", "修改权限或身份"),
    _rule(r"while\s+True\s*:|fork\(\)", "resource_exhaustion", "可能无限占用资源"),
    _rule(r"base64\.b64decode\(|codecs\.decode\(", "opaque", "编码内容, 静态不可判定"),
)

_JS_RULES: tuple[_Rule, ...] = (
    _rule(r"child_process|execSync\(|spawnSync\(", "child_process", "启动子进程"),
    _rule(r"\beval\(|new Function\(|vm\.runIn", "dynamic_execution", "动态求值"),
    _rule(r"fs\.write|fs\.appendFile|createWriteStream", "file_write", "写文件"),
    _rule(r"fs\.read|createReadStream", "file_read", "读文件"),
    _rule(r"fs\.unlink|fs\.rm\b|rimraf", "file_delete", "删除文件"),
    _rule(r"\bfetch\(|axios|http\.request|net\.Socket", "network", "网络访问"),
    _rule(r"Buffer\.from\([^)]*base64", "opaque", "编码内容, 静态不可判定"),
)

_SHELL_RULES: tuple[_Rule, ...] = (
    _rule(r"\bcurl\b|\bwget\b|\bnc\b|\bssh\b|\bscp\b", "network", "网络访问"),
    _rule(
        r"\beval\b|\bsource\b|^\s*\.\s+", "dynamic_execution", "动态求值", re.MULTILINE
    ),
    _rule(r"\brm\b|\bunlink\b", "file_delete", "删除文件"),
    _rule(r">\s*\S+|>>\s*\S+|\btee\b", "file_write", "写文件"),
    _rule(r"\bchmod\b|\bchown\b", "privilege_change", "修改权限"),
    _rule(r"base64\s+-d|\bxxd\b\s+-r", "opaque", "编码内容, 静态不可判定"),
)

_BY_LANGUAGE: dict[str, tuple[_Rule, ...]] = {
    "python": _PYTHON_RULES,
    "javascript": _JS_RULES,
    "typescript": _JS_RULES,
    "shell": _SHELL_RULES,
}

_SIGNAL_TO_FIELD: dict[str, str] = {
    "child_process": "child_process",
    "shell_true": "child_process",
    "dynamic_execution": "dynamic_execution",
    "network": "network_access",
    "privilege_change": "privilege_or_system_change",
    "privilege_escalation": "privilege_or_system_change",
    "credential_exfiltration": "credential_access",
    "download_and_execute": "network_access",
    "reverse_shell": "network_access",
    "resource_exhaustion": "resource_exhaustion_signal",
    "startup_config_write": "external_side_effect",
    "git_hook_write": "external_side_effect",
}


def analyze_script_source(
    source: str,
    language: str,
    *,
    dependency_hash: str | None = None,
    incomplete: bool = False,
) -> ScriptFacts:
    """对脚本文本做确定性分析."""
    rules = (*_HARD_RULES, *_BY_LANGUAGE.get(language, ()))
    signals = tuple(_scan(source, rules))
    codes = {signal.code for signal in signals}

    flags = {field: False for field in set(_SIGNAL_TO_FIELD.values())}
    for code in codes:
        field_name = _SIGNAL_TO_FIELD.get(code)
        if field_name is not None:
            flags[field_name] = True

    opaque = tuple(signal.detail for signal in signals if signal.code == "opaque")
    unknown_language = language not in _BY_LANGUAGE
    if unknown_language:
        opaque = (*opaque, f"没有 {language} 的静态规则集")

    return ScriptFacts(
        content_hash=digest_text(source),
        language=language,
        files_read=_paths(signals, "file_read"),
        files_written=_paths(signals, "file_write", "file_delete"),
        network_access=flags.get("network_access", False),
        child_process=flags.get("child_process", False),
        dynamic_execution=flags.get("dynamic_execution", False),
        privilege_or_system_change=flags.get("privilege_or_system_change", False),
        credential_access=flags.get("credential_access", False),
        external_side_effect=flags.get("external_side_effect", False),
        resource_exhaustion_signal=flags.get("resource_exhaustion_signal", False),
        opaque_constructs=opaque,
        evidence=signals,
        confidence=_confidence(source, opaque, incomplete, unknown_language),
        dependency_hash=dependency_hash,
        incomplete=incomplete,
    )


def _scan(source: str, rules: tuple[_Rule, ...]) -> Iterable[ScriptSignal]:
    lines = source.splitlines()
    for pattern, code, detail in rules:
        match = pattern.search(source)
        if match is None:
            continue
        line = source[: match.start()].count("\n") + 1
        excerpt = lines[line - 1].strip()[:120] if line <= len(lines) else ""
        yield ScriptSignal(code=code, detail=f"{detail}: {excerpt}", line=line)


def _paths(signals: tuple[ScriptSignal, ...], *codes: str) -> tuple[str, ...]:
    """静态分析给不出确定路径时, 只记"有这类操作"而不编造路径."""
    return tuple(f"<{signal.code}>" for signal in signals if signal.code in codes)


def _confidence(
    source: str, opaque: tuple[str, ...], incomplete: bool, unknown_language: bool
) -> float:
    if unknown_language or incomplete:
        return 0.2
    if opaque:
        return 0.5
    if not source.strip():
        return 0.0
    return 0.9
