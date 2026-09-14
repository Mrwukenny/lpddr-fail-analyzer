"""Guards for Windows bat encoding (GBK cmd vs UTF-8, stock-watch-alert pattern)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "scripts" / "start-windows.bat"
_CJK = re.compile(r"[\u4e00-\u9fff]")
_FULLWIDTH_PARENS = re.compile(r"[\uff08\uff09]")


def _bat_text() -> str:
    return BAT.read_text(encoding="utf-8")


def test_chcp_65001_before_any_chinese():
    text = _bat_text()
    chcp_at = text.lower().index("chcp 65001")
    first_cjk = min(m.start() for m in _CJK.finditer(text))
    assert chcp_at < first_cjk
    assert chcp_at < 80, "chcp 65001 should be at the top of the bat"


def test_chinese_echo_lines_quoted_no_fullwidth_parens():
    text = _bat_text()
    assert _FULLWIDTH_PARENS.search(text) is None
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.lower().startswith("echo ") and _CJK.search(stripped):
            rest = stripped[5:].lstrip()
            assert rest.startswith('"'), f"line {i} Chinese echo must be quoted: {stripped}"
            assert rest.endswith('"'), f"line {i} Chinese echo must be quoted: {stripped}"


def test_bat_keeps_drag_drop_default_sample_and_uv_copy():
    text = _bat_text()
    assert "UV_LINK_MODE=copy" in text
    assert "PYTHONIOENCODING=utf-8" in text
    assert r"samples\sample_batch.xlsx" in text
    assert "%~1" in text
    assert "--out out" in text
    assert "lpddr_fail_analyzer analyze" in text
    assert "out\\report.md" in text or r"out\report.md" in text
    assert "编辑器" in text
