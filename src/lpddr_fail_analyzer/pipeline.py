"""End-to-end analyze pipeline."""

from __future__ import annotations

from pathlib import Path

from lpddr_fail_analyzer.classify import analyze_all_dies
from lpddr_fail_analyzer.ingest import load_fails
from lpddr_fail_analyzer.report import (
    write_heatmaps,
    write_normalized_csv,
    write_report_md,
    write_summary_csv,
)


def run_analyze(
    input_path: Path,
    out_dir: Path,
    write_normalized: bool = True,
) -> Path:
    input_path = Path(input_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fails, meta = load_fails(input_path)
    dies = analyze_all_dies(fails)
    if not dies:
        raise ValueError("No dies (Site+Slot groups) found after ingest")

    write_summary_csv(dies, out_dir / "summary.csv")
    heatmaps = write_heatmaps(dies, out_dir)
    if write_normalized:
        write_normalized_csv(fails, out_dir / "normalized_fails.csv")
    write_report_md(meta, fails, dies, heatmaps, out_dir / "report.md")
    return out_dir
