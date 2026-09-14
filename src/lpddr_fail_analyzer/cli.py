"""CLI: `python -m lpddr_fail_analyzer analyze PATH --out DIR`."""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from lpddr_fail_analyzer import __version__
from lpddr_fail_analyzer.ingest import NoValidAddressRowsError
from lpddr_fail_analyzer.pipeline import run_analyze

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="LPDDR4/4X SLT fail_msg 结构分析（离线 MVP，标签仅为嫌疑）。",
)


@app.callback()
def _root() -> None:
    """LPDDR fail analyzer."""


@app.command("analyze")
def analyze_cmd(
    path: Path = typer.Argument(..., exists=True, readable=True, help="Excel (.xlsx) 或 CSV"),
    out: Path = typer.Option(Path("out"), "--out", "-o", help="输出目录（将写入 report.md 等）"),
    write_normalized: bool = typer.Option(
        True,
        "--write-normalized/--no-write-normalized",
        help="是否写出 normalized_fails.csv",
    ),
) -> None:
    """Ingest fail_msg, classify each Site+Slot die, write report/heatmap/summary."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = run_analyze(path, out, write_normalized=write_normalized)
    except NoValidAddressRowsError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"lpddr-fail-analyzer {__version__}")
    typer.echo(f"run_status={result.run_status}")
    typer.echo(f"dropped_rows={result.stats.dropped_rows} kept_rows={result.stats.kept_rows}")
    typer.echo(f"addr_mismatch={result.addr_audit.n_mismatch}")
    typer.echo(
        f"analyzable_dies={result.n_analyzable_dies} "
        f"board_no_dump={result.n_board_no_dump}"
    )
    typer.echo(f"wrote {result.out_dir / 'report.md'}")
    typer.echo(f"wrote {result.out_dir / 'summary.csv'}")
    if result.incomplete:
        # Half-broken input must not look like a fully clean successful run.
        # Details already went to the log + report.md warning section.
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
