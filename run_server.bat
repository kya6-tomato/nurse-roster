@echo off
chcp 65001 >nul
cd /d "%~dp0"

call :find_python
if not defined PYEXE (
    echo Pythonが見つかりません。先に setup.bat を一度実行してください。
    pause
    exit /b 1
)

echo サーバーを起動しています。しばらくするとブラウザが自動で開きます...
echo （終了したいときは、このウィンドウを閉じてください）
"%PYEXE%" app.py
pause
exit /b 0


:find_python
set "PYEXE="

where python >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%P in ('where python') do (
        if not defined PYEXE (
            echo %%P | findstr /i "WindowsApps" >nul
            if errorlevel 1 set "PYEXE=%%P"
        )
    )
)

if not defined PYEXE (
    for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" if not defined PYEXE set "PYEXE=%%D\python.exe"
    )
)
exit /b 0
