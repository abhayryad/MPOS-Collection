@echo off
rem POS Collection - serve on the LAN at http://<this-pc-ip>:8028
cd /d "%~dp0"
if not exist "frontend\dist\index.html" (
  echo Building the React frontend (first run)...
  pushd frontend
  call npm install --no-fund --no-audit || goto :fail
  call npm run build || goto :fail
  popd
)
echo Starting POS Collection on port 8028 (Ctrl+C to stop)...
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo   Open: http:/%%a:8028
python -m uvicorn app:app --host 0.0.0.0 --port 8028
pause
exit /b

:fail
popd
echo Frontend build failed - see the messages above.
pause
