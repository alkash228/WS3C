@echo off
setlocal
cd /d "%~dp0"

set "PY="
if exist "..\.venv\Scripts\python.exe" set "PY=..\.venv\Scripts\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=py"

echo Starting WEB_samv...
echo Python: %PY%
echo Checking port 8090...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8090" ^| findstr "LISTENING"') do (
  echo Stopping old process PID %%p on port 8090...
  taskkill /F /PID %%p >nul 2>nul
)

for /d /r %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d" 2>nul

"%PY%" -m pip install -q -r requirements-web.txt
if errorlevel 1 (
  echo Failed to install Python dependencies. See errors above.
  pause
  exit /b 1
)

"%PY%" -c "from samv.http.server import main"
if errorlevel 1 (
  echo Import check failed. Fix errors above before starting the server.
  pause
  exit /b 1
)

"%PY%" serve.py
if errorlevel 1 (
  echo Server exited with an error.
  pause
  exit /b 1
)

endlocal
