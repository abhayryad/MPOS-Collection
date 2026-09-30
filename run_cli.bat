@echo off
rem MPOS Collection command-line tools, e.g.
rem   run_cli export-slips --date 2026-09-28
rem   run_cli check-db
cd /d "%~dp0"
set PYTHONPATH=%~dp0backend
python -m mpos %*
