@echo off
echo === RAPID Build ===
echo.

echo [1/6] Cleaning previous builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist RAPID.spec del /q RAPID.spec
if exist RAPID.exe del /q RAPID.exe

echo [2/6] Installing/Updating build and UI dependencies...
pip install --upgrade pyinstaller PySide6 PySide6-Fluent-Widgets requests urllib3 curl_cffi >nul 2>&1

echo [3/6] Checking RAPID.pyw syntax...
python -m py_compile RAPID.pyw
if errorlevel 1 (
    echo === ERROR: RAPID.pyw has syntax error ===
    pause
    exit /b 1
)

echo [4/6] Generating RAPID.exe (Qt/Fluent)...
pyinstaller --onefile --windowed --name RAPID --add-data "languages;languages" --add-data "themes;themes" --collect-all PySide6 --collect-all qfluentwidgets --collect-all PySideSix_Frameless_Window --collect-all darkdetect --clean RAPID.pyw

echo.
if not exist dist\RAPID.exe (
    echo === ERROR: Build failed ===
    pause
    exit /b 1
)

echo [5/6] Moving RAPID.exe...
move dist\RAPID.exe RAPID.exe

echo [6/6] Cleaning temporary files...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist RAPID.spec del /q RAPID.spec

echo.
echo === Build completed! ===
echo Executable: RAPID.exe
pause
