@echo off
echo === RAPID Build ===
echo.

echo [1/5] Limpando builds anteriores...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist RAPID.spec del /q RAPID.spec
if exist RAPID.exe del /q RAPID.exe

echo [2/5] Instalando/Atualizando PyInstaller...
pip install --upgrade pyinstaller >nul 2>&1

echo [3/5] Gerando RAPID.exe...
pyinstaller --onefile --windowed --name RAPID --add-data "languages;languages" --add-data "themes;themes" --clean RAPID.pyw

echo.
if not exist dist\RAPID.exe (
    echo === ERRO: Build falhou ===
    pause
    exit /b 1
)

echo [4/5] Movendo RAPID.exe...
move dist\RAPID.exe RAPID.exe

echo [5/5] Limpando arquivos temporarios...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist RAPID.spec del /q RAPID.spec

echo.
echo === Build concluido! ===
echo Executavel: RAPID.exe
pause
