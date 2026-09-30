@echo off
chcp 65001 >nul
echo ============================================================
echo   夜勤待機シフト管理アプリ - 初回セットアップ
echo ============================================================
echo.

call :find_python
if not defined PYEXE call :install_python
if not defined PYEXE (
    echo ============================================================
    echo   Pythonの自動インストールに失敗しました。
    echo ============================================================
    echo お手数ですが、以下の手順で手動インストールしてください。
    echo   1. https://www.python.org/downloads/ を開く
    echo   2. 「Download Python」ボタンからインストーラーをダウンロード
    echo   3. インストーラーを開き、画面の一番下にある
    echo      「Add python.exe to PATH」に必ずチェックを入れてからインストール
    echo   4. インストール後、もう一度この setup.bat を実行してください
    pause
    exit /b 1
)

echo 使用するPython: %PYEXE%
echo.
echo 必要なプログラムをインストールします。少し時間がかかります。
echo.
"%PYEXE%" -m pip install --upgrade pip
"%PYEXE%" -m pip install -r "%~dp0requirements.txt"

echo.
echo ============================================================
echo   インストールが完了しました。
echo   次回からは run_server.bat をダブルクリックしてください。
echo ============================================================
pause
exit /b 0


rem ============================================================
rem   ここから下はサブルーチン（このファイルの中で呼び出して使う部品）
rem ============================================================

:find_python
rem PYEXEに「使えるpython.exeのフルパス」をセットする。見つからなければ空のまま。
set "PYEXE="

where python >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%P in ('where python') do (
        if not defined PYEXE (
            rem Windowsに最初から入っている「Microsoft Store起動用の偽python」を除外する
            echo %%P | findstr /i "WindowsApps" >nul
            if errorlevel 1 set "PYEXE=%%P"
        )
    )
)

if not defined PYEXE (
    rem このセットアップで過去にインストールした場所も探す
    for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" if not defined PYEXE set "PYEXE=%%D\python.exe"
    )
)
exit /b 0


:install_python
echo Pythonが見つかりませんでした。自動でダウンロード・インストールします。
echo （インターネット接続が必要です。数分かかることがあります。しばらくお待ちください）
echo.

set "PY_VERSION=3.12.7"
set "PY_INSTALLER=%TEMP%\python-%PY_VERSION%-installer.exe"
if exist "%PY_INSTALLER%" del "%PY_INSTALLER%" >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-amd64.exe' -OutFile '%PY_INSTALLER%' -TimeoutSec 180 } catch { exit 1 }"

if not exist "%PY_INSTALLER%" (
    echo ダウンロードに失敗しました（インターネット接続をご確認ください）。
    exit /b 0
)

echo ダウンロード完了。インストール中です（この間、画面が止まって見えても正常です）...
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=0 Include_test=0 Include_doc=0
del "%PY_INSTALLER%" >nul 2>nul

call :find_python
if defined PYEXE (
    echo Pythonのインストールが完了しました。
    echo.
)
exit /b 0
