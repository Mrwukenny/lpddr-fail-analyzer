"""Write markdown report, CSV summary, heatmaps, and optional normalized fails."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from lpddr_fail_analyzer.audit import AddrAudit
from lpddr_fail_analyzer.classify import LABEL_ZH, DieAnalysis
from lpddr_fail_analyzer.hexutil import fmt_hex
from lpddr_fail_analyzer.ingest import (
    BatchMeta,
    BoardCensus,
    CANONICAL_COLUMNS,
    IngestStats,
    board_fails_without_dump,
)

# P1 wording (must appear in report.md).
CHANNEL_RANK_DISCLAIMER = (
    "输入中无 Channel/Rank：按 **single-channel view**（单通道视角）处理；"
    "若双通道被合并进同一份 fail_msg，存在 dual-channel mixing risk（通道混叠风险）。"
)
COL_GRANULARITY_DISCLAIMER = (
    "**COL is tester decode granularity, NOT JEDEC bare physical column**"
    "（COL 是测试机解码粒度，不是 JEDEC 裸物理列）。"
)

# Product-lock wording (must appear in report.md).
SECTION_ANALYZABLE = "有地址明细（可分析）"
SECTION_BOARD_ONLY = "仅 board 不良、无 dump"
NO_STRUCTURE_MARK = "本轮无法结构分类"
MULTI_LOOP_DIE_TIP = "同颗多轮/多 loop 勿累加颗数"
BIN_COUNT_TIP = "别拿分Bin数量直接当可分析颗数"
BIN_NE_ANALYZABLE_TIP = "分Bin ≠ 可分析颗数"
STATION_SWAP_VS_RETEST_TIP = "同工位无法分辨换料 vs 复测"
BOARD_MISSING_NOTE = (
    "本批输入无 board_msg 表，无法列出仅 board 不良、无 dump 的颗粒。"
)

ARRAY_LIKE = {"bad_column", "bad_row", "spatial_2d_cluster", "bank_local_cluster"}
IFACE_LIKE = {"scatter", "single_bit_stuck", "coupling_suspect"}


def lpddr_type_tip(pn: str | None) -> str:
    text = (pn or "").upper()
    if "LPDDR4X" in text or "DDR4X" in text:
        return "Summary/PN 文本提示可能为 LPDDR4X；4 与 4X 使用同一结构分类器，本条仅为标注。"
    if "LPDDR4" in text:
        return "Summary/PN 文本提示可能为 LPDDR4；4 与 4X 使用同一结构分类器，本条仅为标注。"
    if "4X" in text:
        return "PN 中含 4X 字样，可能为 LPDDR4X；未做独立分类，仅作提示。"
    return "未在 Summary/PN 中明确区分 LPDDR4 与 LPDDR4X；本工具使用同一结构分类器。"


def _label_zh(name: str) -> str:
    return LABEL_ZH.get(name, name)


def _top_hex(pairs: list[tuple[int, int]]) -> str:
    if not pairs:
        return "—"
    parts = []
    for val, cnt in pairs[:5]:
        parts.append(f"{fmt_hex(val)} ×{cnt}")
    return ", ".join(parts)


def write_summary_csv(
    dies: list[DieAnalysis],
    path: Path,
    stats: IngestStats | None = None,
    run_status: str = "ok",
    addr_mismatch: int = 0,
    board: BoardCensus | None = None,
) -> None:
    dropped = stats.dropped_rows if stats is not None else 0
    dropped_bad = stats.dropped_bad_address if stats is not None else 0
    n_analyzable = len(dies)
    analyzable_keys = {(d.site, d.slot) for d in dies}
    board_only: list = []
    board_present = "no"
    n_board_no_dump = ""
    if board is not None and board.present and board.missing_reason is None:
        board_present = "yes"
        board_only = board_fails_without_dump(board, analyzable_keys)
        n_board_no_dump = len(board_only)
    elif board is not None:
        board_present = "no"

    rows = []
    for d in dies:
        rows.append(
            {
                "section": SECTION_ANALYZABLE,
                "site": d.site,
                "slot": d.slot,
                "die_id": d.die_id,
                "n_fail_rows": d.n_fail_rows,
                "n_unique_cells": d.n_unique_cells,
                "n_patterns": d.n_patterns,
                "n_loops": d.n_loops,
                "primary_label": d.primary_label,
                "primary_label_zh": _label_zh(d.primary_label),
                "secondary_labels": "|".join(d.secondary_labels),
                "top_row": fmt_hex(d.top_rows[0][0]) if d.top_rows else "",
                "top_col": fmt_hex(d.top_cols[0][0]) if d.top_cols else "",
                "top_bank": d.top_banks[0][0] if d.top_banks else "",
                "xor_hint0": d.xor_hints[0] if d.xor_hints else "",
                "note": "",
                "n_board_fail_rows": "",
                "run_status": run_status,
                "dropped_rows": dropped,
                "dropped_bad_address": dropped_bad,
                "addr_mismatch": addr_mismatch,
                "n_analyzable_dies": n_analyzable,
                "n_board_no_dump": n_board_no_dump,
                "board_msg_present": board_present,
            }
        )
    for b in board_only:
        rows.append(
            {
                "section": SECTION_BOARD_ONLY,
                "site": b.site,
                "slot": b.slot,
                "die_id": b.die_id,
                "n_fail_rows": "",
                "n_unique_cells": "",
                "n_patterns": "",
                "n_loops": "",
                "primary_label": "",
                "primary_label_zh": NO_STRUCTURE_MARK,
                "secondary_labels": "",
                "top_row": "",
                "top_col": "",
                "top_bank": "",
                "xor_hint0": "",
                "note": NO_STRUCTURE_MARK,
                "n_board_fail_rows": b.n_fail_rows,
                "run_status": run_status,
                "dropped_rows": dropped,
                "dropped_bad_address": dropped_bad,
                "addr_mismatch": addr_mismatch,
                "n_analyzable_dies": n_analyzable,
                "n_board_no_dump": n_board_no_dump,
                "board_msg_present": board_present,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def write_normalized_csv(fails: pd.DataFrame, path: Path) -> None:
    cols = [c for c in CANONICAL_COLUMNS if c in fails.columns]
    extra = [c for c in ("row_i", "bank_i", "col_i", "linear_i", "loop_i") if c in fails.columns]
    fails[cols + extra].to_csv(path, index=False, encoding="utf-8-sig")


def _fmt_addr_tick(value: float, _pos: object) -> str:
    iv = int(round(value))
    if iv < 0:
        return ""
    return f"0x{iv:X}"


def _hist2d_or_scatter(ax, rows: list[int], cols: list[int], title: str) -> None:
    ax.set_title(title)
    ax.set_xlabel("COL")
    ax.set_ylabel("ROW")
    if not rows:
        ax.text(0.5, 0.5, "no points", ha="center", va="center", transform=ax.transAxes)
        return
    n = len(rows)
    uniq_r = len(set(rows))
    uniq_c = len(set(cols))
    if n >= 8 and uniq_r >= 4 and uniq_c >= 4:
        bins_c = min(48, max(8, uniq_c))
        bins_r = min(48, max(8, uniq_r))
        hb = ax.hist2d(cols, rows, bins=(bins_c, bins_r), cmap="magma")
        plt.colorbar(hb[3], ax=ax, fraction=0.046, pad=0.04, label="count")
    else:
        ax.scatter(cols, rows, s=18, c="#c0392b", alpha=0.75, edgecolors="none")
        ax.set_facecolor("#f7f7f7")
    rmin, rmax = min(rows), max(rows)
    cmin, cmax = min(cols), max(cols)
    rpad = max(64, int((rmax - rmin) * 0.06)) if rmax > rmin else 256
    cpad = max(32, int((cmax - cmin) * 0.06)) if cmax > cmin else 64
    ax.set_ylim(rmin - rpad, rmax + rpad)
    ax.set_xlim(cmin - cpad, cmax + cpad)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_fmt_addr_tick))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_fmt_addr_tick))
    ax.grid(True, alpha=0.25)


def write_heatmaps(dies: list[DieAnalysis], out_dir: Path) -> list[Path]:
    """Write per-bank heatmaps for main banks plus one combined figure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    all_cells: list[tuple[int, int, int]] = []
    bank_cells: dict[int, list[tuple[int, int]]] = {}
    for d in dies:
        for r, b, c in d.unique_cells:
            all_cells.append((r, b, c))
            bank_cells.setdefault(b, []).append((r, c))

    if not all_cells:
        return written

    total = len(all_cells)
    ranked = sorted(bank_cells.items(), key=lambda kv: -len(kv[1]))
    main_banks = [b for b, pts in ranked if len(pts) >= max(3, int(0.05 * total))] or [ranked[0][0]]

    for bank in main_banks:
        pts = bank_cells[bank]
        fig, ax = plt.subplots(figsize=(7.2, 5.4))
        _hist2d_or_scatter(
            ax,
            [r for r, _ in pts],
            [c for _, c in pts],
            f"Bank {bank} unique fail cells (n={len(pts)})",
        )
        path = out_dir / f"heatmap_bank{bank}.png"
        fig.tight_layout()
        fig.savefig(path, dpi=120)
        plt.close(fig)
        written.append(path)

    # Combined: color by bank.
    fig, ax = plt.subplots(figsize=(7.8, 5.6))
    banks = sorted({b for _, b, _ in all_cells})
    cmap = plt.get_cmap("tab10")
    for i, bank in enumerate(banks):
        pts = [(r, c) for r, b, c in all_cells if b == bank]
        ax.scatter(
            [c for _, c in pts],
            [r for r, _ in pts],
            s=16,
            alpha=0.8,
            color=cmap(i % 10),
            label=f"BANK {bank} (n={len(pts)})",
            edgecolors="none",
        )
    ax.set_xlabel("COL")
    ax.set_ylabel("ROW")
    ax.set_title("Combined unique fail cells (row × col, colored by bank)")
    ax.legend(loc="best", fontsize=8, framealpha=0.9)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(_fmt_addr_tick))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(_fmt_addr_tick))
    ax.grid(True, alpha=0.25)
    combined = out_dir / "heatmap_combined.png"
    fig.tight_layout()
    fig.savefig(combined, dpi=120)
    plt.close(fig)
    written.append(combined)

    # Pattern pie (batch-level, using fail rows via dies.pattern_counts).
    pattern_total: Counter[str] = Counter()
    for d in dies:
        pattern_total.update(d.pattern_counts)
    if pattern_total:
        fig, ax = plt.subplots(figsize=(6.4, 4.8))
        labels, sizes = zip(*pattern_total.most_common())
        ax.pie(sizes, labels=labels, autopct=lambda p: f"{p:.0f}%" if p >= 4 else "")
        ax.set_title("Pattern Name share (fail rows)")
        pie_path = out_dir / "pattern_pie.png"
        fig.tight_layout()
        fig.savefig(pie_path, dpi=120)
        plt.close(fig)
        written.append(pie_path)

    return written


