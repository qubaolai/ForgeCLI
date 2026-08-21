from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from forgecli.interfaces.runtime.tool_wiring import (
    _create_file,
    _move_file,
    _replace_file,
)


def test_create_never_overwrites_a_competing_target(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _create_file(target, "replace")

    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_replace_preserves_executable_mode(tmp_path: Path) -> None:
    target = tmp_path / "script.sh"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o755)

    _replace_file(target, "new")

    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_move_never_overwrites_a_competing_target(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    target = tmp_path / "target.txt"
    source.write_text("source", encoding="utf-8")
    target.write_text("target", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _move_file(str(source), str(target))

    assert source.read_text(encoding="utf-8") == "source"
    assert target.read_text(encoding="utf-8") == "target"
