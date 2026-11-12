@echo off
setlocal EnableExtensions EnableDelayedExpansion

pushd "%~dp0"
if errorlevel 1 (
    echo ERROR: Could not access the project folder.
    pause
    exit /b 1
)

echo ==========================================================
echo RAPID - Reliable Asynchronous Parallel Internet Downloader
echo ==========================================================
echo.

set "SCRIPT=rapid.pyw"
set "OUTPUT=RAPID.exe"

if not exist "%SCRIPT%" (
    echo ERROR: %SCRIPT% was not found.
    echo Place build.bat in the same folder as %SCRIPT%.
    goto :fail
)

echo [1/6] Checking Python...
if not exist ".rapid-build-venv\Scripts\python.exe" (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -m venv ".rapid-build-venv"
    ) else (
        where python >nul 2>&1
        if errorlevel 1 (
            echo ERROR: Python 3.10 or newer was not found.
            goto :fail
        )
        python -m venv ".rapid-build-venv"
    )
    if errorlevel 1 (
        echo ERROR: Could not create the build virtual environment.
        goto :fail
    )
)

set "PYTHON_EXE=%CD%\.rapid-build-venv\Scripts\python.exe"
"%PYTHON_EXE%" -c "import sys; print('Python', sys.version.split()[0]); raise SystemExit(sys.version_info < (3, 10))"
if errorlevel 1 (
    echo ERROR: RAPID requires Python 3.10 or newer to build.
    goto :fail
)

"%PYTHON_EXE%" -c "import tkinter; tkinter.Tcl()" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Tkinter is unavailable in this Python installation.
    echo Reinstall Python with Tcl/Tk support enabled.
    goto :fail
)

echo.
echo [2/6] Installing build dependencies...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto :dependency_error
"%PYTHON_EXE%" -m pip install --upgrade pyinstaller requests urllib3 truststore certifi
if errorlevel 1 goto :dependency_error

"%PYTHON_EXE%" -c "import PyInstaller, requests, urllib3, truststore, certifi"
if errorlevel 1 goto :dependency_error

echo.
echo [3/6] Checking %SCRIPT% syntax...
"%PYTHON_EXE%" -m py_compile "%SCRIPT%"
if errorlevel 1 (
    echo ERROR: %SCRIPT% has a Python syntax error.
    goto :fail
)

echo.
echo [4/6] Preparing build options...
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"
if exist "RAPID.spec" del /q "RAPID.spec"

set "PYI_ARGS=--noconfirm --clean --onefile --windowed --name RAPID --collect-all certifi --collect-all requests --collect-all urllib3 --collect-all truststore"
if exist "icons\icon.ico" (
    set "PYI_ARGS=!PYI_ARGS! --icon=icons\icon.ico"
) else (
    echo WARNING: icons\icon.ico was not found. The executable will use the default icon.
)
if exist "languages\" (
    set "PYI_ARGS=!PYI_ARGS! --add-data=languages;languages"
) else (
    echo WARNING: languages folder was not found. The application will use its language fallback.
)
if exist "themes\" (
    set "PYI_ARGS=!PYI_ARGS! --add-data=themes;themes"
) else (
    echo WARNING: themes folder was not found. The application will use its built-in theme fallback.
)

echo.
echo [5/6] Building %OUTPUT%...
echo Application: RAPID - Reliable Asynchronous Parallel Internet Downloader
echo Source: %SCRIPT%
"%PYTHON_EXE%" -m PyInstaller !PYI_ARGS! "%SCRIPT%"
if errorlevel 1 (
    echo ERROR: PyInstaller failed. The build and dist folders were kept for diagnosis.
    goto :fail
)

if not exist "dist\%OUTPUT%" (
    echo ERROR: PyInstaller did not produce dist\%OUTPUT%.
    goto :fail
)

echo.
echo [6/6] Installing the new executable...
copy /y "dist\%OUTPUT%" "%OUTPUT:.exe=.new.exe%" >nul
if errorlevel 1 (
    echo ERROR: Could not stage the new executable.
    goto :fail
)
move /y "%OUTPUT:.exe=.new.exe%" "%OUTPUT%" >nul
if errorlevel 1 (
    echo ERROR: Could not replace %OUTPUT%.
    echo Close RAPID if it is currently running, then rename the .new.exe file manually.
    goto :fail
)

if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"
if exist "RAPID.spec" del /q "RAPID.spec"

echo.
echo ==========================================================
echo BUILD COMPLETED SUCCESSFULLY
echo Output: %OUTPUT%
echo ==========================================================
popd
pause
exit /b 0

:dependency_error
echo ERROR: Could not install or verify the build dependencies.
echo Check your internet connection and Python pip configuration.

echo.
:fail
popd
pause
exit /b 1