def auto_conclusion(meta: BatchMeta, dies: list[DieAnalysis]) -> list[str]:
    """Three sentences: 主模式 / 阵列 vs 接口·映射·条件 / 建议下一刀."""
    n_dies = len(dies)
    n_rows = sum(d.n_fail_rows for d in dies)
    prim = Counter(d.primary_label for d in dies)
    top_lab, top_n = prim.most_common(1)[0] if prim else ( "scatter", 0)
    zh = _label_zh(top_lab)

    sentence1 = (
        f"本批有地址明细、可分析颗粒 {n_dies} 颗"
        f"（fail_msg 唯一 Site+Slot，同颗多轮不累加；"
        f"{STATION_SWAP_VS_RETEST_TIP}）、"
        f"{n_rows} 条失败地址行，"
        f"主模式是「{zh}」({top_lab}，{top_n}/{n_dies} 颗主标签)。"
    )

    array_n = sum(1 for d in dies if d.primary_label in ARRAY_LIKE)
    iface_n = n_dies - array_n
    if array_n >= iface_n:
        leaning = (
            "从行/列/Bank/空间聚集看，更像阵列内部结构缺陷嫌疑"
            "（坏行线、坏列线或局部阵列簇），而不是纯随机误码。"
        )
    else:
        leaning = (
            "结构偏散布或单点，更像接口·映射·测试条件相关嫌疑"
            "（含固定 XOR 位），而不是大面积阵列线缺陷。"
        )
    xor_note = ""
    xor_all = [h for d in dies for h in d.xor_hints[:1]]
    if xor_all:
        xor_note = f" XOR 观察：{xor_all[0]}——只作位翻转提示，不是根因。"
    sentence2 = (
        leaning
        + xor_note
        + " 标签仅为结构嫌疑，不断言 SI / ECC / row-hammer 等根因。"
    )

    if top_lab == "bad_column":
        next_cut = (
            "建议下一刀：对主坏列（同 COL、跨 ROW）做单列地址复测，"
            "确认多 Pattern/Loop 是否稳定复现，并对比其他 Site+Slot 是否同列。"
        )
    elif top_lab == "bad_row":
        next_cut = (
            "建议下一刀：对主坏行（同 ROW、跨 COL）缩小行范围复测，"
            "核对是否随 Pattern 变化，并对照相邻 ROW 是否成对出现。"
        )
    elif top_lab == "single_bit_stuck":
        next_cut = (
            "建议下一刀：锁定该 (ROW,BANK,COL) 做单地址读写/反相复测，"
            "看多 Pattern 是否始终同一格；仍不要直接下物理失效结论。"
        )
    elif top_lab == "bank_local_cluster":
        next_cut = (
            "建议下一刀：把复测范围收窄到主 Bank，检查该 Bank 内是行/列还是二维簇，"
            "并与其他 Bank 对照。"
        )
    elif top_lab == "spatial_2d_cluster":
        next_cut = (
            "建议下一刀：按热力图包围盒做局部地址扫描，确认簇边界是否随电压/温度移动。"
        )
    elif top_lab == "coupling_suspect":
        next_cut = (
            "建议下一刀：对相邻 ROW/COL 成对地址做 aggressor/victim 对照（结构验证），"
            "不要直接写成 row-hammer 根因。"
        )
    else:
        next_cut = (
            "建议下一刀：先核对 XOR 固定位与 Pattern 是否绑定，"
            "再用更短 Loop 或改电压/温度窗口看失败是否条件相关。"
        )
    return [sentence1, sentence2, next_cut]


