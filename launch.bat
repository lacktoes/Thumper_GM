@echo off
title Thumpers GM Dashboard
cd /d "%~dp0"

echo Starting Thumpers GM Dashboard...
echo.
echo App will open at http://localhost:8501
echo Close this window to stop the server.
echo.

streamlit run app.py --server.headless false --browser.gatherUsageStats false

pause
