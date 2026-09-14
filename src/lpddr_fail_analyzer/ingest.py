"""Ingest SLT Excel / CSV fail_msg tables with header search and fill-forward."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from lpddr_fail_analyzer.hexutil import cell_to_str, is_empty, parse_int_field

CANONICAL_COLUMNS = [
    "site",
    "slot",
    "loop",
    "pattern_name",
    "linear_addr",
    "row",
    "bank",
    "col",
    "exp_value",
    "rd_value",
    "reread1",
    "reread2",
    "reread3",
    "xor_val1",
]

FILL_FORWARD_CANON = ["site", "slot", "loop", "pattern_name"]

# Tokens that identify the true header row (fail_msg rows 1–2 are titles).
HEADER_TOKEN_GROUPS: dict[str, tuple[str, ...]] = {
    "site": ("site",),
    "slot": ("slot",),
    "loop": ("loop",),
    "pattern_name": ("pattern name", "pattern_name", "pattern"),
    "linear_addr": ("linear addr", "linear_addr", "linear address", "linearaddr"),
    "row": ("row",),
    "bank": ("bank",),
    "col": ("col", "column"),
    "exp_value": ("exp value", "exp_value", "exp"),
    "rd_value": ("rd value", "rd_value", "rd"),
    "reread1": ("re-read value1", "reread value1", "re-read1", "reread1"),
    "reread2": ("re-read value2", "reread value2", "re-read2", "reread2"),
    "reread3": ("re-read value3", "reread value3", "re-read3", "reread3"),
    "xor_val1": ("xor val1", "xor_val1", "xor value", "xor"),
}

REQUIRED_HEADER_KEYS = ("site", "slot", "row", "bank", "col")

SUMMARY_KEYS = (
    "PN",
    "LOT ID",
    "测试温度",
    "VDD1",
    "VDD2",
    "VDDQ",
    "测试颗粒总数量",
    "良品总数",
    "良率",
    "开始时间",
    "结束时间",
)


@dataclass
class BatchMeta:
    source_path: Path
    pn: str | None = None
    lot_id: str | None = None
    temperature: str | None = None
    vdd1: str | None = None
    vdd2: str | None = None
    vddq: str | None = None
    total_dies: str | None = None
    good_dies: str | None = None
    yield_rate: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    extras: dict[str, str] = field(default_factory=dict)

    def run_name(self) -> str:
        if self.lot_id:
            return _safe_name(self.lot_id)
        return _safe_name(self.source_path.stem)


def _safe_name(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text.strip())
    return cleaned.strip("._") or "run"


def _norm_header_token(value: Any) -> str:
    text = cell_to_str(value)
    if text is None:
        return ""
    return " ".join(text.lower().replace("_", " ").split())


def _match_canonical(token: str) -> str | None:
    if not token:
        return None
    for canon, aliases in HEADER_TOKEN_GROUPS.items():
        if token in aliases:
            return canon
    return None


def find_header_row(rows: Iterable[Iterable[Any]], max_scan: int = 40) -> tuple[int, dict[int, str]]:
    """Return (header_row_index, {col_index: canonical_name}) for the fail_msg header."""
    for idx, row in enumerate(rows):
        if idx >= max_scan:
            break
        mapping: dict[int, str] = {}
        tokens = [_norm_header_token(cell) for cell in row]
        for col_i, token in enumerate(tokens):
            canon = _match_canonical(token)
            if canon and canon not in mapping.values():
                mapping[col_i] = canon
        if all(key in mapping.values() for key in REQUIRED_HEADER_KEYS):
            return idx, mapping
    raise ValueError(
        "Could not find fail_msg header row containing Site, Slot, ROW, BANK, COL "
        "(and preferably Loop, Pattern Name, Linear ADDR)."
    )


def _ffill_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    present = [c for c in columns if c in df.columns]
    if not present:
        return df
    df = df.copy()
    for col in present:
        df[col] = df[col].replace({"": pd.NA})
        df[col] = df[col].ffill()
    return df


def _to_display(series: pd.Series) -> pd.Series:
    return series.map(cell_to_str)


def normalize_fail_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Map a table (with or without a title block) onto canonical fail_msg columns."""
    if raw.empty:
        raise ValueError("fail_msg table is empty")

    header_idx, col_map = find_header_row(raw.itertuples(index=False, name=None))
    body = raw.iloc[header_idx + 1 :].copy()
    body.columns = range(body.shape[1])

    out = pd.DataFrame()
    for col_i, canon in col_map.items():
        if col_i < body.shape[1]:
            out[canon] = body[col_i].values

    for canon in CANONICAL_COLUMNS:
        if canon not in out.columns:
            out[canon] = pd.NA

    for canon in CANONICAL_COLUMNS:
        out[canon] = _to_display(out[canon])

    out = _ffill_columns(out, FILL_FORWARD_CANON)

    out["row_i"] = out["row"].map(parse_int_field)
    out["bank_i"] = out["bank"].map(parse_int_field)
    out["col_i"] = out["col"].map(parse_int_field)
    out["linear_i"] = out["linear_addr"].map(parse_int_field)
    out["loop_i"] = out["loop"].map(parse_int_field)

    # Keep rows that look like fail addresses.
    mask = out["row_i"].notna() & out["bank_i"].notna() & out["col_i"].notna()
    out = out.loc[mask].copy()
    if out.empty:
        raise ValueError("No fail address rows after header detection / fill-forward")

    # Drop leftover title rows accidentally captured (no site/slot after ffill).
    out = out[out["site"].notna() & out["slot"].notna()].copy()
    out.reset_index(drop=True, inplace=True)
    return out[CANONICAL_COLUMNS + ["row_i", "bank_i", "col_i", "linear_i", "loop_i"]]