def run_status_label(stats: IngestStats | None, audit: AddrAudit | None) -> str:
    if stats is not None and stats.incomplete:
        return "incomplete"
    if audit is not None and audit.has_warnings:
        return "warnings"
    return "ok"


def _board_only_intro(board: BoardCensus, n_board_only: int) -> list[str]:
    lines = [
        f"## {SECTION_BOARD_ONLY}",
        "",
    ]
    if not board.present or board.missing_reason in {"not_excel", "no_sheet"}:
        lines.append(BOARD_MISSING_NOTE)
        lines.append("")
        return lines
    if board.missing_reason == "bad_header":
        lines.append(
            "board_msg 表存在，但无法识别 Site/Slot/Result 表头，"
            "无法列出仅 board 不良、无 dump 的颗粒。"
        )
        if board.header_error:
            lines.append(f"（{board.header_error}）")
        lines.append("")
        return lines
    if board.missing_reason == "empty":
        lines.append("board_msg 表为空，无法列出仅 board 不良、无 dump 的颗粒。")
        lines.append("")
        return lines
    lines.append(
        "本节目列为 `board_msg` 中 Result=fail 的**唯一 Site+Slot**，"
        "且 `fail_msg` 没有 ROW/BANK/COL 地址 dump。"
        f"**{NO_STRUCTURE_MARK}**。"
        f" 去重键仅为 Site+Slot（Excel 无 UID）；**{STATION_SWAP_VS_RETEST_TIP}**。"
        f"{BIN_NE_ANALYZABLE_TIP}。"
    )
    lines.append("")
    lines.append(
        f"- board 不良行（勿当作颗数）： **{board.n_fail_rows}**"
    )
    lines.append(
        f"- board 不良唯一 Site+Slot： **{board.n_unique_fail_dies}**"
    )
    lines.append(f"- 其中无 dump、本轮无法结构分类： **{n_board_only}**")
    lines.append("")
    if n_board_only == 0:
        lines.append(
            "board_msg 中的不良 Site+Slot 均已出现在 fail_msg 地址明细中；本节目为空。"
        )
        lines.append("")
        return lines
    lines.append("| Site | Slot | board失败次数 | Bin | 说明 |")
    lines.append("| --- | --- | ---: | --- | --- |")
    return lines


