"""Bash 给模型的说明 (对应 BashTool/prompt.ts 的 getSimplePrompt)."""

from __future__ import annotations

from forgecli.core.tool.names import (
    BASH_TOOL_NAME,
    FILE_EDIT_TOOL_NAME,
    FILE_READ_TOOL_NAME,
    FILE_WRITE_TOOL_NAME,
    GLOB_TOOL_NAME,
    GREP_TOOL_NAME,
)

__all__ = ["DEFAULT_TIMEOUT_MS", "MAX_TIMEOUT_MS", "prompt"]

DEFAULT_TIMEOUT_MS = 120_000
MAX_TIMEOUT_MS = 600_000


def prompt(sandboxed: bool) -> str:
    sandbox_section = ""
    if sandboxed:
        sandbox_section = (
            "\n# Sandbox\n"
            "Commands run inside a sandbox: writes outside the working directories "
            "and network access are blocked at the system-call level unless the "
            "sandbox settings allow them. If a command fails with a permission error "
            "that looks like the sandbox, say so instead of retrying with sudo or a "
            "different path.\n"
        )
    return f"""Executes a given bash command and returns its output.

The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).

IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:

 - File search: Use {GLOB_TOOL_NAME} (NOT find or ls)
 - Content search: Use {GREP_TOOL_NAME} (NOT grep or rg)
 - Read files: Use {FILE_READ_TOOL_NAME} (NOT cat/head/tail)
 - Edit files: Use {FILE_EDIT_TOOL_NAME} (NOT sed/awk)
 - Write files: Use {FILE_WRITE_TOOL_NAME} (NOT echo >/cat <<EOF)
 - Communication: Output text directly (NOT echo/printf)
While the {BASH_TOOL_NAME} tool can do similar things, it is better to use the built-in tools as they provide a better user experience and make it easier to review tool calls and give permission.

# Instructions
 - If your command will create new directories or files, first use this tool to run `ls` to verify the parent directory exists and is the correct location.
 - Always quote file paths that contain spaces with double quotes in your command (e.g., cd "path with spaces/file.txt")
 - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.
 - You may specify an optional timeout in milliseconds (up to {MAX_TIMEOUT_MS}ms / {MAX_TIMEOUT_MS // 60000} minutes). By default, your command will timeout after {DEFAULT_TIMEOUT_MS}ms ({DEFAULT_TIMEOUT_MS // 60000} minutes).
 - When issuing multiple commands:
   - If the commands are independent and can run in parallel, make multiple {BASH_TOOL_NAME} tool calls in a single message. Example: if you need to run "git status" and "git diff", send a single message with two {BASH_TOOL_NAME} tool calls in parallel.
   - If the commands depend on each other and must run sequentially, use a single {BASH_TOOL_NAME} call with '&&' to chain them together.
   - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.
   - DO NOT use newlines to separate commands (newlines are ok in quoted strings).
 - For git commands:
   - Prefer to create a new commit rather than amending an existing commit.
   - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.
   - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue.
   - NEVER commit changes unless the user explicitly asks you to.
 - Avoid unnecessary `sleep` commands:
   - Do not sleep between commands that can run immediately - just run them.
   - Do not retry failing commands in a sleep loop - diagnose the root cause.
   - If you must poll an external process, use a check command rather than sleeping first, and keep any sleep short (1-5 seconds).
{sandbox_section}"""
