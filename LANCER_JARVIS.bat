@echo off
echo ==========================================
echo    J.A.R.V.I.S - INITIALISATION SYSTEME
echo ==========================================
echo.
echo Activation du support UTF-8...
chcp 65001 >nul 2>&1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo Verification des mises a jour depuis GitHub...
git pull origin master

rem Verification de l'environnement virtuel
if not exist ".venv\Scripts\python.exe" goto no_venv

echo Installation automatique des dependances...
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet

echo Lancement de JARVIS via environnement virtuel...
".venv\Scripts\python.exe" main.py
goto end

:no_venv
echo [ERREUR] Dossier .venv introuvable.
echo Installation automatique des dependances...
python -m pip install -r requirements.txt --quiet

echo Tentative via Python global...
python main.py
goto end

:end
echo.
echo ==========================================
echo    J.A.R.V.I.S - COMPTE RENDU DE FIN
echo ==========================================
echo.
pause
