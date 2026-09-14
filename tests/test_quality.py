"""P0 dropped-row accounting, P1 disclaimers, P2 Linear ADDR audit."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from lpddr_fail_analyzer.audit import audit_linear_addr
from lpddr_fail_analyzer.ingest import NoValidAddressRowsError, load_fails, normalize_fail_frame
from lpddr_fail_analyzer.pipeline import run_analyze
from lpddr_fail_analyzer.report import CHANNEL_RANK_DISCLAIMER, COL_GRANULARITY_DISCLAIMER

CSV_HEADER = (
    "Site,Slot,Loop,Pattern Name,Linear ADDR,ROW,BANK,COL,EXP Value,RD Value,"
    "Re-read value1,Re-read value2,Re-read value3,XOR Val1\n"
)


def _write_csv(path: Path, body: str) -> Path:
    path.write_text(CSV_HEADER + body, encoding="utf-8")
    return path


def test_unparseable_address_rows_are_counted_not_silent(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path / "half_broken.csv",
        "1,2,1,p,0x10,0x100,0,0x1,00,FF,FF,FF,FF,FF\n"
        "1,2,1,p,0x20,BOGUS,0,0x1,00,FF,FF,FF,FF,FF\n"
        "1,2,1,p,0x30,0x101,,0x2,00,FF,FF,FF,FF,FF\n"
        "1,2,1,p,0x40,0x102,0,0x3,00,FF,FF,FF,FF,FF\n"
        ",,,,,,,,,,,,,\n",
    )
    df, meta, stats = load_fails(csv_path)
    assert len(df) == 2
    assert stats.kept_rows == 2
    assert stats.dropped_bad_address == 2
    assert stats.dropped_rows == 2
    assert stats.incomplete
    assert stats.skipped_padding_rows >= 1
    assert any("unparseable ROW/BANK/COL" in ex.reason for ex in stats.examples)

    out = tmp_path / "half-out"
    result = run_analyze(csv_path, out)
    assert result.incomplete
    assert result.run_status == "incomplete"
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "INCOMPLETE" in text
    assert "dropped_rows=2" in text or "dropped_rows： **2**" in text
    assert "不是一次完全干净的成功运行" in text
    summary = (out / "summary.csv").read_text(encoding="utf-8")
    assert "incomplete" in summary
    assert "dropped_rows" in summary


def test_all_bad_address_rows_fail_hard(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path / "all_bad.csv",
        "1,2,1,p,0x10,???,0,0x1,00,FF,FF,FF,FF,FF\n"
        "1,2,1,p,0x20,zzz,1,not-a-col,00,FF,FF,FF,FF,FF\n",
    )
    with pytest.raises(NoValidAddressRowsError, match="dropped_rows=2"):
        load_fails(csv_path)

    out = tmp_path / "all-bad-out"
    with pytest.raises(NoValidAddressRowsError, match="fake-clean"):
        run_analyze(csv_path, out)
    assert not (out / "report.md").exists()


def test_cli_half_broken_exits_nonzero_and_writes_flagged_report(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path / "cli_half.csv",
        "9,8,1,foo,0x1,0x10,0,0x0,0,1,1,1,1,1\n"
        "9,8,1,foo,0x2,BADROW,0,0x0,0,1,1,1,1,1\n",
    )
    out = tmp_path / "cli-half"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "lpddr_fail_analyzer",
            "analyze",
            str(csv_path),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = proc.stderr + proc.stdout
    assert proc.returncode != 0, combined
    assert "dropped_rows" in combined
    assert (out / "report.md").is_file()
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "INCOMPLETE" in report
    assert "dropped_rows" in report


def test_cli_all_bad_fails_hard(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path / "cli_all_bad.csv",
        "9,8,1,foo,0x1,NOPE,0,0x0,0,1,1,1,1,1\n",
    )
    out = tmp_path / "cli-all-bad"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "lpddr_fail_analyzer",
            "analyze",
            str(csv_path),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert not (out / "report.md").exists()


def test_report_disclaimers_present(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path / "disc.csv",
        "1,0,1,p,0x10,0x1,0,0x2,00,FF,FF,FF,FF,FF\n",
    )
    out = tmp_path / "disc-out"
    run_analyze(csv_path, out)
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "single-channel view" in text
    assert "dual-channel mixing" in text
    assert CHANNEL_RANK_DISCLAIMER in text
    assert "tester decode granularity" in text
    assert "NOT JEDEC bare physical column" in text
    assert COL_GRANULARITY_DISCLAIMER in text


def test_linear_addr_collision_is_reported(tmp_path: Path):
    csv_path = _write_csv(
        tmp_path / "addr_collision.csv",
        "1,2,1,p,0x100,0x10,0,0x1,00,FF,FF,FF,FF,FF\n"
        "1,2,1,p,0x100,0x20,0,0x1,00,FF,FF,FF,FF,FF\n",
    )
    df, _, stats = load_fails(csv_path)
    assert stats.dropped_rows == 0
    audit = audit_linear_addr(df)
    assert audit.n_linear_collisions == 1
    assert audit.n_mismatch >= 1
    assert audit.has_warnings

    result = run_analyze(csv_path, tmp_path / "addr-out")
    assert result.run_status == "warnings"
    text = (result.out_dir / "report.md").read_text(encoding="utf-8")
    assert "WARNINGS" in text
    assert "n_mismatch" in text
    assert "Linear ADDR" in text
    assert "linear_collision" in text or "maps to multiple cells" in text
    summary = (result.out_dir / "summary.csv").read_text(encoding="utf-8")
    assert "warnings" in summary


def test_linear_addr_cell_spread_vs_burst_window():
    rows = []
    for linear, row, col in (
        (0x1000, 0x10, 0x1),
        (0x9000, 0x10, 0x1),  # same cell, high-bit linear change
        (0x2000, 0x11, 0x2),
        (0x2008, 0x11, 0x2),  # same cell, burst-only linear change
    ):
        rows.append(
            {
                "row_i": row,
                "bank_i": 0,
                "col_i": col,
                "linear_i": linear,
            }
        )
    audit = audit_linear_addr(pd.DataFrame(rows))
    assert audit.n_cell_linear_spreads == 1
    assert audit.n_linear_collisions == 0
    assert "distant Linear ADDR" in audit.examples[0].detail


def test_normalize_still_returns_stats_on_clean_headed_frame():
    raw = pd.DataFrame(
        [
            [
                "Site",
                "Slot",
                "Loop",
                "Pattern Name",
                "Linear ADDR",
                "ROW",
                "BANK",
                "COL",
            ],
            [5, 6, 1, "p", "0xAA", "0x1", 7, "0x2"],
        ]
    )
    out, stats = normalize_fail_frame(raw)
    assert len(out) == 1
    assert stats.dropped_rows == 0
    assert stats.kept_rows == 1
