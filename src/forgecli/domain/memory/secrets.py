"""记忆写入的凭证过滤 (ADR-0033 决策 5).

**这份过滤不能复用 `application/agent_run/scrubbing.py`.** 那份的注释明写"不做脱敏",
理由是工具入参是用户判断要不要放行的依据, 挡掉一部分只会让他在看不全的信息上做决定.
记忆的取舍正好相反: 静默写入, 长期读取, 没有人在看. 同一个仓库里出现两份文本处理不是
重复, 是两个不同的取舍 —— 这段话写在这里, 是给下一个想合并它们的人看的.

它也不住在 `domain/security/`: 那里放的是裁决用的词汇与规则, 而记忆按决策 3 永远
不参与裁决. 放过去会让人以为两者有关系.

### 判据只认形状, 不猜语义

拒绝的三类:

1. **已知前缀.** 各家 token 的自述格式, 误判率接近零.
2. **PEM 头.** 私钥的开头是固定文本.
3. **大小写数字混排的长串.** 典型的随机 token 长这样, 而正常的项目事实 (命令, 路径,
   目录名) 不会.

第 3 条刻意**不查熵**, 也刻意不拦纯十六进制长串: 一个 40 位的 commit sha 熵值很高,
但它不是凭证, 而"记不住 sha"对用户是可见的能力损失. 宁可漏掉一种少见的全小写 token,
也不要让模型每次想记个哈希都被拒 —— 被拒得莫名其妙, 它就再也不用这个工具了.
"""

from __future__ import annotations

import re

__all__ = ["CREDENTIAL_PREFIXES", "looks_like_secret"]

# 各家凭证的自述前缀. 命中即拒, 不看上下文.
CREDENTIAL_PREFIXES = (
    "sk-",
    "sk_live_",
    "sk_test_",
    "rk_live_",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "glpat-",
    "xoxb-",
    "xoxp-",
    "xoxa-",
    "AKIA",
    "ASIA",
    "AIza",
    "ya29.",
    "npm_",
    "dop_v1_",
    "SG.",
)

_PEM = re.compile(r"-----BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE)")

# 一个不含空白的长串.
_TOKEN = re.compile(r"[A-Za-z0-9+/=_\-.]{32,}")
_LOWER = re.compile(r"[a-z]")
_UPPER = re.compile(r"[A-Z]")
_DIGIT = re.compile(r"[0-9]")


def looks_like_secret(text: str) -> bool:
    """这段内容像不像凭证. 像就拒绝写入, 不静默截断."""
    if _PEM.search(text):
        return True
    for prefix in CREDENTIAL_PREFIXES:
        if prefix in text:
            return True
    return any(_is_random_token(match.group()) for match in _TOKEN.finditer(text))


def _is_random_token(token: str) -> bool:
    """大小写数字三样都有的长串.

    三样都要求, 而不是"任意两样": 只有小写加数字的是十六进制哈希与多数路径,
    只有大小写的是驼峰标识符, 两者都是正常内容.
    """
    return bool(_LOWER.search(token) and _UPPER.search(token) and _DIGIT.search(token))
