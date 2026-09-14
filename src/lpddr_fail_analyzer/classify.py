"""Rule-based structural classification of per-die fail addresses.

Labels are *suspects* only — never SI / ECC / row-hammer root-cause claims.
Priority is strong → weak as listed in PRIMARY_ORDER.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from lpddr_fail_analyzer.hexutil import fmt_hex, parse_hex_dump

# Canonical labels (machine names used in summary.csv).
SINGLE_BIT = "single_bit_stuck"
BAD_COLUMN = "bad_column"
BAD_ROW = "bad_row"
BANK_LOCAL = "bank_local_cluster"
SPATIAL_2D = "spatial_2d_cluster"
COUPLING = "coupling_suspect"
SCATTER = "scatter"

PRIMARY_ORDER = (
    SINGLE_BIT,
    BAD_COLUMN,
    BAD_ROW,
    BANK_LOCAL,
    SPATIAL_2D,
    COUPLING,
    SCATTER,
)

LABEL_ZH = {
    SINGLE_BIT: "单点/单比特卡住嫌疑",
    BAD_COLUMN: "坏列/列主导",
    BAD_ROW: "坏行/行主导",
    BANK_LOCAL: "Bank 局部聚集",
    SPATIAL_2D: "二维空间聚集",
    COUPLING: "相邻耦合嫌疑",
    SCATTER: "散布/无明显结构",
}

# Thresholds (unique cells, not raw fail-row counts, unless noted).
MIN_LINE_SPAN = 4
LINE_SHARE = 0.40
BANK_UNIQUE_SHARE = 0.60
BANK_FAILROW_SHARE = 0.70
CLUSTER_WINDOW = 16
CLUSTER_FRAC = 0.50
COUPLING_FRAC = 0.40
MIN_CLUSTER_CELLS = 6


@dataclass
class DieAnalysis:
    site: str
    slot: str
    n_fail_rows: int
    n_unique_cells: int
    n_patterns: int
    n_loops: int
    primary_label: str
    secondary_labels: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    pattern_counts: dict[str, int] = field(default_factory=dict)
    top_rows: list[tuple[int, int]] = field(default_factory=list)
    top_cols: list[tuple[int, int]] = field(default_factory=list)
    top_banks: list[tuple[int, int]] = field(default_factory=list)
    xor_hints: list[str] = field(default_factory=list)
    unique_cells: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def die_id(self) -> str:
        return f"S{self.site}_Q{self.slot}"


def unique_cells_from_df(df: pd.DataFrame) -> list[tuple[int, int, int]]:
    cells = (
        df[["row_i", "bank_i", "col_i"]]
        .dropna()
        .astype(int)
        .drop_duplicates()
        .itertuples(index=False, name=None)
    )
    return [(int(r), int(b), int(c)) for r, b, c in cells]


def _counter_share(counter: Counter[int], n: int) -> tuple[int | None, int, float]:
    if not counter or n <= 0:
        return None, 0, 0.0
    key, cnt = counter.most_common(1)[0]
    return key, cnt, cnt / n


def _neighbor_fraction(
    coords: list[tuple[int, int]],
    window: int,
    chebyshev: bool = True,
) -> float:
    """Fraction of (row, col) points that have another point within `window`."""
    n = len(coords)
    if n <= 1:
        return 0.0
    lookup = set(coords)
    hit = 0
    # Exact adjacency / window via set; for large n still OK at SLT fail sizes.
    for r, c in coords:
        found = False
        if window <= 2:
            for dr in range(-window, window + 1):
                for dc in range(-window, window + 1):
                    if dr == 0 and dc == 0:
                        continue
                    if not chebyshev and abs(dr) + abs(dc) > window:
                        continue
                    if (r + dr, c + dc) in lookup:
                        found = True
                        break
                if found:
                    break
        else:
            # Chebyshev window can be large; scan other points (n is typically hundreds).
            for r2, c2 in coords:
                if r2 == r and c2 == c:
                    continue
                if max(abs(r2 - r), abs(c2 - c)) <= window:
                    found = True
                    break
        if found:
            hit += 1
    return hit / n


def _adjacent_pair_fraction(coords: list[tuple[int, int]]) -> float:
    """Fraction of points that share an orthogonal neighbor (row±1 or col±1)."""
    lookup = set(coords)
    if len(coords) <= 1:
        return 0.0
    hit = 0
    for r, c in coords:
        if (r + 1, c) in lookup or (r - 1, c) in lookup or (r, c + 1) in lookup or (r, c - 1) in lookup:
            hit += 1
    return hit / len(coords)


def classify_unique_cells(cells: Iterable[tuple[int, int, int]]) -> dict[str, Any]:
    """Classify a die from unique (row, bank, col) tuples. Returns label flags + evidence."""
    uniq = list({(int(r), int(b), int(c)) for r, b, c in cells})
    n = len(uniq)
    flags: dict[str, bool] = {lab: False for lab in PRIMARY_ORDER}
    evidence: dict[str, Any] = {"n_unique_cells": n}

    if n == 0:
        flags[SCATTER] = True
        evidence["reason"] = "no unique cells"
        return {"flags": flags, "evidence": evidence}

    rows = Counter(r for r, _, _ in uniq)
    cols = Counter(c for _, _, c in uniq)
    banks = Counter(b for _, b, _ in uniq)
    top_row, top_row_n, top_row_share = _counter_share(rows, n)
    top_col, top_col_n, top_col_share = _counter_share(cols, n)
    top_bank, top_bank_n, top_bank_share = _counter_share(banks, n)

    evidence.update(
        {
            "n_unique_rows": len(rows),
            "n_unique_cols": len(cols),
            "n_unique_banks": len(banks),
            "top_row": top_row,
            "top_row_n": top_row_n,
            "top_row_share": round(top_row_share, 4),
            "top_col": top_col,
            "top_col_n": top_col_n,
            "top_col_share": round(top_col_share, 4),
            "top_bank": top_bank,
            "top_bank_n": top_bank_n,
            "top_bank_share": round(top_bank_share, 4),
        }
    )

    # Per-bank 1D structure (a bad column is often bank-local).
    per_bank_col_hit = False
    per_bank_row_hit = False
    bank_details = []
    cells_by_bank: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for r, b, c in uniq:
        cells_by_bank[b].append((r, c))
    for b, coords in cells_by_bank.items():
        bn = len(coords)
        b_rows = Counter(r for r, _ in coords)
        b_cols = Counter(c for _, c in coords)
        _, bcol_n, bcol_share = _counter_share(b_cols, bn)
        _, brow_n, brow_share = _counter_share(b_rows, bn)
        bank_details.append(
            {
                "bank": b,
                "n": bn,
                "col_share": round(bcol_share, 4),
                "row_share": round(brow_share, 4),
            }
        )
        if bn >= MIN_LINE_SPAN and bcol_n >= MIN_LINE_SPAN and bcol_share >= LINE_SHARE:
            per_bank_col_hit = True
        if bn >= MIN_LINE_SPAN and brow_n >= MIN_LINE_SPAN and brow_share >= LINE_SHARE:
            per_bank_row_hit = True
    evidence["per_bank"] = bank_details

    if n == 1:
        flags[SINGLE_BIT] = True
        evidence["reason"] = "single unique (ROW,BANK,COL)"
        return {"flags": flags, "evidence": evidence}

    col_dominant = (
        top_col_n is not None
        and top_col_n >= MIN_LINE_SPAN
        and top_col_share >= LINE_SHARE
        and top_col_n >= top_row_n
    ) or per_bank_col_hit
    # Global row-share, or any bank that is itself a full bad-row, even when
    # another bank is column-dominant (mixed dies → secondary label).
    row_dominant = (
        top_row_n is not None
        and top_row_n >= MIN_LINE_SPAN
        and top_row_share >= LINE_SHARE
        and top_row_n > top_col_n
    ) or per_bank_row_hit

    # If both 1D structures exist, keep both flags; primary order prefers column.
    if col_dominant:
        flags[BAD_COLUMN] = True
    if row_dominant:
        flags[BAD_ROW] = True

    bank_local = top_bank_share >= BANK_UNIQUE_SHARE and n >= MIN_LINE_SPAN
    if bank_local:
        flags[BANK_LOCAL] = True

    # 2D cluster / coupling only when the die is not already a clean 1D line.
    all_rc = [(r, c) for r, _, c in uniq]
    cluster_frac = _neighbor_fraction(all_rc, CLUSTER_WINDOW)
    couple_frac = _adjacent_pair_fraction(all_rc)
    evidence["cluster_neighbor_frac"] = round(cluster_frac, 4)
    evidence["adjacent_pair_frac"] = round(couple_frac, 4)

    one_d = flags[BAD_COLUMN] or flags[BAD_ROW]
    if (
        not one_d
        and n >= MIN_CLUSTER_CELLS
        and cluster_frac >= CLUSTER_FRAC
        and len(rows) >= 3
        and len(cols) >= 3
    ):
        flags[SPATIAL_2D] = True

    if not one_d and n >= MIN_CLUSTER_CELLS and couple_frac >= COUPLING_FRAC:
        flags[COUPLING] = True

    if not any(flags[lab] for lab in PRIMARY_ORDER if lab != SCATTER):
        flags[SCATTER] = True

    return {"flags": flags, "evidence": evidence}


def xor_bit_hints(xor_values: Iterable[Any], min_frac: float = 0.35) -> list[str]:
    bit_hits: Counter[int] = Counter()
    valid = 0
    for raw in xor_values:
        n = parse_hex_dump(raw)
        if n is None:
            continue
        valid += 1
        bit = 0
        x = int(n)
        while x:
            if x & 1:
                bit_hits[bit] += 1
            x >>= 1
            bit += 1
    if valid == 0:
        return []
    hints = []
    for bit, cnt in bit_hits.most_common(6):
        frac = cnt / valid
        if frac >= min_frac:
            hints.append(
                f"XOR bit{bit} (mask {fmt_hex(1 << bit)}) 在 {cnt}/{valid} ({frac:.0%}) 行置位"
            )
    return hints


def analyze_die(site: str, slot: str, df: pd.DataFrame) -> DieAnalysis:
    cells = unique_cells_from_df(df)
    classified = classify_unique_cells(cells)
    flags: dict[str, bool] = classified["flags"]
    evidence: dict[str, Any] = classified["evidence"]

    matched = [lab for lab in PRIMARY_ORDER if flags.get(lab)]
    if not matched:
        matched = [SCATTER]
    primary = matched[0]
    secondary = [lab for lab in matched[1:] if lab != SCATTER]

    # Multi-pattern stability note for single-bit.
    n_patterns = int(df["pattern_name"].nunique(dropna=True))
    if primary == SINGLE_BIT and n_patterns > 1:
        evidence["stable_across_patterns"] = True
        evidence["reason"] = (
            f"single unique cell observed across {n_patterns} patterns (stuck-like, still a suspect)"
        )

    row_counts = Counter(int(x) for x in df["row_i"].dropna().astype(int))
    col_counts = Counter(int(x) for x in df["col_i"].dropna().astype(int))
    bank_counts = Counter(int(x) for x in df["bank_i"].dropna().astype(int))

    failrow_share = 0.0
    if len(df) and bank_counts:
        failrow_share = bank_counts.most_common(1)[0][1] / len(df)
    evidence["top_bank_failrow_share"] = round(failrow_share, 4)
    if failrow_share >= BANK_FAILROW_SHARE and BANK_LOCAL not in [primary, *secondary]:
        # Strong fail-row concentration can still be a secondary bank-local hint.
        if BANK_LOCAL not in secondary and primary != BANK_LOCAL:
            secondary.append(BANK_LOCAL)
            flags[BANK_LOCAL] = True

    pattern_counts = (
        df["pattern_name"].fillna("(blank)").astype(str).value_counts().to_dict()
    )
    n_loops = int(df["loop"].nunique(dropna=True))

    return DieAnalysis(
        site=str(site),
        slot=str(slot),
        n_fail_rows=int(len(df)),
        n_unique_cells=len(cells),
        n_patterns=n_patterns,
        n_loops=n_loops,
        primary_label=primary,
        secondary_labels=secondary,
        evidence=evidence,
        pattern_counts={str(k): int(v) for k, v in pattern_counts.items()},
        top_rows=row_counts.most_common(5),
        top_cols=col_counts.most_common(5),
        top_banks=bank_counts.most_common(8),
        xor_hints=xor_bit_hints(df["xor_val1"].tolist()),
        unique_cells=cells,
    )


def analyze_all_dies(fails: pd.DataFrame) -> list[DieAnalysis]:
    results: list[DieAnalysis] = []
    grouped = fails.groupby(["site", "slot"], dropna=False, sort=True)
    for (site, slot), sub in grouped:
        results.append(analyze_die(str(site), str(slot), sub))
    return results
