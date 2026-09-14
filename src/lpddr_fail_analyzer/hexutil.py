"""Parse DRAM address / XOR fields that arrive as hex strings, ints, or Excel floats."""

from __future__ import annotations

import math
from typing import Any


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def cell_to_str(value: Any) -> str | None:
    if is_empty(value):
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    return text or None


def _as_text_int(value: Any) -> str | None:
    if is_empty(value):
        return None
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return str(int(value))
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "-"}:
        return None
    return text


def parse_int_field(value: Any) -> int | None:
    """Parse Linear ADDR / ROW / BANK / COL: 0x… as hex, else decimal, else hex."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return int(value)
        return None
    text = _as_text_int(value)
    if text is None:
        return None
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        return int(text, 10)
    except ValueError:
        try:
            return int(text, 16)
        except ValueError:
            return None


def parse_hex_dump(value: Any) -> int | None:
    """Parse ATE hex dumps (XOR/EXP/RD) that usually omit the 0x prefix."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return int(value)
        return None
    text = _as_text_int(value)
    if text is None:
        return None
    if text.lower().startswith("0x"):
        text = text[2:]
    try:
        return int(text, 16)
    except ValueError:
        return parse_int_field(value)


def fmt_hex(value: int | None, width: int | None = None) -> str:
    if value is None:
        return ""
    if width:
        return f"0x{value:0{width}X}"
    return f"0x{value:X}"
