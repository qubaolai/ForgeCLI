"""LSP 的九种操作: 每种对应哪个 LSP 方法, 要什么参数, 回来的东西怎么渲染成文字.

位置一律按 1 起算 (与 Read 的行号一致), 发给服务器前减一, 收回来加一.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from forgecli.tools.builtin.LSP.client import LspClient

__all__ = ["OPERATIONS", "needs_position", "needs_query", "run_operation"]

OPERATIONS = (
    "goToDefinition",
    "findReferences",
    "hover",
    "documentSymbol",
    "workspaceSymbol",
    "goToImplementation",
    "prepareCallHierarchy",
    "incomingCalls",
    "outgoingCalls",
)

_POSITIONAL = frozenset(
    {
        "goToDefinition",
        "findReferences",
        "hover",
        "goToImplementation",
        "prepareCallHierarchy",
        "incomingCalls",
        "outgoingCalls",
    }
)

# LSP SymbolKind 的编号 -> 名字 (规范 3.17 的表).
_SYMBOL_KINDS = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum_member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type_parameter",
}

# 一次最多渲染多少条位置. 引用可能有上千处, 全列出来没人看得完.
MAX_LOCATIONS = 200

# 读源码行的回调: 给一个路径返回它的文本, 读不了返回 None.
ReadText = Callable[[str], str | None]


def needs_position(operation: str) -> bool:
    return operation in _POSITIONAL


def needs_query(operation: str) -> bool:
    return operation == "workspaceSymbol"


def run_operation(
    client: LspClient,
    operation: str,
    *,
    path: str,
    line: int,
    character: int,
    query: str,
    read_text: ReadText,
) -> str:
    """发请求并把结果渲染成给模型看的文字."""
    uri = Path(path).as_uri()
    document = {"uri": uri}
    position = {"line": line - 1, "character": character - 1}
    at = {"textDocument": document, "position": position}

    if operation == "goToDefinition":
        result = client.request("textDocument/definition", at)
        return _render_locations(result, read_text, empty="No definition found.")
    if operation == "goToImplementation":
        result = client.request("textDocument/implementation", at)
        return _render_locations(result, read_text, empty="No implementation found.")
    if operation == "findReferences":
        params = dict(at)
        params["context"] = {"includeDeclaration": True}
        result = client.request("textDocument/references", params)
        return _render_locations(result, read_text, empty="No references found.")
    if operation == "hover":
        result = client.request("textDocument/hover", at)
        return _render_hover(result)
    if operation == "documentSymbol":
        result = client.request(
            "textDocument/documentSymbol", {"textDocument": document}
        )
        return _render_document_symbols(result)
    if operation == "workspaceSymbol":
        result = client.request("workspace/symbol", {"query": query})
        return _render_workspace_symbols(result)
    if operation == "prepareCallHierarchy":
        items = client.request("textDocument/prepareCallHierarchy", at)
        return _render_hierarchy_items(
            items, empty="No call hierarchy item at this position."
        )
    if operation == "incomingCalls":
        items = client.request("textDocument/prepareCallHierarchy", at)
        item = _first_item(items)
        if item is None:
            return "No call hierarchy item at this position."
        calls = client.request("callHierarchy/incomingCalls", {"item": item})
        return _render_calls(
            calls, key="from", label="called from", empty="No callers found."
        )
    if operation == "outgoingCalls":
        items = client.request("textDocument/prepareCallHierarchy", at)
        item = _first_item(items)
        if item is None:
            return "No call hierarchy item at this position."
        calls = client.request("callHierarchy/outgoingCalls", {"item": item})
        return _render_calls(calls, key="to", label="calls", empty="No callees found.")
    return f"Unsupported operation: {operation}"


# ---- 渲染 ----


def _path_of(uri: object) -> str:
    if not isinstance(uri, str):
        return ""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    return unquote(parsed.path)


def _location_of(item: object) -> tuple[str, int, int] | None:
    """Location 或 LocationLink -> (路径, 行, 列), 1 起算."""
    if not isinstance(item, dict):
        return None
    uri = item.get("uri")
    range_ = item.get("range")
    if uri is None:
        uri = item.get("targetUri")
        range_ = item.get("targetSelectionRange")
        if range_ is None:
            range_ = item.get("targetRange")
    if not isinstance(range_, dict):
        return None
    start = range_.get("start")
    if not isinstance(start, dict):
        return None
    line = int(start.get("line", 0)) + 1
    character = int(start.get("character", 0)) + 1
    return _path_of(uri), line, character


def _source_line(
    path: str, line: int, read_text: ReadText, cache: dict[str, list[str]]
) -> str:
    lines = cache.get(path)
    if lines is None:
        lines = []
        text = read_text(path)
        if text is not None:
            lines = text.splitlines()
        cache[path] = lines
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def _render_locations(result: object, read_text: ReadText, *, empty: str) -> str:
    items: list[object] = []
    if isinstance(result, list):
        items = result
    elif isinstance(result, dict):
        items = [result]
    if not items:
        return empty
    cache: dict[str, list[str]] = {}
    lines: list[str] = []
    for item in items[:MAX_LOCATIONS]:
        located = _location_of(item)
        if located is None:
            continue
        path, line, character = located
        source = _source_line(path, line, read_text, cache)
        entry = f"{path}:{line}:{character}"
        if source:
            entry = f"{entry}  {source}"
        lines.append(entry)
    if len(items) > MAX_LOCATIONS:
        lines.append(
            f"[{len(items)} locations total, showing the first {MAX_LOCATIONS}]"
        )
    if not lines:
        return empty
    return "\n".join(lines)


def _render_hover(result: object) -> str:
    if not isinstance(result, dict):
        return "No hover information at this position."
    contents = result.get("contents")
    text = _markup_text(contents)
    if not text.strip():
        return "No hover information at this position."
    return text.strip()


def _markup_text(contents: object) -> str:
    """MarkedString | MarkedString[] | MarkupContent -> 纯文本."""
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        value = contents.get("value", "")
        language = contents.get("language")
        if isinstance(language, str) and language:
            return f"```{language}\n{value}\n```"
        return str(value)
    if isinstance(contents, list):
        parts: list[str] = []
        for item in contents:
            parts.append(_markup_text(item))
        return "\n\n".join(parts)
    return ""


def _kind_name(kind: object) -> str:
    if isinstance(kind, int):
        return _SYMBOL_KINDS.get(kind, f"kind{kind}")
    return "symbol"


def _render_document_symbols(result: object) -> str:
    if not isinstance(result, list) or not result:
        return "No symbols in this file."
    lines: list[str] = []
    for item in result:
        _append_symbol(item, 0, lines)
    return "\n".join(lines)


def _append_symbol(item: object, depth: int, lines: list[str]) -> None:
    """DocumentSymbol (带 children) 或 SymbolInformation (扁平) 都渲染成一行."""
    if not isinstance(item, dict):
        return
    name = str(item.get("name", ""))
    kind = _kind_name(item.get("kind"))
    range_ = item.get("selectionRange")
    if range_ is None:
        range_ = item.get("range")
    if range_ is None:
        location = item.get("location")
        if isinstance(location, dict):
            range_ = location.get("range")
    line = 0
    if isinstance(range_, dict) and isinstance(range_.get("start"), dict):
        line = int(range_["start"].get("line", 0)) + 1
    indent = "  " * depth
    lines.append(f"{indent}{kind} {name}  :{line}")
    children = item.get("children")
    if isinstance(children, list):
        for child in children:
            _append_symbol(child, depth + 1, lines)


def _render_workspace_symbols(result: object) -> str:
    if not isinstance(result, list) or not result:
        return "No matching symbols in the workspace."
    lines: list[str] = []
    for item in result[:MAX_LOCATIONS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", ""))
        kind = _kind_name(item.get("kind"))
        located = _location_of(item.get("location"))
        where = ""
        if located is not None:
            where = f"  {located[0]}:{located[1]}"
        container = item.get("containerName")
        if isinstance(container, str) and container:
            name = f"{container}.{name}"
        lines.append(f"{kind} {name}{where}")
    if len(result) > MAX_LOCATIONS:
        lines.append(
            f"[{len(result)} symbols total, showing the first {MAX_LOCATIONS}]"
        )
    return "\n".join(lines)


def _first_item(items: object) -> dict[str, Any] | None:
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return None


def _describe_item(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    name = str(item.get("name", ""))
    kind = _kind_name(item.get("kind"))
    located = _location_of(
        {"uri": item.get("uri"), "range": item.get("selectionRange")}
    )
    where = ""
    if located is not None:
        where = f"  {located[0]}:{located[1]}:{located[2]}"
    detail = item.get("detail")
    if isinstance(detail, str) and detail:
        return f"{kind} {name} ({detail}){where}"
    return f"{kind} {name}{where}"


def _render_hierarchy_items(items: object, *, empty: str) -> str:
    if not isinstance(items, list) or not items:
        return empty
    lines: list[str] = []
    for item in items:
        lines.append(_describe_item(item))
    return "\n".join(lines)


def _render_calls(calls: object, *, key: str, label: str, empty: str) -> str:
    if not isinstance(calls, list) or not calls:
        return empty
    lines: list[str] = []
    for call in calls[:MAX_LOCATIONS]:
        if not isinstance(call, dict):
            continue
        item = call.get(key)
        described = _describe_item(item)
        ranges = call.get("fromRanges")
        spots: list[str] = []
        if isinstance(ranges, list):
            for range_ in ranges:
                if isinstance(range_, dict) and isinstance(range_.get("start"), dict):
                    spots.append(str(int(range_["start"].get("line", 0)) + 1))
        if spots:
            lines.append(f"{label} {described}  (lines {', '.join(spots)})")
        else:
            lines.append(f"{label} {described}")
    if len(calls) > MAX_LOCATIONS:
        lines.append(f"[{len(calls)} calls total, showing the first {MAX_LOCATIONS}]")
    return "\n".join(lines)
