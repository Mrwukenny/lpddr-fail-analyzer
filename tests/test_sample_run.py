import subprocess
import sys
from pathlib import Path

from lpddr_fail_analyzer.pipeline import run_analyze

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_XLSX = ROOT / "samples" / "sample_batch.xlsx"
SAMPLE_CSV = ROOT / "samples" / "sample_fail_msg.csv"


def test_sample_xlsx_writes_artifacts(tmp_path: Path):
    out = tmp_path / "demo"
    dest = run_analyze(SAMPLE_XLSX, out)
    assert dest == out
    assert (out / "report.md").is_file()
    assert (out / "summary.csv").is_file()
    assert (out / "normalized_fails.csv").is_file()
    heatmaps = list(out.glob("heatmap*.png"))
    assert heatmaps, "expected at least one heatmap png"
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "LOT ID" in text
    assert "Q75246.10" in text
    assert "T-SCE11N4G320AH-QCA2" in text
    assert "VDD1" in text
    assert "结构嫌疑" in text
    summary = (out / "summary.csv").read_text(encoding="utf-8")
    assert "primary_label" in summary
    assert "15" in summary
    assert "7" in summary
    assert "bad_column" in summary


def test_cli_module_exit_zero(tmp_path: Path):
    out = tmp_path / "cli-demo"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "lpddr_fail_analyzer",
            "analyze",
            str(SAMPLE_XLSX),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (out / "report.md").is_file()
    assert (out / "summary.csv").is_file()
    assert list(out.glob("heatmap*.png"))


def test_sample_csv_exits_clean(tmp_path: Path):
    out = tmp_path / "csv-run"
    run_analyze(SAMPLE_CSV, out, write_normalized=False)
    assert (out / "report.md").is_file()
    assert (out / "summary.csv").is_file()
    assert not (out / "normalized_fails.csv").exists()
