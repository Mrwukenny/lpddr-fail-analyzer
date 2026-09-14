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
    assert dest.out_dir == out
    assert dest.run_status == "ok"
    assert dest.stats.dropped_rows == 0
    assert dest.addr_audit.n_mismatch == 0
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
    assert "INCOMPLETE" not in text
    assert "single-channel view" in text
    assert "dual-channel mixing" in text
    assert "tester decode granularity" in text
    assert "NOT JEDEC bare physical column" in text
    assert "dropped_rows" in text
    assert "有地址明细（可分析）" in text
    assert "仅 board 不良、无 dump" in text
    assert "同颗多轮/多 loop 勿累加颗数" in text
    assert "别拿分Bin数量直接当可分析颗数" in text
    assert "分Bin ≠ 可分析颗数" in text
    assert "同工位无法分辨换料 vs 复测" in text
    assert "- 可分析颗数（fail_msg 唯一 Site+Slot）： **1**" in text
    summary = (out / "summary.csv").read_text(encoding="utf-8")
    assert "primary_label" in summary
    assert "15" in summary
    assert "7" in summary
    assert "bad_column" in summary
    assert "dropped_rows" in summary
    assert "run_status" in summary


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
