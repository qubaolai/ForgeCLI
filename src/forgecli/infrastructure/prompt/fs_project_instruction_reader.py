"""受限读取每个受信任根目录的 FORGE.md

限制不是防御恶意用户 —— 工作区本来就是用户的. 它防的是两件别的事:

1. **一份会自动发给llm的文件不该无上限.** 32K / 64K 是滥用防护
2. **符号链接越界.** 工作区里一个指向 ~/.ssh 的 FORGE.md 符号链接, 字面路径看着在区内.
   判定因此只看 realpath.

读不到, 越界, 非 UTF-8, 含 NUL 的一律**跳过**而不抛错: 提示词是辅助机制, 一份读不了的
FORGE.md 不该让整个会话起不来
"""

from __future__ import annotations

from pathlib import Path

from forgecli.application.prompt.project_instruction_reader import (
    INSTRUCTION_FILE_NAME,
    MAX_INSTRUCTION_BYTES,
    MAX_TOTAL_INSTRUCTION_BYTES,
    TRUNCATION_NOTICE,
    ProjectInstruction,
    ProjectInstructionReader,
)
from forgecli.domain.tool.hashing import digest_text
from forgecli.domain.workspace.boundary import is_within

__all__ = ["FsProjectInstructionReader"]


class FsProjectInstructionReader(ProjectInstructionReader):
    def read(self, workspace_roots: tuple[str, ...]) -> tuple[ProjectInstruction, ...]:
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

    def _read_one(
        self, root: str, *, primary: bool, budget: int
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
            source_id=f"{'primary' if primary else 'added'}:{INSTRUCTION_FILE_NAME}",
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
