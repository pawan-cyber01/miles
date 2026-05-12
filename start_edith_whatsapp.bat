@echo off
title E.D.I.T.H. + WhatsApp Bridge Launcher
color 0B

echo.
echo  ===================================================
echo    E.D.I.T.H. SYSTEM LAUNCHER v8.0
echo    Even Dead, I'm The Hero - Stark Industries
echo  ===================================================
echo.

cd /d "%~dp0"

echo  [1/3] Starting Ollama AI backend...
start "Ollama" cmd /c "ollama serve"
timeout /t 3 /nobreak >nul

echo  [2/3] Starting E.D.I.T.H. Flask + HUD...
start "EDITH-HUD" cmd /c "python edith.py"
timeout /t 5 /nobreak >nul

echo  [3/3] Starting WhatsApp Bridge...
echo.
echo  ===================================================
echo   Scan the QR code below with WhatsApp mobile:
echo   WhatsApp ^> Linked Devices ^> Link a Device
echo  ===================================================
echo.
node whatsapp_bridge.js

pause
