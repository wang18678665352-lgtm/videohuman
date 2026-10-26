@echo off
rem videohuman 服务器一键启动(原生模式):0.0.0.0 监听(局域网可达)+ token 鉴权
rem token 在 configs\server.local.bat 里配置(见同目录 .example 模板)
cd /d %~dp0

if exist configs\server.local.bat (
    call configs\server.local.bat
) else (
    echo [提示] configs\server.local.bat 不存在,将只读取系统环境变量中的 VH_WEB_TOKEN
)
if "%VH_WEB_TOKEN%"=="" (
    echo [错误] VH_WEB_TOKEN 为空:局域网监听必须设置访问令牌,服务将拒绝启动
    echo 复制 configs\server.local.bat.example 为 server.local.bat 并填入 token
    pause
    exit /b 1
)

rem 延迟 5 秒等服务起来,再用 Chrome 打开页面(独立窗口,不占用本控制台)
start "" /min cmd /c "timeout /t 5 /nobreak >nul && start "" chrome.exe http://127.0.0.1:8100"

echo videohuman 服务器启动中:
echo   本机:   http://127.0.0.1:8100
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo   局域网: http:%%a:8100
echo 关闭本窗口即停止服务
uv run --no-sync vh serve --host 0.0.0.0 --port 8100
pause
