"""FORGE.md 的读取端口与结果 DTO (ADR-0018 §5).

读取规则 (查哪些路径, 多大算超限, 怎么标信任) 属 application; 真的去碰文件系统属
infrastructure. 端口在这里, 实现在 infrastructure/prompt.

上限写在这里而不是实现里: 它是**决策**不是实现细节, 换一个 reader 实现不该顺带换掉
"单文件 32 KiB, 总计 64 KiB"这条约束.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from forgecli.core.boundary import is_within
from forgecli.core.tool.hashing import digest_text

__all__ = [
    "FsProjectInstructionReader",
    "INSTRUCTION_FILE_NAME",
    "MAX_INSTRUCTION_BYTES",
    "MAX_TOTAL_INSTRUCTION_BYTES",
    "ProjectInstruction",
]

INSTRUCTION_FILE_NAME = "FORGE.md"
MAX_INSTRUCTION_BYTES = 32 * 1024
MAX_TOTAL_INSTRUCTION_BYTES = 64 * 1024

# 截断标记. 显式加进正文, 不静默截断 —— 模型据此知道自己看到的不是全部.
TRUNCATION_NOTICE = "[FORGE.md 已截断]"


@dataclass(frozen=True)
class ProjectInstruction:
    """一份读到的项目指令. 已经过大小与编码校验.

    没有 truncated 标志: 截断这件事由正文末尾的 TRUNCATION_NOTICE 表达, 而那是模型真正
    看得到的那一份. 再挂一个布尔就是同一个事实的第二份副本, 且没有消费方 —— ADR-0018
    §10.1 要的 project_instructions_truncated 属于阶段 4 的诊断接线, 那时再从正文取.
    """

    source_id: str
    text: str
    digest: str

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("ProjectInstruction.source_id 不能为空")


# ---- 原 infrastructure/prompt/fs_project_instruction_reader.py ----
# 受限读取每个受信任根目录的 FORGE.md
#
# 限制不是防御恶意用户 —— 工作区本来就是用户的. 它防的是两件别的事:
#
# 1. **一份会自动发给llm的文件不该无上限.** 32K / 64K 是滥用防护
# 2. **符号链接越界.** 工作区里一个指向 ~/.ssh 的 FORGE.md 符号链接, 字面路径看着在区内.
#    判定因此只看 realpath.
#
# 读不到, 越界, 非 UTF-8, 含 NUL 的一律**跳过**而不抛错: 提示词是辅助机制, 一份读不了的
# FORGE.md 不该让整个会话起不来
class FsProjectInstructionReader:
    """读取受信任根目录顶层的 FORGE.md."""

    def read(self, workspace_roots: tuple[str, ...]) -> tuple[ProjectInstruction, ...]:
        """按给定根的顺序读取. 读不到, 越界, 非 UTF-8 的一律跳过, 不抛错.

        提示词是辅助机制: 一份读不了的 FORGE.md 不该让整个会话起不来 (ADR-0018 §11).
        """
        found: list[ProjectInstruction] = []
        budget = MAX_TOTAL_INSTRUCTION_BYTES
        for index, root in enumerate(workspace_roots):
            if budget <= 0:
                break
            instruction = self._read_one(root, primary=index == 0, budget=budget)
            if instruction is None:
                continue
            found.append(instruction)
            budget -= len(instruction.text.encode("utf-8"))
        return tuple(found)

    def read_nested(self, directory: str) -> ProjectInstruction | None:
        """某个子目录顶层的 FORGE.md (ADR-0056 决策 7): 首次触达该目录下的文件时以通知
        发一次. 同样的大小与编码限制, source_id 是目录路径."""
        return self._read_one(
            directory, primary=False, budget=MAX_INSTRUCTION_BYTES, source_id=directory
        )

    def _read_one(
        self,
        root: str,
        *,
        primary: bool,
        budget: int,
        source_id: str = "",
    ) -> ProjectInstruction | None:
        try:
            root_real = Path(root).resolve()
            target = (root_real / INSTRUCTION_FILE_NAME).resolve()
        except OSError:
            return None
        # 只读顶层, 不递归; 且 realpath 必须仍在这个根内 —— 挡掉越界符号链接.
        if not is_within(str(target), str(root_real)):
            return None
        try:
            if not target.is_file():
                return None
            raw = target.read_bytes()
        except OSError:
            return None
        if b"\x00" in raw:
            # 含 NUL 说明它不是文本. 不猜, 直接跳过.
            return None
        limit = min(MAX_INSTRUCTION_BYTES, budget)
        truncated = len(raw) > limit
        text = _decode(raw[:limit] if truncated else raw)
        if text is None or not text.strip():
            return None
        if truncated:
            text = f"{text}\n{TRUNCATION_NOTICE}"
        return ProjectInstruction(
            source_id=source_id
            or f"{'primary' if primary else 'added'}:{INSTRUCTION_FILE_NAME}",
            text=text,
            digest=digest_text(text),
        )


def _decode(raw: bytes) -> str | None:
    """严格 UTF-8 解码; 截断点落在多字节字符中间时回退到最后一个完整字符.

    用 errors='ignore' 会把一个真的非 UTF-8 文件解成一串乱码照样发给供应商, 所以先严格
    解一次: 只有在"确实是 UTF-8, 只是被切断了"这一种情况下才逐字节回退.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.start == 0:
            return None
        head = raw[: error.start]
        try:
            return head.decode("utf-8")
        except UnicodeDecodeError:
            return None
