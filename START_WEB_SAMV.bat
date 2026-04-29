@echo off
setlocal
cd /d "%~dp0"

echo Starting WEB_samv...
if exist "..\.venv\Scripts\python.exe" (
  "..\.venv\Scripts\python.exe" "serve.py"
) else (
  py "serve.py"
)

endlocal