def _read_table_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, header=None, dtype=object, encoding="utf-8-sig")


def _read_fail_msg_xlsx(path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet_name = _pick_fail_sheet(xl.sheet_names)
    return pd.read_excel(xl, sheet_name=sheet_name, header=None, dtype=object)


def _pick_fail_sheet(names: list[str]) -> str:
    lowered = {n.lower().strip(): n for n in names}
    for key in ("fail_msg", "fail msg", "failmsg"):
        if key in lowered:
            return lowered[key]
    for n in names:
        if "fail" in n.lower():
            return n
    if len(names) == 1:
        return names[0]
    raise ValueError(f"No fail_msg sheet found in {names}")


def _pick_summary_sheet(names: list[str]) -> str | None:
    for n in names:
        if n.lower().strip() in {"summary", "汇总"}:
            return n
    return None


def parse_summary_sheet(path: Path) -> BatchMeta:
    meta = BatchMeta(source_path=path)
    if path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
        return meta
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = _pick_summary_sheet(xl.sheet_names)
    if sheet is None:
        return meta
    raw = pd.read_excel(xl, sheet_name=sheet, header=None, dtype=object)
    kv = _extract_summary_kv(raw)
    meta.pn = kv.get("PN")
    meta.lot_id = kv.get("LOT ID")
    meta.temperature = kv.get("测试温度")
    meta.vdd1 = kv.get("VDD1")
    meta.vdd2 = kv.get("VDD2")
    meta.vddq = kv.get("VDDQ")
    meta.total_dies = kv.get("测试颗粒总数量")
    meta.good_dies = kv.get("良品总数")
    meta.yield_rate = kv.get("良率")
    meta.start_time = kv.get("开始时间")
    meta.end_time = kv.get("结束时间")
    meta.extras = {k: v for k, v in kv.items() if k not in SUMMARY_KEYS}
    return meta


def _extract_summary_kv(raw: pd.DataFrame) -> dict[str, str]:
    wanted = {k.casefold(): k for k in SUMMARY_KEYS}
    found: dict[str, str] = {}

    def consider_key(key_raw: Any, value_raw: Any) -> None:
        key = cell_to_str(key_raw)
        val = cell_to_str(value_raw)
        if key is None or val is None:
            return
        canon = wanted.get(key.casefold())
        if canon and canon not in found:
            found[canon] = val

    nrows, ncols = raw.shape
    for r in range(nrows):
        for c in range(ncols):
            key = cell_to_str(raw.iat[r, c])
            if key is None:
                continue
            # Prefer the cell to the right, then below.
            right = raw.iat[r, c + 1] if c + 1 < ncols else None
            below = raw.iat[r + 1, c] if r + 1 < nrows else None
            if not is_empty(right):
                consider_key(key, right)
            elif not is_empty(below):
                consider_key(key, below)
    return found


def load_fails(path: Path) -> tuple[pd.DataFrame, BatchMeta]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        raw = _read_fail_msg_xlsx(path)
        meta = parse_summary_sheet(path)
    elif suffix in {".csv", ".tsv"}:
        raw = _read_table_csv(path)
        meta = BatchMeta(source_path=path)
    else:
        raise ValueError(f"Unsupported input type: {path.suffix}")
    fails = normalize_fail_frame(raw)
    meta.source_path = path
    return fails, meta
