"""语言服务器表: 按扩展名选服务器, 在受控 PATH 里找可执行文件."""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "LanguageServer",
    "SERVERS",
    "language_id_of",
    "locate_executable",
    "server_for",
]


@dataclass(frozen=True)
class LanguageServer:
    name: str
    argv: tuple[str, ...]
    extensions: tuple[str, ...]
    language_ids: Mapping[str, str]

    @property
    def executable(self) -> str:
        return self.argv[0]


SERVERS: tuple[LanguageServer, ...] = (
    LanguageServer(
        name="pyright",
        argv=("pyright-langserver", "--stdio"),
        extensions=(".py", ".pyi"),
        language_ids={".py": "python", ".pyi": "python"},
    ),
    LanguageServer(
        name="typescript-language-server",
        argv=("typescript-language-server", "--stdio"),
        extensions=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
        language_ids={
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".jsx": "javascriptreact",
            ".mjs": "javascript",
            ".cjs": "javascript",
        },
    ),
    LanguageServer(
        name="gopls",
        argv=("gopls", "serve"),
        extensions=(".go",),
        language_ids={".go": "go"},
    ),
    LanguageServer(
        name="rust-analyzer",
        argv=("rust-analyzer",),
        extensions=(".rs",),
        language_ids={".rs": "rust"},
    ),
    LanguageServer(
        name="clangd",
        argv=("clangd",),
        extensions=(".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"),
        language_ids={
            ".c": "c",
            ".h": "c",
            ".cc": "cpp",
            ".cpp": "cpp",
            ".cxx": "cpp",
            ".hpp": "cpp",
            ".hh": "cpp",
        },
    ),
    LanguageServer(
        name="jdtls",
        argv=("jdtls",),
        extensions=(".java",),
        language_ids={".java": "java"},
    ),
)


def server_for(path: str) -> LanguageServer | None:
    extension = os.path.splitext(path)[1].lower()
    for server in SERVERS:
        if extension in server.extensions:
            return server
    return None


def language_id_of(server: LanguageServer, path: str) -> str:
    extension = os.path.splitext(path)[1].lower()
    return server.language_ids.get(extension, "plaintext")


def locate_executable(
    server: LanguageServer, environment: Mapping[str, str]
) -> str | None:
    return shutil.which(server.executable, path=environment.get("PATH", ""))
