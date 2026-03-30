"""
乐谱识别 MIDI 编辑器 - 程序入口
运行前请先执行 setup_env.bat 安装依赖
"""
import sys
import os

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ⚠️ 必须在 pygame/fluidsynth 初始化之前导入 onnxruntime，
# 否则 pygame 加载的音频 DLL 会导致 onnxruntime DLL 初始化失败
try:
    import onnxruntime as _ort  # noqa: F401
except Exception:
    pass  # 未安装则忽略，运行时再报错

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore    import Qt
from PyQt6.QtGui     import QFont

from ui.main_window import MainWindow


def main():
    # 高 DPI 支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("乐谱 MIDI 编辑器")
    app.setOrganizationName("MusicProject")

    # 设置默认字体
    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
