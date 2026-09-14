"""Linear ADDR consistency audit vs ROW/BANK/COL (no silent ignore).

This does **not** reverse a JEDEC map. When Linear ADDR is present it checks
that the tester fields agree with each other:

- the same Linear ADDR must not decode to two different (ROW, BANK, COL) cells
- the same (ROW, BANK, COL) must not map to Linear ADDR values that differ
  beyond a small burst/beat window (low bits)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from lpddr_fail_analyzer.hexutil import fmt_hex

log = logging.getLogger(__name__)

# Same (ROW,BANK,COL) may still have Linear ADDR beat/byte offsets in low bits.
# Differences outside this mask are treated as a real mismatch.
BURST_MASK = 0xFF
MAX_AUDIT_EXAMPLES = 8


@dataclass
class AddrMismatch:
    kind: str
    detail: str


@dataclass
class AddrAudit:
    n_rows: int = 0
    n_with_linear: int = 0
    n_without_linear: int = 0
    n_linear_collisions: int = 0
    n_cell_linear_spreads: int = 0
    n_mismatch: int = 0
    examples: list[AddrMismatch] = field(default_factory=list)

    @property
    def has_warnings(self) -> bool:
        return self.n_mismatch > 0

    def warning_lines(self) -> list[str]:
        if self.n_with_linear == 0:
            return ["Linear ADDR not present on kept rows — ADDR consistency audit skipped."]
        if not self.has_warnings:
            return [
                f"Linear ADDR consistency audit: {self.n_with_linear}/{self.n_rows} kept rows "
                "had Linear ADDR; no ROW/BANK/COL mismatches."
            ]
        lines = [
            f"Linear ADDR consistency audit: n_mismatch={self.n_mismatch} "
            f"(same Linear ADDR → different cell={self.n_linear_collisions}, "
            f"same cell → distant Linear ADDR={self.n_cell_linear_spreads})."
        ]
        for ex in self.examples:
            lines.append(f"  ADDR mismatch ({ex.kind}): {ex.detail}")
        return lines


def _cell_text(row: int, bank: int, col: int) -> str:
    return f"(ROW={fmt_hex(row)}, BANK={bank}, COL={fmt_hex(col)})"


def audit_linear_addr(fails: pd.DataFrame) -> AddrAudit:
    """Audit Linear ADDR vs ROW/BANK/COL on kept fail rows."""
    n_rows = int(len(fails))
    if n_rows == 0 or "linear_i" not in fails.columns:
        return AddrAudit(n_rows=n_rows)

    with_lin = fails[fails["linear_i"].notna()].copy()
    audit = AddrAudit(
        n_rows=n_rows,
        n_with_linear=int(len(with_lin)),
        n_without_linear=n_rows - int(len(with_lin)),
    )
    if with_lin.empty:
        for line in audit.warning_lines():
            log.info("%s", line)
        return audit

    examples: list[AddrMismatch] = []

    for lin, group in with_lin.groupby("linear_i", sort=False):
        cells = (
            group[["row_i", "bank_i", "col_i"]]
            .drop_duplicates()
            .astype(int)
            .itertuples(index=False, name=None)
        )
        cells_list = [(int(r), int(b), int(c)) for r, b, c in cells]
        if len(cells_list) > 1:
            audit.n_linear_collisions += 1
            if len(examples) < MAX_AUDIT_EXAMPLES:
                shown = ", ".join(_cell_text(*cell) for cell in cells_list[:4])
                examples.append(
                    AddrMismatch(
                        kind="linear_collision",
                        detail=f"Linear ADDR {fmt_hex(int(lin))} maps to multiple cells: {shown}",
                    )
                )

    for (row, bank, col), group in with_lin.groupby(["row_i", "bank_i", "col_i"], sort=False):
        linears = sorted({int(x) for x in group["linear_i"].tolist()})
        if len(linears) <= 1:
            continue
        base = linears[0]
        far = [x for x in linears[1:] if ((x ^ base) & ~BURST_MASK) != 0]
        if not far:
            continue
        audit.n_cell_linear_spreads += 1
        if len(examples) < MAX_AUDIT_EXAMPLES:
            shown = ", ".join(fmt_hex(x) for x in linears[:4])
            examples.append(
                AddrMismatch(
                    kind="cell_linear_spread",
                    detail=(
                        f"{_cell_text(int(row), int(bank), int(col))} maps to distant "
                        f"Linear ADDR values: {shown}"
                    ),
                )
            )

    audit.examples = examples
    audit.n_mismatch = audit.n_linear_collisions + audit.n_cell_linear_spreads

    for line in audit.warning_lines():
        if audit.has_warnings:
            log.warning("%s", line)
        else:
            log.info("%s", line)
    return audit
