import pandas as pd

from lpddr_fail_analyzer.classify import (
    BAD_COLUMN,
    BAD_ROW,
    SINGLE_BIT,
    analyze_die,
    classify_unique_cells,
    xor_bit_hints,
)


def _die_frame(cells, pattern="pA"):
    """cells: iterable of (row, bank, col)."""
    rows = []
    for i, (r, b, c) in enumerate(cells):
        rows.append(
            {
                "site": "1",
                "slot": "0",
                "loop": "1",
                "pattern_name": pattern,
                "linear_addr": hex(i),
                "row": hex(r),
                "bank": str(b),
                "col": hex(c),
                "exp_value": "0",
                "rd_value": "1",
                "reread1": "1",
                "reread2": "1",
                "reread3": "1",
                "xor_val1": "80",
                "row_i": r,
                "bank_i": b,
                "col_i": c,
                "linear_i": i,
                "loop_i": 1,
            }
        )
    return pd.DataFrame(rows)


def test_col_dominant_unique_cells():
    cells = [(r, 0, 0x10) for r in range(0x100, 0x108)]
    result = classify_unique_cells(cells)
    assert result["flags"][BAD_COLUMN]
    assert not result["flags"][BAD_ROW]
    die = analyze_die("1", "0", _die_frame(cells))
    assert die.primary_label == BAD_COLUMN


def test_row_dominant_unique_cells():
    cells = [(0x200, 0, c) for c in range(0x0, 0x8)]
    result = classify_unique_cells(cells)
    assert result["flags"][BAD_ROW]
    assert not result["flags"][BAD_COLUMN]
    die = analyze_die("2", "3", _die_frame(cells))
    assert die.primary_label == BAD_ROW
    assert die.site == "2"
    assert die.slot == "3"


def test_xor_unprefixed_hex_bit7():
    hints = xor_bit_hints(["80", "80", "80"])
    assert hints
    assert "bit7" in hints[0]
    assert "0x80" in hints[0]


def test_single_bit_stable_across_patterns():
    cell = [(0x11, 3, 0x22)]
    df = pd.concat(
        [_die_frame(cell, "alpha"), _die_frame(cell, "beta")],
        ignore_index=True,
    )
    die = analyze_die("8", "1", df)
    assert die.primary_label == SINGLE_BIT
    assert die.n_unique_cells == 1
    assert die.n_patterns == 2
    assert die.evidence.get("stable_across_patterns") is True
