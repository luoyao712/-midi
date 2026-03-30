@echo off
chcp 65001 >nul
echo ============================================
echo  乐谱MIDI编辑器 — 全量打包（独立 exe）
echo  预计耗时：20-40 分钟，输出约 3-5 GB
echo ============================================
echo.
echo [警告] 此方案体积极大，建议优先使用 build_launcher.bat
echo 按任意键继续，Ctrl+C 取消...
pause >nul

if not exist "venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境，请先运行 setup_env.bat
    pause
    exit /b 1
)

echo [1/2] 安装 PyInstaller...
venv\Scripts\pip.exe install --quiet --upgrade pyinstaller

echo [2/2] 开始打包（请勿关闭此窗口）...
venv\Scripts\pyinstaller.exe music_editor.spec

if %errorlevel% neq 0 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo ============================================
echo  打包完成！输出目录：dist\乐谱MIDI编辑器\
echo  将该目录整体发给用户，双击其中的 乐谱MIDI编辑器.exe 即可运行
echo ============================================
pause
