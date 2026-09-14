"""CLI: `python -m lpddr_fail_analyzer analyze PATH --out DIR`."""

from __future__ import annotations

from pathlib import Path

import typer

from lpddr_fail_analyzer import __version__
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
    dest = run_analyze(path, out, write_normalized=write_normalized)
    typer.echo(f"lpddr-fail-analyzer {__version__}")
    typer.echo(f"wrote {dest / 'report.md'}")
    typer.echo(f"wrote {dest / 'summary.csv'}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
