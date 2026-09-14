@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
REM Windows helper: drag-and-drop an xlsx/csv onto this bat, or run it and
REM accept the default samples\sample_batch.xlsx, or type a path.
REM chcp 65001 first so later UTF-8 Chinese echo is not read as GBK.
REM Quote every Chinese echo; use ASCII () not fullwidth parentheses.
cd /d "%~dp0\.."

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "UV_LINK_MODE=copy"

where uv >nul 2>nul
if errorlevel 1 (
  echo "[ERROR] 未找到 uv. 请先安装 uv: https://docs.astral.sh/uv/getting-started/installation/"
  echo   powershell: irm https://astral.sh/uv/install.ps1 ^| iex
  pause
  exit /b 1
)

if not exist ".venv" (
  echo "正在 uv sync 安装依赖..."
  uv sync
  if errorlevel 1 (
    echo "[ERROR] uv sync 失败"
    pause
    exit /b 1
  )
)

set "INPUT=%~1"
if "!INPUT!"=="" (
  set "DEFAULT="
  if exist "samples\sample_batch.xlsx" set "DEFAULT=samples\sample_batch.xlsx"
  if defined DEFAULT (
    set /p "INPUT=输入 Excel/CSV 路径 (直接回车使用 !DEFAULT!): "
    if "!INPUT!"=="" set "INPUT=!DEFAULT!"
  ) else (
    set /p "INPUT=输入 Excel/CSV 路径: "
  )
)

if "!INPUT!"=="" (
  echo "[ERROR] 未提供输入文件"
  pause
  exit /b 1
)

if not exist "!INPUT!" (
  echo "[ERROR] 找不到文件: !INPUT!"
  pause
  exit /b 1
)

if not exist "out" mkdir out
echo "分析 !INPUT! ..."
uv run python -m lpddr_fail_analyzer analyze "!INPUT!" --out out
set "ERR=!ERRORLEVEL!"
echo.
echo "输出目录: %CD%\out"
if not "!ERR!"=="0" (
  echo "[ERROR] 分析失败, exit=!ERR!"
) else (
  echo "完成. 请用编辑器打开 out\report.md (UTF-8). 控制台可能仍乱码, 报告文件是好的."
)
pause
exit /b !ERR!
