@echo off
echo ==========================================
echo    J.A.R.V.I.S - INITIALISATION SYSTEME
echo ==========================================
echo.
echo Activation du support UTF-8...
set PYTHONUTF8=1

rem Verification de l'environnement virtuel
if not exist ".venv\Scripts\python.exe" goto no_venv

echo Lancement de JARVIS via environnement virtuel...
".venv\Scripts\python.exe" main.py
goto end

:no_venv
echo Tentative via Python global...
py main.py
goto end

:end
echo.
echo ==========================================
echo    J.A.R.V.I.S - COMPTE RENDU DE FIN
echo ==========================================
echo.
pause
