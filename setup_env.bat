@echo off
chcp 65001 >nul
echo ============================================
echo    乐谱识别 MIDI 编辑器 - 环境安装脚本
echo ============================================
echo.

REM 检查 Python 3.11 是否安装
py -3.11 --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python 3.11
    echo 请前往 https://www.python.org/downloads/release/python-3119/ 下载安装
    echo 安装时勾选 "Add Python to PATH"
    pause
    exit /b 1
)

echo [1/5] 创建 Python 3.11 虚拟环境...
py -3.11 -m venv venv
if errorlevel 1 (
    echo [错误] 虚拟环境创建失败
    pause
    exit /b 1
)

echo [2/5] 激活虚拟环境...
call venv\Scripts\activate.bat

echo [3/5] 升级 pip...
python -m pip install --upgrade pip

echo [4/5] 安装依赖包（首次安装约需 10-20 分钟，请耐心等待）...
pip install PyQt6 mido Pillow PyMuPDF pygame numpy opencv-python music21
if errorlevel 1 (
    echo [错误] 基础依赖安装失败
    pause
    exit /b 1
)

echo.
echo 正在安装 PaddlePaddle（约 1GB，请确保网络稳定）...
pip install paddlepaddle -i https://mirror.baidu.com/pypi/simple
pip install paddleocr -i https://mirror.baidu.com/pypi/simple

echo.
echo 正在安装 oemer（五线谱识别）...
pip install oemer

echo.
echo 正在安装 pyfluidsynth（高品质音色播放）...
pip install pyfluidsynth
echo.
echo [5/5] 检查 FluidSynth 动态库...
echo.
echo ============================================
echo  [重要] 真实钢琴音色需要额外两个步骤：
echo.
echo  步骤 1: 下载 FluidSynth（Windows 二进制）
echo    https://github.com/FluidSynth/fluidsynth/releases
echo    解压后将 fluidsynth.dll 放到本项目根目录
echo.
echo  步骤 2: 下载 SoundFont 音色文件
echo    推荐：GeneralUser GS（免费）
echo    https://schristiancollins.com/generaluser.php
echo    下载后将 .sf2 文件放到本项目根目录
echo    并在 config.py 中设置 SOUNDFONT_PATH
echo ============================================
echo.
echo 安装完成！运行方式：
echo   venv\Scripts\activate
echo   python main.py
echo.
pause
