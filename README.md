# LPDDR fail analyzer

离线 MVP：从客户 SLT 批次 Excel / `fail_msg` 自动分析 **LPDDR4/4X 颗粒（die）失败地址的结构嫌疑**。

本工具做的是规则分类（坏列、坏行、单点、Bank 聚集、二维簇、相邻耦合、散布），**不断言** SI / ECC / row-hammer 等根因。LPDDR4 与 4X 使用**同一套分类器**；若 Summary/PN 里能看出类型，只会在报告里加一行提示。

## Windows 快速开始

1. 安装 [Python 3.11+](https://www.python.org/downloads/)（勾选 Add to PATH）。
2. 安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)：

```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

3. 打开本仓库目录，同步依赖：

```powershell
uv sync
```

4. 分析自带样本（或换成客户 `.xlsx`）：

```powershell
uv run python -m lpddr_fail_analyzer analyze samples\sample_batch.xlsx --out out\demo
```

也接受已按列填好的 CSV：

```powershell
uv run python -m lpddr_fail_analyzer analyze samples\sample_fail_msg.csv --out out\demo-csv
```

5. 或者双击 `scripts\start-windows.bat`：
   - 把 Excel/CSV **拖到 bat 上**；或
   - 直接运行，回车使用默认 `samples\sample_batch.xlsx`，也可粘贴路径。

分析完成后请用**编辑器**打开 `out\report.md`（UTF-8）。cmd 窗口里中文仍可能乱码，报告文件本身是好的。bat 会先 `chcp 65001`；若 uv 提示 `Failed to hardlink files; falling back to full copy`，可忽略（bat 已设 `UV_LINK_MODE=copy`）。

输出目录（`--out` 就是最终目录，例如 `out\demo`）包含：

| 文件 | 内容 |
| --- | --- |
| `report.md` | 一页式报告：PN/LOT/温度/VDD*、可分析颗数、有地址明细 / 仅 board 不良无 dump、结构、三句自动结论 |
| `summary.csv` | 每颗 Site+Slot 的主/次标签与计数 |
| `heatmap_bankN.png` | 主 Bank 的 ROW×COL 失败热力图 |
| `heatmap_combined.png` | 各 Bank 叠在一起的散点 |
| `pattern_pie.png` | Pattern Name 占比 |
| `normalized_fails.csv` | fill-forward 后的规范失败行（可用 `--no-write-normalized` 关闭） |

跑测试：

```powershell
uv sync
uv run pytest
```

## fill-forward 说明（Excel `fail_msg` 很关键）

真实表 **不是** 第 1 行当表头：

- 第 1–2 行是标题（如 Board ID / Fail Information）
- **真正表头** 是含有这些列名的那一行：`Site, Slot, Loop, Pattern Name, Linear ADDR, ROW, BANK, COL, EXP Value, RD Value, Re-read value1, Re-read value2, Re-read value3, XOR Val1`
- 解析器会在前几十行里 **按列名搜索表头**，不写死行号
- 同一颗的后续失败地址行里，Site / Slot / Loop / Pattern Name **经常是空的**，必须从上一行非空值 **向下填充（fill-forward）**
- 多颗 = fill-forward 之后的多个 **唯一 Site+Slot**（同颗多轮 / 多 Loop **不计多次**）。Excel **无 UID**，**同工位无法分辨换料 vs 复测**，因此不去重拆成两颗。

CSV 样本 `samples/sample_fail_msg.csv` 已是填好的版本，工具仍会做一次 fill-forward，空单元格也能补上。

Summary 表若存在，会抽取 `PN`、`LOT ID`、`测试温度`、`VDD1/VDD2/VDDQ`、产量统计写入报告页眉。

若存在 `board_msg`，会单独列出 **仅 board 不良、无 dump** 的 Site+Slot（本轮无法结构分类）。**可分析颗数**只来自 `fail_msg` 里带 ROW/BANK/COL 的唯一 Site+Slot（分Bin ≠ 可分析颗数）。同一 Site+Slot 即使多轮地址完全不像，仍计 1 颗。

## 数据质量（避免假绿）

- 无法解析的 ROW/BANK/COL **不会被静默丢掉**：计入 `dropped_rows`，写入日志、`report.md` 警告区与 `summary.csv`。半坏文件会标成 **INCOMPLETE / WARNINGS**，CLI 退出码非 0（仍会写出报告）。若全部地址行都坏，则硬失败、不写看起来成功的报告。
- 输入无 Channel/Rank：按 **single-channel view** 处理；同一份 fail_msg 若混了双通道，存在 dual-channel mixing risk。
- **COL is tester decode granularity, NOT JEDEC bare physical column。**
- 若存在 Linear ADDR，会对其与 ROW/BANK/COL 做一致性审计（同一 Linear ADDR 不得对应多个格点；同一格点的 Linear ADDR 不得在突发窗口之外发散）。

## 样本示例

仓库内 `samples/sample_batch.xlsx` 含 `Summary`、`board_msg`、`fail_msg` 三张表。在仓库根目录执行：

```bash
uv sync
uv run python -m lpddr_fail_analyzer analyze samples/sample_batch.xlsx --out out/demo
```

预期：命令退出码 0，并写出 `out/demo/report.md`、`out/demo/summary.csv` 以及 `heatmap_*.png`。该样本在 fill-forward 后是 Site=15 / Slot=7 的**一颗**可分析颗粒（fail_msg 里有 3 个 Loop，仍计 1 颗），地址结构上列主导（坏列嫌疑）很强，同时可能带出行/Bank 次级嫌疑。`board_msg` 里还有其他不良 Site+Slot 没有地址 dump，报告会单独列出并标明无法结构分类——请以报告为准，且它们都不是根因结论。

## 分类优先级（强 → 弱）

对每颗（Site+Slot）用 **唯一 (ROW, BANK, COL) 格点**（避免同一地址被多 Pattern/Loop 放大）：

1. `single_bit_stuck` 单点/单比特卡住嫌疑  
2. `bad_column` 坏列/列主导  
3. `bad_row` 坏行/行主导  
4. `bank_local_cluster` Bank 局部聚集  
5. `spatial_2d_cluster` 二维空间聚集  
6. `coupling_suspect` 相邻耦合嫌疑  
7. `scatter` 散布/无明显结构  

自动结论三句：**主模式** / **更像阵列缺陷还是接口·映射·条件** / **建议下一刀**。措辞保持「嫌疑」与「建议验证」，不会写成已证实的失效机理。

## 依赖

仅本地：`pandas`、`openpyxl`、`matplotlib`、`typer`；开发用 `pytest`。无云服务。
