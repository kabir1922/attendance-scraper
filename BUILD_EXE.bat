@echo off
title Attendance Scraper - Build Tool
color 0A

echo ============================================================
echo   ATTENDANCE SCRAPER - BUILD .EXE
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Please install Python first.
    pause
    exit /b
)

echo [1/4] Installing required packages...
pip install selenium webdriver-manager beautifulsoup4 pandas openpyxl pyinstaller
echo.

echo [2/4] Cleaning old builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist AttendanceScraper.spec del AttendanceScraper.spec
echo      Done.
echo.

echo [3/4] Building .exe with PyInstaller...
pyinstaller --onefile --windowed --name "AttendanceScraper" --collect-all selenium --collect-all webdriver_manager --collect-all bs4 --collect-all pandas --collect-all openpyxl attendance_scraper.py

echo.

echo [4/4] Checking output...
if exist "dist\AttendanceScraper.exe" (
    echo ============================================================
    echo   SUCCESS! Your .exe is ready:
    echo   dist\AttendanceScraper.exe
    echo ============================================================
    echo.
    echo   You can now:
    echo   1. Copy AttendanceScraper.exe to any Windows PC
    echo   2. Double-click to run - no Python needed!
    echo   3. Only requirement: Google Chrome must be installed
    echo.
    explorer dist
) else (
    echo [ERROR] Build failed. Check the output above for errors.
)

pause
