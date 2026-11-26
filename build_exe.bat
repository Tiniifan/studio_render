@echo off
rem Builds studio_render.exe and studio_render_cli.exe: out\<version>\studio_render\ and out\<version>\studio_render.zip (see tools\build_exe.py)
setlocal
cd /d "%~dp0"
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo PyInstaller is missing: pip install pyinstaller
    exit /b 1
)
python tools\build_exe.py
if errorlevel 1 (
    echo The build failed.
    exit /b 1
)
endlocal
