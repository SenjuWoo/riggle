@echo off
rem Start Riggle: bundled Python first, then any Python 3.10+ on PATH.
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if exist "%~dp0python\python.exe" (
  "%~dp0python\python.exe" run.py %*
  goto :done
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run.py %*
) else (
  python run.py %*
)
:done
if errorlevel 1 pause
