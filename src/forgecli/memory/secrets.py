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

### 为什么不换成 detect-secrets (ADR-0040 决策 4.9, 2026-08-28 实测后撤回)

原提案要把这里换成 Yelp 的 detect-secrets, 并"启用 provider-specific, high entropy 和
keyword detector". 实测了两种配置:

- **开熵检测器**: 文件路径, 40 位 commit sha, 构建产物名 `index-JwK66ukq.js`, 驼峰标识符
  全部命中. 那正是上面那段刻意避开的误报, 而记忆是**静默写入**的 (ADR-0033 决策 2) ——
  误报的表现是"记不住东西", 且没有人会看到原因.
- **只开厂商与关键词检测器**: 零误报, 但十种真凭证只抓到六种, 漏掉 OpenAI, Anthropic,
  Google API key 与 npm token —— 而这四种下面的前缀表都认得.

两个方向都不如现状, 所以不换. 但那次实测暴露了这里两个真误报, 已在第 3 条的实现里修掉
(见 `_TOKEN` 与 `_is_random_token` 的注释).
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
#
# 字符集里**没有 `/`**: 带它的话, 一条绝对路径就是一个 70 字符, 大小写数字齐全的"长串",
# 于是每一条"构建产物在 /Users/.../dist/forgecli-0.0.1.whl"都被当成凭证拒掉. 去掉之后
# 路径会被切成一段一段, 没有一段够 32.
#
# 代价是含 `/` 的裸 base64 会被切开而漏掉. 接受它: 有名有姓的厂商 token 由上面的前缀表
# 认, 而这一条本来就是给未知厂商兜底的最后一道, 按本模块的取舍宁可漏.
_TOKEN = re.compile(r"[A-Za-z0-9+=_\-.]{32,}")
_LOWER = re.compile(r"[a-z]")
_UPPER = re.compile(r"[A-Z]")
_DIGIT = re.compile(r"[0-9]")

# 同一类字符连着出现八个以上. 随机 token 不会长这样, 而英文单词拼起来的驼峰标识符一定会:
# `ToolAuthorizationServiceRegistryBuilderFactoryImpl2026` 里的 `uthorization` 是 12 个.
_SAME_CLASS_RUN = re.compile(r"[a-z]{8,}|[A-Z]{8,}|[0-9]{8,}")


def looks_like_secret(text: str) -> bool:
    """这段内容像不像凭证. 像就拒绝写入, 不静默截断."""
    if _PEM.search(text):
        return True
    for prefix in CREDENTIAL_PREFIXES:
        if prefix in text:
            return True
    return any(_is_random_token(match.group()) for match in _TOKEN.finditer(text))


def _is_random_token(token: str) -> bool:
    """大小写数字三样都有, 而且没有长的同类字符连排.

    三样都要求, 而不是"任意两样": 只有小写加数字的是十六进制哈希与多数路径,
    只有大小写的是驼峰标识符, 两者都是正常内容.

    "没有长连排"这一条是 2026-08-28 补的. 原先只要三样齐全就算, 于是
    `ToolAuthorizationServiceRegistryBuilderFactoryImpl2026` 这种带年份的驼峰类名被判成
    凭证 —— 而这个仓库里正好全是这种名字. 随机 token 每两三个字符就换一次字符类, 英文
    单词拼起来的标识符不会.
    """
    if not (_LOWER.search(token) and _UPPER.search(token) and _DIGIT.search(token)):
        return False
    return _SAME_CLASS_RUN.search(token) is None