def write_report_md(
    meta: BatchMeta,
    fails: pd.DataFrame,
    dies: list[DieAnalysis],
    heatmap_paths: list[Path],
    out_path: Path,
    stats: IngestStats | None = None,
    audit: AddrAudit | None = None,
    board: BoardCensus | None = None,
) -> None:
    n_dies = len(dies)
    n_rows = len(fails)
    n_cells = int(fails[["row_i", "bank_i", "col_i"]].drop_duplicates().shape[0])
    pattern_total: Counter[str] = Counter()
    for d in dies:
        pattern_total.update(d.pattern_counts)
    conclusions = auto_conclusion(meta, dies)
    status = run_status_label(stats, audit)

    analyzable_keys = {(d.site, d.slot) for d in dies}
    census = board if board is not None else BoardCensus(present=False, missing_reason="not_excel")
    board_only = (
        board_fails_without_dump(census, analyzable_keys)
        if census.present and census.missing_reason is None
        else []
    )
    n_board_only = len(board_only)

    header_rows = [
        ("输入文件", str(meta.source_path)),
        ("PN", meta.pn or "—"),
        ("LOT ID", meta.lot_id or "—"),
        ("测试温度", meta.temperature or "—"),
        ("VDD1", meta.vdd1 or "—"),
        ("VDD2", meta.vdd2 or "—"),
        ("VDDQ", meta.vddq or "—"),
        ("测试颗粒总数量", meta.total_dies or "—"),
        ("良品总数", meta.good_dies or "—"),
        ("良率", meta.yield_rate or "—"),
        ("run_status", status),
    ]

    lines: list[str] = []
    lines.append("# LPDDR SLT fail_msg 结构分析（MVP）")
    lines.append("")
    lines.append(
        "> 本报告只给出 **结构嫌疑**（单点 / 坏列 / 坏行 / Bank 聚集 / 二维簇 / 相邻耦合 / 散布）。"
        "**不断言** SI、ECC、row-hammer 或其他根因。"
    )
    lines.append("")
    if status == "incomplete":
        dropped = stats.dropped_rows if stats is not None else 0
        bad = stats.dropped_bad_address if stats is not None else 0
        lines.append(
            f"> ⚠️ **INCOMPLETE / WARNINGS** — 本报告不是一次完全干净的成功运行。"
            f" dropped_rows={dropped}（unparseable ROW/BANK/COL={bad}）。"
            " 分类仅基于 kept 行。"
        )
        lines.append("")
    elif status == "warnings":
        n_mis = audit.n_mismatch if audit is not None else 0
        lines.append(
            f"> ⚠️ **WARNINGS** — Linear ADDR 与 ROW/BANK/COL 存在一致性不匹配"
            f"（n_mismatch={n_mis}）。本报告带警告，不是完全干净的成功运行。"
        )
        lines.append("")
    lines.append("## 批次信息")
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("| --- | --- |")
    for k, v in header_rows:
        lines.append(f"| {k} | {v} |")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- fail 地址行（kept）： **{n_rows}**")
    if stats is not None:
        lines.append(f"- candidate 地址行： **{stats.candidate_rows}**")
        lines.append(
            f"- dropped_rows： **{stats.dropped_rows}** "
            f"（unparseable ROW/BANK/COL={stats.dropped_bad_address}, "
            f"missing Site/Slot={stats.dropped_no_identity}）"
        )
    lines.append(
        f"- 可分析颗数（fail_msg 唯一 Site+Slot）： **{n_dies}**"
        f"（去重键仅为 Site+Slot，Excel 无 UID；**{STATION_SWAP_VS_RETEST_TIP}**）"
    )
    if census.present and census.missing_reason is None:
        lines.append(
            f"- 仅 board 不良、无 dump： **{n_board_only}**（{NO_STRUCTURE_MARK}）"
        )
    else:
        lines.append(f"- 仅 board 不良、无 dump： 无 board_msg 表")
    lines.append(f"- 唯一 (ROW,BANK,COL) 格点： **{n_cells}**")
    lines.append(f"- Pattern 种类： **{fails['pattern_name'].nunique(dropna=True)}**")
    if audit is not None:
        lines.append(
            f"- Linear ADDR 审计 n_mismatch： **{audit.n_mismatch}** "
            f"（有 Linear ADDR 的 kept 行 {audit.n_with_linear}/{audit.n_rows}）"
        )
    lines.append("")
    lines.append("## 警告")
    lines.append("")
    warn_bits: list[str] = []
    if stats is not None:
        warn_bits.extend(stats.warning_lines())
    if audit is not None:
        warn_bits.extend(audit.warning_lines())
    if not warn_bits:
        lines.append("无。dropped_rows=0，Linear ADDR 审计无 mismatch。")
    else:
        for w in warn_bits:
            lines.append(f"- {w}")
    lines.append("")
    lines.append(f"## {SECTION_ANALYZABLE}")
    lines.append("")
    lines.append(
        "本节目列为 `fail_msg` 中带 ROW/BANK/COL 的**唯一 Site+Slot**"
        "（不去重 UID、不因地址长得不像而拆成两颗）。"
        f"**{STATION_SWAP_VS_RETEST_TIP}**。"
        f"{MULTI_LOOP_DIE_TIP}；结构分类按唯一 (ROW,BANK,COL) 格点，多 loop 不放大坏行/坏列权重。"
        f"{BIN_COUNT_TIP}（{BIN_NE_ANALYZABLE_TIP}）。"
    )
    lines.append("")
    lines.append("### 颗粒主标签")
    lines.append("")
    lines.append("| Site | Slot | fail行 | 唯一格点 | 主标签 | 次标签 | 顶 ROW | 顶 COL | 顶 BANK |")
    lines.append("| --- | --- | ---: | ---: | --- | --- | --- | --- | --- |")
    for d in dies:
        sec = ", ".join(_label_zh(s) for s in d.secondary_labels) or "—"
        tr = fmt_hex(d.top_rows[0][0]) if d.top_rows else "—"
        tc = fmt_hex(d.top_cols[0][0]) if d.top_cols else "—"
        tb = str(d.top_banks[0][0]) if d.top_banks else "—"
        lines.append(
            f"| {d.site} | {d.slot} | {d.n_fail_rows} | {d.n_unique_cells} | "
            f"{_label_zh(d.primary_label)} | {sec} | {tr} | {tc} | {tb} |"
        )
    lines.append("")
    lines.append("### 各颗结构细节")
    lines.append("")
    for d in dies:
        lines.append(f"#### {d.die_id}（Site {d.site}, Slot {d.slot}）")
        lines.append("")
        lines.append(f"- 主标签：**{_label_zh(d.primary_label)}** (`{d.primary_label}`)")
        if d.secondary_labels:
            sec = ", ".join(f"{_label_zh(s)} (`{s}`)" for s in d.secondary_labels)
            lines.append(f"- 次标签：{sec}")
        ev = d.evidence
        lines.append(
            f"- 唯一格点 {d.n_unique_cells}；"
            f" 顶列 {fmt_hex(ev.get('top_col'))} 占唯一格点 {ev.get('top_col_share', 0):.0%}；"
            f" 顶行 {fmt_hex(ev.get('top_row'))} 占 {ev.get('top_row_share', 0):.0%}；"
            f" 顶 Bank {ev.get('top_bank')} 占 {ev.get('top_bank_share', 0):.0%}。"
        )
        lines.append(f"- Pattern 分解：{d.pattern_counts}")
        lines.append(f"- Loop 数：{d.n_loops}（同颗多轮只计 1 颗）")
        lines.append(f"- 出现最多的 ROW：{_top_hex(d.top_rows)}")
        lines.append(f"- 出现最多的 COL：{_top_hex(d.top_cols)}")
        banks = ", ".join(f"BANK {b} ×{n}" for b, n in d.top_banks)
        lines.append(f"- BANK 分布：{banks or '—'}")
        if d.xor_hints:
            lines.append("- XOR 位提示（结构观察）：")
            for h in d.xor_hints[:4]:
                lines.append(f"  - {h}")
        lines.append("")

    lines.extend(_board_only_intro(census, n_board_only))
    for b in board_only:
        bins = ", ".join(b.bins) if b.bins else "—"
        lines.append(
            f"| {b.site} | {b.slot} | {b.n_fail_rows} | {bins} | {NO_STRUCTURE_MARK} |"
        )
    if board_only:
        lines.append("")

    lines.append("## Pattern 分布（fail 行）")
    lines.append("")
    if (out_path.parent / "pattern_pie.png").exists():
        lines.append("![pattern pie](pattern_pie.png)")
        lines.append("")
    lines.append("| Pattern Name | fail行 | 占比 |")
    lines.append("| --- | ---: | ---: |")
    total_p = sum(pattern_total.values()) or 1
    for name, cnt in pattern_total.most_common():
        lines.append(f"| {name} | {cnt} | {cnt / total_p:.1%} |")
    lines.append("")

    img_names = [p.name for p in heatmap_paths if p.suffix.lower() == ".png" and "heatmap" in p.name]
    if img_names:
        lines.append("## 热力图")
        lines.append("")
        lines.append("坐标为解析后的 ROW × COL（唯一格点），用于观察列线/行线/簇，不是物理版图。")
        lines.append("")
        for name in img_names:
            lines.append(f"![{name}]({name})")
            lines.append("")

    lines.append("## 自动结论")
    lines.append("")
    for i, sent in enumerate(conclusions, 1):
        lines.append(f"{i}. {sent}")
    lines.append("")
    lines.append("## 提示")
    lines.append("")
    lines.append(f"- **{STATION_SWAP_VS_RETEST_TIP}**（去重键仅为 Site+Slot，Excel 无 UID，不拆成两颗）")
    lines.append(f"- **{MULTI_LOOP_DIE_TIP}**")
    lines.append(f"- **{BIN_COUNT_TIP}**（{BIN_NE_ANALYZABLE_TIP}）")
    lines.append(f"- {lpddr_type_tip(meta.pn)}")
    lines.append(f"- {CHANNEL_RANK_DISCLAIMER}")
    lines.append(f"- {COL_GRANULARITY_DISCLAIMER}")
    lines.append(
        "- Site/Slot/Loop/Pattern Name 在 Excel 中常只写在该颗/该段首行，解析时已按列 fill-forward。"
    )
    lines.append("- 分类按唯一 (ROW,BANK,COL) 格点，避免同一地址被多 Pattern/Loop 放大。")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
