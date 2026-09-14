"""End-to-end analyze pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lpddr_fail_analyzer.audit import AddrAudit, audit_linear_addr
from lpddr_fail_analyzer.classify import analyze_all_dies
from lpddr_fail_analyzer.ingest import IngestStats, load_fails
from lpddr_fail_analyzer.report import (
    run_status_label,
    write_heatmaps,
    write_normalized_csv,
    write_report_md,
    write_summary_csv,
)


@dataclass
class AnalyzeResult:
    out_dir: Path
    stats: IngestStats
    addr_audit: AddrAudit
    incomplete: bool
    warnings: list[str]
    run_status: str

    def __fspath__(self) -> str:
        return str(self.out_dir)


def run_analyze(
    input_path: Path,
    out_dir: Path,
    write_normalized: bool = True,
) -> AnalyzeResult:
    input_path = Path(input_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fails, meta, stats = load_fails(input_path)
    audit = audit_linear_addr(fails)
    dies = analyze_all_dies(fails)
    if not dies:
        raise ValueError("No dies (Site+Slot groups) found after ingest")

    status = run_status_label(stats, audit)
    write_summary_csv(
        dies,
        out_dir / "summary.csv",
        stats=stats,
        run_status=status,
        addr_mismatch=audit.n_mismatch,
    )
    heatmaps = write_heatmaps(dies, out_dir)
    if write_normalized:
        write_normalized_csv(fails, out_dir / "normalized_fails.csv")
    write_report_md(
        meta,
        fails,
        dies,
        heatmaps,
        out_dir / "report.md",
        stats=stats,
        audit=audit,
    )
    warnings = [*stats.warning_lines(), *audit.warning_lines()]
    return AnalyzeResult(
        out_dir=out_dir,
        stats=stats,
        addr_audit=audit,
        incomplete=stats.incomplete,
        warnings=warnings,
        run_status=status,
    )
