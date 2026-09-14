"""Ingest SLT Excel / CSV fail_msg tables with header search and fill-forward."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from lpddr_fail_analyzer.hexutil import cell_to_str, is_empty, parse_int_field

log = logging.getLogger(__name__)

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

# Fields that mark a body row as a real fail-address candidate (not Excel padding).
# Site/Slot/Loop/Pattern are fill-forwarded, so they must not be used here.
ADDRESS_HINT_COLUMNS = (
    "row",
    "bank",
    "col",
    "linear_addr",
    "exp_value",
    "rd_value",
    "xor_val1",
    "reread1",
    "reread2",
    "reread3",
)

MAX_DROP_EXAMPLES = 8


@dataclass
class DropExample:
    reason: str
    site: str | None = None
    slot: str | None = None
    row: str | None = None
    bank: str | None = None
    col: str | None = None
    linear_addr: str | None = None

    def as_text(self) -> str:
        parts = [self.reason]
        for label, val in (
            ("Site", self.site),
            ("Slot", self.slot),
            ("ROW", self.row),
            ("BANK", self.bank),
            ("COL", self.col),
            ("Linear ADDR", self.linear_addr),
        ):
            if val is not None:
                parts.append(f"{label}={val}")
        return "; ".join(parts)


@dataclass
class IngestStats:
    """How many candidate fail rows were kept vs explicitly dropped (never silent)."""

    candidate_rows: int = 0
    kept_rows: int = 0
    dropped_rows: int = 0
    dropped_bad_address: int = 0
    dropped_no_identity: int = 0
    skipped_padding_rows: int = 0
    examples: list[DropExample] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return self.dropped_rows > 0

    def warning_lines(self) -> list[str]:
        if self.dropped_rows <= 0:
            return []
        lines = [
            f"dropped_rows={self.dropped_rows} "
            f"(unparseable ROW/BANK/COL={self.dropped_bad_address}, "
            f"missing Site/Slot after fill-forward={self.dropped_no_identity}); "
            f"kept_rows={self.kept_rows} / candidate_rows={self.candidate_rows}. "
            "Classification used kept rows only — this is not a fully clean run."
        ]
        for ex in self.examples:
            lines.append(f"  drop example: {ex.as_text()}")
        return lines

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


def _nonempty_mask(series: pd.Series) -> pd.Series:
    return series.map(lambda v: not is_empty(v))


def _drop_example(reason: str, rec: pd.Series) -> DropExample:
    def _s(key: str) -> str | None:
        return cell_to_str(rec[key]) if key in rec.index else None

    return DropExample(
        reason=reason,
        site=_s("site"),
        slot=_s("slot"),
        row=_s("row"),
        bank=_s("bank"),
        col=_s("col"),
        linear_addr=_s("linear_addr"),
    )


class NoValidAddressRowsError(ValueError):
    """Every candidate address row was unparseable or lacked Site/Slot — fail hard."""


def normalize_fail_frame(raw: pd.DataFrame) -> tuple[pd.DataFrame, IngestStats]:
    """Map a table (with or without a title block) onto canonical fail_msg columns.

    Rows with unparseable ROW/BANK/COL are **not** silently dropped: they are counted
    in ``IngestStats.dropped_rows``. Padding / fully empty body rows are skipped and
    do not count as dropped. If every candidate row is bad, raises
    ``NoValidAddressRowsError``.
    """
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

    hint = pd.Series(False, index=out.index)
    for col in ADDRESS_HINT_COLUMNS:
        if col in out.columns:
            hint = hint | _nonempty_mask(out[col])

    parseable = out["row_i"].notna() & out["bank_i"].notna() & out["col_i"].notna()
    has_id = out["site"].notna() & out["slot"].notna()
    candidate = hint
    bad_address = candidate & ~parseable
    no_identity = candidate & parseable & ~has_id
    kept = candidate & parseable & has_id

    stats = IngestStats(
        candidate_rows=int(candidate.sum()),
        kept_rows=int(kept.sum()),
        dropped_bad_address=int(bad_address.sum()),
        dropped_no_identity=int(no_identity.sum()),
        skipped_padding_rows=int((~candidate).sum()),
    )
    stats.dropped_rows = stats.dropped_bad_address + stats.dropped_no_identity

    examples: list[DropExample] = []
    for _, rec in out.loc[bad_address].head(MAX_DROP_EXAMPLES).iterrows():
        examples.append(_drop_example("unparseable ROW/BANK/COL", rec))
    remain = MAX_DROP_EXAMPLES - len(examples)
    if remain > 0:
        for _, rec in out.loc[no_identity].head(remain).iterrows():
            examples.append(_drop_example("missing Site/Slot after fill-forward", rec))
    stats.examples = examples

    if stats.dropped_rows:
        for line in stats.warning_lines():
            log.warning("%s", line)
    else:
        log.info(
            "ingest kept_rows=%s candidate_rows=%s padding_skipped=%s",
            stats.kept_rows,
            stats.candidate_rows,
            stats.skipped_padding_rows,
        )

    if stats.kept_rows == 0:
        raise NoValidAddressRowsError(
            "No valid fail address rows after header detection / fill-forward "
            f"(candidate_rows={stats.candidate_rows}, dropped_rows={stats.dropped_rows}, "
            f"dropped_bad_address={stats.dropped_bad_address}, "
            f"dropped_no_identity={stats.dropped_no_identity}). "
            "All address rows were bad — refusing to emit a fake-clean report."
        )

    out = out.loc[kept].copy()
    out.reset_index(drop=True, inplace=True)
    return out[CANONICAL_COLUMNS + ["row_i", "bank_i", "col_i", "linear_i", "loop_i"]], stats


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


def _pick_board_sheet(names: list[str]) -> str | None:
    lowered = {n.lower().strip(): n for n in names}
    for key in ("board_msg", "board msg", "boardmsg"):
        if key in lowered:
            return lowered[key]
    for n in names:
        if "board" in n.lower() and "fail" not in n.lower():
            return n
    return None


BOARD_HEADER_TOKEN_GROUPS: dict[str, tuple[str, ...]] = {
    "site": ("site",),
    "slot": ("slot",),
    "result": ("result",),
    "bin": ("bin code", "bincode", "bin"),
    "fail_loop": ("fail loop", "failloop"),
    "elapsed": ("elapsed time", "elapsed"),
    "lot_id": ("lot id", "lot"),
    "pn": ("pn",),
}

BOARD_REQUIRED_HEADER_KEYS = ("site", "slot", "result")

_FAIL_RESULT_TOKENS = frozenset({"fail", "failed", "ng", "不良", "失败"})


def _match_board_canonical(token: str) -> str | None:
    if not token:
        return None
    cleaned = " ".join(token.replace("*", " ").split())
    for canon, aliases in BOARD_HEADER_TOKEN_GROUPS.items():
        if cleaned in aliases:
            return canon
    return None


def is_fail_result(value: Any) -> bool:
    text = cell_to_str(value)
    if text is None:
        return False
    stripped = text.strip()
    if stripped in {"不良", "失败"}:
        return True
    return stripped.casefold() in {t.casefold() for t in _FAIL_RESULT_TOKENS}


def find_board_header(rows: Iterable[Iterable[Any]], max_scan: int = 20) -> tuple[int, dict[int, str]]:
    """Return (body_start_row, {col_index: canonical_name}) for board_msg.

    Real SLT files use a two-row header: Result/Bin on the first row, Site/Slot on the second
    (under a merged Board ID label). Accumulates tokens across consecutive header rows.
    """
    prev_map: dict[int, str] = {}
    for idx, row in enumerate(rows):
        if idx >= max_scan:
            break
        this_map: dict[int, str] = {}
        for col_i, cell in enumerate(row):
            token = _norm_header_token(cell)
            canon = _match_board_canonical(token)
            if canon and canon not in this_map.values():
                this_map[col_i] = canon
        merged = dict(prev_map)
        merged.update(this_map)
        if all(key in merged.values() for key in BOARD_REQUIRED_HEADER_KEYS):
            return idx + 1, merged
        prev_map = merged
    raise ValueError(
        "Could not find board_msg header row(s) containing Site, Slot, Result"
    )


def _site_slot_sort_key(site: str, slot: str) -> tuple:
    def part(value: str) -> tuple[int, int | str]:
        try:
            return (0, int(value))
        except ValueError:
            return (1, value)

    return (part(site), part(slot))


@dataclass
class BoardDie:
    """One unique Site+Slot seen as fail on board_msg (multi-round rows collapsed)."""

    site: str
    slot: str
    n_board_rows: int
    n_fail_rows: int
    bins: list[str] = field(default_factory=list)

    @property
    def die_id(self) -> str:
        return f"S{self.site}_Q{self.slot}"


@dataclass
class BoardCensus:
    """Unique Site+Slot census from board_msg — never treat row/bin counts as 颗数."""

    present: bool
    sheet_name: str | None = None
    missing_reason: str | None = None
    header_error: str | None = None
    n_board_rows: int = 0
    n_unique_site_slot: int = 0
    n_fail_rows: int = 0
    fail_dies: list[BoardDie] = field(default_factory=list)

    @property
    def n_unique_fail_dies(self) -> int:
        return len(self.fail_dies)


def normalize_board_frame(raw: pd.DataFrame) -> BoardCensus:
    """Parse a board_msg table into unique Site+Slot fail dies (not row counts)."""
    if raw.empty:
        return BoardCensus(present=True, missing_reason="empty")

    body_idx, col_map = find_board_header(raw.itertuples(index=False, name=None))
    body = raw.iloc[body_idx:].copy()
    body.columns = range(body.shape[1])

    out = pd.DataFrame()
    for col_i, canon in col_map.items():
        if col_i < body.shape[1]:
            out[canon] = body[col_i].values
    for needed in ("site", "slot", "result"):
        if needed not in out.columns:
            out[needed] = pd.NA
    if "bin" not in out.columns:
        out["bin"] = pd.NA

    for col in out.columns:
        out[col] = _to_display(out[col])
    out = _ffill_columns(out, ["site", "slot"])

    has_any = _nonempty_mask(out["site"]) | _nonempty_mask(out["slot"]) | _nonempty_mask(out["result"])
    out = out.loc[has_any].copy()

    n_board_rows = int(len(out))
    n_unique = int(out.dropna(subset=["site", "slot"]).drop_duplicates(["site", "slot"]).shape[0])
    fail_mask = out["result"].map(is_fail_result)
    n_fail_rows = int(fail_mask.sum())

    fail_dies: list[BoardDie] = []
    identified = out.dropna(subset=["site", "slot"])
    for (site, slot), sub in identified.groupby(["site", "slot"], dropna=False, sort=False):
        fail_sub = sub[sub["result"].map(is_fail_result)]
        if fail_sub.empty:
            continue
        bins: list[str] = []
        if "bin" in fail_sub.columns:
            for raw_bin in fail_sub["bin"].tolist():
                text = cell_to_str(raw_bin)
                if text and text not in bins:
                    bins.append(text)
        fail_dies.append(
            BoardDie(
                site=str(site),
                slot=str(slot),
                n_board_rows=int(len(sub)),
                n_fail_rows=int(len(fail_sub)),
                bins=bins,
            )
        )
    fail_dies.sort(key=lambda d: _site_slot_sort_key(d.site, d.slot))

    return BoardCensus(
        present=True,
        n_board_rows=n_board_rows,
        n_unique_site_slot=n_unique,
        n_fail_rows=n_fail_rows,
        fail_dies=fail_dies,
    )


def load_board_census(path: Path) -> BoardCensus:
    """Load unique board-fail Site+Slot if a board_msg sheet exists."""
    path = Path(path)
    if path.suffix.lower() not in {".xlsx", ".xlsm", ".xls"}:
        return BoardCensus(present=False, missing_reason="not_excel")
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = _pick_board_sheet(xl.sheet_names)
    if sheet is None:
        return BoardCensus(present=False, missing_reason="no_sheet")
    raw = pd.read_excel(xl, sheet_name=sheet, header=None, dtype=object)
    try:
        census = normalize_board_frame(raw)
    except ValueError as exc:
        log.warning("board_msg header not recognized: %s", exc)
        return BoardCensus(
            present=True,
            sheet_name=sheet,
            missing_reason="bad_header",
            header_error=str(exc),
        )
    census.sheet_name = sheet
    return census


def board_fails_without_dump(
    board: BoardCensus,
    analyzable: Iterable[tuple[str, str]],
) -> list[BoardDie]:
    """board_msg fail Site+Slot that have no fail_msg address dump."""
    have = {(str(site), str(slot)) for site, slot in analyzable}
    return [d for d in board.fail_dies if (d.site, d.slot) not in have]


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


def load_fails(path: Path) -> tuple[pd.DataFrame, BatchMeta, IngestStats]:
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
    fails, stats = normalize_fail_frame(raw)
    meta.source_path = path
    return fails, meta, stats
