import sys
import os
import subprocess
import json
import glob
import ctypes
import ctypes.wintypes
import time
import multiprocessing
import tempfile
import msvcrt

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTabWidget, QListWidget, QFrame, QSizePolicy,
    QSpacerItem, QListWidgetItem, QMessageBox, QFileDialog
)
from PyQt5.QtGui import QPixmap, QFont, QColor, QDesktopServices, QPalette, QBrush
from PyQt5.QtCore import Qt, QSize, QPoint, QUrl, QRect, QTimer, pyqtSignal, QProcess, QEvent, QPropertyAnimation

# --- 配置文件路径 ---
SETTINGS_FILE = "launcher_settings.json"

# --- Windows API 常量和控制台控制函数 ---
SW_HIDE = 0  # 隐藏窗口并激活另一个窗口。
SW_SHOW = 5  # 激活窗口并以当前大小和位置显示。
WM_CLOSE = 0x0010  # WM_CLOSE 消息，用于请求窗口关闭。

# 加载所需的 Windows DLL
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")
psapi = ctypes.WinDLL("psapi")  # 导入 psapi 以获取进程信息

# 定义 OpenProcess 的访问权限
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # 允许查询进程信息以获取模块文件名
PROCESS_TERMINATE = 0x0001 # 允许终止进程

# 全局列表，用于存储找到的 HWNDs（窗口句柄）
_enum_windows_callback_found_hwnds = []
_enum_windows_callback_target_exe_name_startswith = None


# 定义 EnumWindows 回调函数的签名。
# 此回调将用于枚举所有顶级窗口。
@ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.c_int, ctypes.c_long)
def _enum_windows_callback(hwnd, lParam):
    """
    EnumWindows 的回调函数。
    检查枚举到的窗口是否属于目标进程名称且可见。
    """
    global _enum_windows_callback_found_hwnds
    global _enum_windows_callback_target_exe_name_startswith

    if _enum_windows_callback_target_exe_name_startswith is None:
        return True  # 如果未设置目标名称，则继续枚举

    # 获取窗口的进程 ID
    process_id = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))

    if process_id.value == 0:
        return True  # 跳过系统空闲或无效进程

    # 打开进程以获取其可执行文件路径
    # 使用 PROCESS_QUERY_LIMITED_INFORMATION 获取最小权限
    process_handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
    if process_handle:
        path_buffer = ctypes.create_unicode_buffer(260)  # MAX_PATH
        if psapi.GetModuleFileNameExW(process_handle, None, path_buffer, 260):
            exe_path = path_buffer.value
            base_name = os.path.basename(exe_path).lower()
            target_name_lower = _enum_windows_callback_target_exe_name_startswith.lower()

            # 检查基本名称是否以目标名称开头
            if base_name.startswith(target_name_lower):
                # 如果匹配 EXE 名称，则将窗口句柄添加到全局列表
                _enum_windows_callback_found_hwnds.append(hwnd)
        kernel32.CloseHandle(process_handle)  # 总是关闭句柄
    return True  # 继续枚举以查找所有匹配的窗口


# 定义 Windows API 函数的参数类型
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.c_int, ctypes.c_long), ctypes.c_long]
user32.IsWindowVisible.argtypes = [ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_ulong)]
user32.ShowWindow.argtypes = [ctypes.c_int, ctypes.c_int]
user32.PostMessageW.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.PostMessageW.restype = ctypes.wintypes.BOOL


# 定义 OpenProcess 和 TerminateProcess 的参数类型
kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
kernel32.TerminateProcess.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_uint]
kernel32.TerminateProcess.restype = ctypes.wintypes.BOOL


# 定义 GetModuleFileNameExW 的参数类型
LPWSTR = ctypes.POINTER(ctypes.wintypes.WCHAR)
psapi.GetModuleFileNameExW.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.HMODULE, LPWSTR, ctypes.wintypes.DWORD]
psapi.GetModuleFileNameExW.restype = ctypes.wintypes.DWORD


def _find_window_handle_by_exe_name(exe_name_startswith, max_attempts=120, delay_s=0.5):
    """
    尝试查找可执行文件名称以给定字符串开头的进程的主窗口句柄。
    它会多次迭代并延迟，以允许进程创建其窗口。
    返回 HWNDs 列表。
    """
    global _enum_windows_callback_found_hwnds
    global _enum_windows_callback_target_exe_name_startswith

    print(
        f"开始查找可执行文件名称以 '{exe_name_startswith}' 开头的窗口（最多尝试 {max_attempts} 次，每次延迟 {delay_s} 秒）...")
    _enum_windows_callback_target_exe_name_startswith = exe_name_startswith
    _enum_windows_callback_found_hwnds = []  # 每次搜索前重置列表

    # 这里只执行一次枚举；调用者将使用 QTimer 处理重试
    user32.EnumWindows(_enum_windows_callback, 0)

    if _enum_windows_callback_found_hwnds:
        print(f"成功找到 {len(_enum_windows_callback_found_hwnds)} 个窗口句柄。")
    else:
        print(f"未找到可执行文件名称以 '{exe_name_startswith}' 开头的窗口。")
    return _enum_windows_callback_found_hwnds


class CustomTitleBar(QWidget):
    """
    自定义无边框 PyQt 窗口的标题栏。
    处理窗口拖动、最小化、最大化/恢复和关闭。
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.parent_window = parent
        self.setFixedHeight(50)
        self.setStyleSheet("background-color: #2a2a2a;")  # 深色标题栏

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addStretch()  # 将按钮推到右侧

        # 窗口控制按钮
        self.settings_btn = QPushButton("⚙")  # 带齿轮图标的设置按钮
        self.min_btn = QPushButton("-")
        self.close_btn = QPushButton("✕")

        # 设置控制按钮样式
        button_size = 50
        font_size = 20
        for btn in [self.settings_btn, self.min_btn, self.close_btn]:
            btn.setFixedSize(button_size, button_size)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: #cccccc;
                    border: none;
                    font-size: {font_size}px;
                }}
                QPushButton:hover {{
                    background-color: #4a4a4a;
                }}
                QPushButton#close_btn:hover {{
                    background-color: #ff0000;
                }}
            """)
            btn.setFlat(True)  # 使按钮看起来扁平
        self.close_btn.setObjectName("close_btn")
        self.settings_btn.setObjectName("settings_btn")

        # 将按钮信号连接到父窗口槽
        self.settings_btn.clicked.connect(self.parent_window.open_settings_placeholder)
        self.min_btn.clicked.connect(self.parent_window.showMinimized)
        self.close_btn.clicked.connect(self.parent_window.close)

        # 按顺序添加按钮：设置、最小化、关闭
        layout.addWidget(self.settings_btn)
        layout.addWidget(self.min_btn)
        layout.addWidget(self.close_btn)

        self.start_pos = None

    def mousePressEvent(self, event):
        """处理鼠标按下事件以开始拖动窗口。"""
        if event.button() == Qt.LeftButton:
            self.start_pos = event.globalPos() - self.parent_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """处理鼠标移动事件以拖动窗口。"""
        if self.start_pos is not None and event.buttons() == Qt.LeftButton:
            self.parent_window.move(event.globalPos() - self.start_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        """处理鼠标释放事件以停止拖动窗口。"""
        self.start_pos = None
        event.accept()


class SwipeableLabel(QLabel):
    """
    一个可以检测左右滑动手势的 QLabel。
    用于循环浏览新闻预览图像。
    """
    swipeDetected = pyqtSignal(int)  # -1 表示左滑，1 表示右滑

    def __init__(self, parent=None):
        super().__init__(parent)
        self.start_x = 0
        self.threshold = 50  # 检测滑动的最小距离

    def mousePressEvent(self, event):
        """记录鼠标按下的 X 坐标。"""
        if event.button() == Qt.LeftButton:
            self.start_x = event.pos().x()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """
        根据鼠标释放和按下 X 坐标之间的差异检测滑动方向，并发出信号。
        """
        if event.button() == Qt.LeftButton:
            end_x = event.pos().x()
            diff_x = end_x - self.start_x

            if diff_x > self.threshold:
                self.swipeDetected.emit(-1)  # 右滑，切换到上一条新闻
            elif diff_x < -self.threshold:
                self.swipeDetected.emit(1)  # 左滑，切换到下一条新闻
        super().mouseReleaseEvent(event)


class GameLauncher(QWidget):
    """
    游戏启动器主窗口小部件，模仿流行的游戏启动器布局和样式。
    """

    def __init__(self):
        super().__init__()
        print("GameLauncher: __init__ 调用。")
        self.setWindowTitle("游戏启动器")
        self.setGeometry(100, 100, 1440, 810)  # 设置初始窗口位置和大小
        self.setWindowFlags(Qt.FramelessWindowHint)  # 设置无边框窗口

        # 核心修改：更可靠地获取当前脚本/可执行文件目录
        # current_script_dir 用于访问应用程序中捆绑的资源（如 res, htmlget.py）。
        # 在单文件模式下，它指向临时解压目录（sys._MEIPASS）。
        if getattr(sys, 'frozen', False):
            # 如果是 PyInstaller 打包的可执行文件，使用 sys._MEIPASS 作为临时解压目录
            # sys._MEIPASS 指向 PyInstaller 在运行时创建的临时目录
            self.current_script_dir = sys._MEIPASS
            # exe_real_dir 用于持久化文件（如设置），指向实际 .exe 文件所在的目录
            self.exe_real_dir = os.path.dirname(sys.argv[0])
            print(f"GameLauncher: 检测到冻结模式。current_script_dir (临时): {self.current_script_dir}")
            print(f"GameLauncher: 检测到冻结模式。exe_real_dir (实际可执行文件路径): {self.exe_real_dir}")
        else:
            # 如果是 Python 脚本，使用 __file__ 的目录作为当前脚本目录和实际可执行文件目录
            self.current_script_dir = os.path.dirname(os.path.abspath(__file__))
            self.exe_real_dir = self.current_script_dir
            print(f"GameLauncher: 检测到脚本模式。current_script_dir/exe_real_dir: {self.current_script_dir}")

        # res_dir 仍然指向冻结时临时解压目录（sys._MEIPASS）中的 'res'
        self.res_dir = os.path.join(self.current_script_dir, 'res')
        # SETTINGS_FILE 现在使用 exe_real_dir 以确保它保存在 .exe 文件旁边
        self.settings_file_path = os.path.join(self.exe_real_dir, SETTINGS_FILE)

        self.htmlget_process = None
        self.game_process = None
        self.core_program_process = None  # 用于存储核心程序进程
        self.core_program_window_handles = []  # 存储多个句柄的列表
        self.core_program_hidden = True  # 跟踪控制台可见性状态
        self.core_program_path = None  # 新增：核心程序路径

        self.current_news_index = 0
        self.news_carousel_timer = QTimer(self)
        self.news_carousel_timer.timeout.connect(self._advance_carousel_and_display)
        self.carousel_cycle_count = 0

        self.data_check_timer = QTimer(self)
        self.data_check_timer.timeout.connect(self.check_and_reload_data)

        # 新增：检查游戏进程状态的定时器
        self.game_check_timer = QTimer(self)
        self.game_check_timer.timeout.connect(self._check_running_game_status)
        self.game_check_attempts = 0
        self.max_game_check_attempts = 60  # 最多检查 60 秒（每秒一次）

        # 定义颜色（对应于 CSS 变量）
        self.download_btn_bg_color = "#ffc107"  # 黄色
        self.download_btn_hover_bg_color = "#333333"  # 按钮背景在悬停时变暗
        self.download_btn_text_color = "#333333"
        self.download_btn_hover_text_color = "#ffc107"  # 按钮文本在悬停时变为黄色

        self.play_btn_bg_color = "#4CAF50"  # 绿色
        self.play_btn_hover_bg_color = "#333333"  # 按钮背景在悬停时变暗
        self.play_btn_text_color = "#FFFFFF"  # 初始文本白色
        self.play_btn_hover_text_color = "#4CAF50"  # 文本在悬停时变为绿色

        self.sub_text_color = "#aaaaaa"
        self.sub_text_hover_color = "#eeeeee"
        self.transition_speed_ms = 300  # 300ms 过渡速度

        # 加载游戏路径设置
        self.game_exe_path = self.load_game_path_setting()
        self.core_program_path = self.load_core_program_path_setting()  # 加载核心程序路径
        print(f"GameLauncher: 初始 game_exe_path: {self.game_exe_path}")
        print(f"GameLauncher: 初始 core_program_path: {self.core_program_path}")  # 调试行

        # 启动顺序：首先启动后台脚本，然后加载数据，最后初始化和更新 UI
        print("GameLauncher: 启动后台脚本。")
        self.start_background_scripts()
        print("GameLauncher: 加载抓取的数据 JSON。")
        self.scraped_data = self.load_scraped_data_json()  # 初始数据加载，可能尚未完全抓取

        print("GameLauncher: 初始化 UI。")
        self.initUI()
        print("GameLauncher: 应用样式。")
        self.applyStyles()
        print("GameLauncher: 更新动态内容。")
        self.updateDynamicContent()  # 初始内容更新，可能显示“加载中...”
        print("GameLauncher: 填充新闻。")
        self.populateNews()
        print("GameLauncher: 启动新闻轮播。")
        self.start_news_carousel()  # 初始新闻轮播，可能显示占位符

        # 启动核心程序（现在内部处理路径查找和提示）
        print("GameLauncher: 启动核心程序。")
        self.start_core_program()

        # 为按钮安装事件过滤器以处理悬停事件
        print("GameLauncher: 为 start_game_btn 安装事件过滤器。")
        self.start_game_btn.installEventFilter(self)

        # 单次定时器，确保 UI 完全初始化后更新游戏路径 UI
        print("GameLauncher: 安排游戏路径的初始 UI 更新。")
        QTimer.singleShot(0, self.update_game_path_ui)
        print("GameLauncher: __init__ 完成。")

    def load_core_program_path_setting(self):
        """
        从配置文件加载核心程序可执行文件路径。
        如果文件不存在或加载失败，则返回 None。
        """
        print("load_core_program_path_setting: 尝试加载核心程序设置。")
        if os.path.exists(self.settings_file_path):
            try:
                with open(self.settings_file_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    path = settings.get("core_program_path")
                    if path:
                        if os.path.exists(path):
                            if os.path.isfile(path):
                                print(
                                    f"load_core_program_path_setting: 成功从 {SETTINGS_FILE} 加载并验证核心程序路径：'{path}'。")
                                return path
                            else:
                                print(
                                    f"load_core_program_path_setting: 警告：{SETTINGS_FILE} 中的核心程序路径 '{path}' 存在但不是文件。")
                        else:
                            print(
                                f"load_core_program_path_setting: 警告：{SETTINGS_FILE} 中的核心程序路径 '{path}' 不存在。")
                    else:
                        print(f"load_core_program_path_setting: 警告：在 {SETTINGS_FILE} 中未找到核心程序路径，或路径为空。")
            except json.JSONDecodeError as e:
                print(
                    f"load_core_program_path_setting: 错误：{SETTINGS_FILE} 的核心程序部分中 JSON 解码错误：{e}。")
            except Exception as e:
                print(f"load_core_program_path_setting: 读取 {SETTINGS_FILE} 的核心程序部分时出错：{e}。")
        else:
            print(f"load_core_program_path_setting: 信息：未找到设置文件 {SETTINGS_FILE}。")
        return None

    def save_core_program_path_setting(self):
        """
        将当前核心程序可执行文件路径保存到配置文件。
        """
        print("save_core_program_path_setting: 尝试保存核心程序路径。")
        settings = {}
        if os.path.exists(self.settings_file_path):
            try:
                with open(self.settings_file_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"save_core_program_path_setting: 读取现有设置文件时出错，将创建新文件：{e}")

        settings["core_program_path"] = self.core_program_path

        try:
            with open(self.settings_file_path, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=4)
            print(f"save_core_program_path_setting: 核心程序路径已保存到 {SETTINGS_FILE}：{self.core_program_path}")
        except Exception as e:
            print(f"save_core_program_path_setting: 错误：无法将核心程序路径保存到 {SETTINGS_FILE}：{e}")

    def _prompt_for_core_program_file(self):
        """
        打开文件对话框，供用户选择核心程序可执行文件（idv-login*.exe）。
        返回选定的文件路径或 None。
        """
        print("prompt_for_core_program_file: 提示用户选择核心程序。")
        file_dialog = QFileDialog(self)
        file_dialog.setWindowTitle("选择核心程序 (idv-login*.exe)")
        file_dialog.setFileMode(QFileDialog.ExistingFile)
        file_dialog.setNameFilter("核心程序 (*idv-login*.exe);;所有文件 (*)")

        # 尝试使用当前已知路径的目录，否则使用默认目录
        initial_dir = os.path.dirname(self.core_program_path) if self.core_program_path and os.path.exists(
            os.path.dirname(self.core_program_path)) else self.exe_real_dir  # 使用 exe_real_dir 作为初始路径
        if not os.path.exists(initial_dir):
            initial_dir = os.path.expanduser("~")
        file_dialog.setDirectory(initial_dir)

        if file_dialog.exec_():
            selected_files = file_dialog.selectedFiles()
            if selected_files:
                selected_path = selected_files[0]
                # 简单检查文件名是否包含 'idv-login'，如果不包含则提示
                if "idv-login" not in os.path.basename(selected_path).lower():
                    reply = QMessageBox.question(self, "确认核心程序",
                                                 f"您选择的文件 '{os.path.basename(selected_path)}' 的名称中不包含 'idv-login'。您确定这是正确的核心程序吗？",
                                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if reply == QMessageBox.No:
                        print("prompt_for_core_program_file: 由于文件名不匹配，用户取消了文件选择。")
                        return None
                print(f"prompt_for_core_program_file: 用户选择核心程序: {selected_path}")
                return selected_path
        print("prompt_for_core_program_file: 用户取消了核心程序选择。")
        return None

    def start_core_program(self):
        """
        在启动器启动时，查找并启动核心程序（以 'idv-login' 开头）
        并默认隐藏其控制台窗口。
        """
        print("start_core_program: 检查平台。")
        if sys.platform != "win32":
            print("start_core_program: 非 Windows 平台，不尝试启动 idv-login 核心程序或控制其控制台。")
            return

        core_program_to_launch = None

        # 调试：打印初始 core_program_path 值
        print(f"start_core_program: 从 __init__ 加载的 core_program_path: {self.core_program_path}")

        # 尝试 1：从加载的设置中获取核心程序路径
        if self.core_program_path and os.path.exists(self.core_program_path) and os.path.isfile(self.core_program_path):
            core_program_to_launch = self.core_program_path
            print(f"start_core_program: 来自设置的核心程序路径 '{core_program_to_launch}' 已验证为有效。")
        else:  # 此 'else' 块是关键。
            print(
                f"start_core_program: 从设置加载的核心程序路径 ('{self.core_program_path}') 无效或不存在，尝试其他查找方法。")
            # 尝试 2：在当前可执行文件的实际目录中搜索 idv-login*.exe
            print(f"start_core_program: 在 {self.exe_real_dir} 中搜索 'idv-login*.exe'。")
            exe_files = glob.glob(os.path.join(self.exe_real_dir, "idv-login*.exe"))
            if exe_files:
                core_program_to_launch = exe_files[0]
                self.core_program_path = core_program_to_launch  # 更新实例变量
                self.save_core_program_path_setting()  # 保存找到的路径
                print(f"start_core_program: 在实际可执行文件目录中找到核心程序：'{core_program_to_launch}'，并已保存。")
            else:
                # 尝试 3：如果仍未找到，提示用户手动选择
                print("start_core_program: 自动搜索未能找到核心程序，提示用户手动选择。")
                selected_path = self._prompt_for_core_program_file()
                if selected_path:
                    core_program_to_launch = selected_path
                    self.core_program_path = core_program_to_launch  # 更新实例变量
                    self.save_core_program_path_setting()  # 保存用户选择的路径
                    print(f"start_core_program: 用户手动选择核心程序：'{core_program_to_launch}'，并已保存。")
                else:
                    QMessageBox.warning(self, "未找到核心程序", "未能找到核心程序。核心功能可能不可用。")
                    print("start_core_program: 用户取消了核心程序选择，核心功能可能不可用。")
                    self.core_program_path = None  # 确保路径为空
                    return  # 无法启动核心程序，直接返回

        if not core_program_to_launch:
            print("start_core_program: 未能找到或用户未选择核心程序，无法启动。")
            return

        # 启动核心程序
        try:
            core_program_dir = os.path.dirname(core_program_to_launch)
            core_exe_basename = os.path.basename(core_program_to_launch)

            # 使用 ShellExecuteW 启动核心程序并请求管理员权限
            # "runas" 是关键，它会触发 UAC 提示以请求管理员权限
            ret_val = ctypes.windll.shell32.ShellExecuteW(
                None,  # 窗口句柄
                "runas",  # 操作：以管理员身份运行
                os.fspath(core_program_to_launch),  # 文件名
                "",  # 参数
                os.fspath(core_program_dir),  # 工作目录
                SW_HIDE  # 默认隐藏窗口
            )
            print(f"start_core_program: ShellExecuteW 启动核心程序返回：{ret_val}")

            if ret_val <= 32:  # 返回值 <= 32 表示错误
                error_code = ctypes.windll.kernel32.GetLastError()
                QMessageBox.critical(self, "核心程序启动错误",
                                     f"未能启动核心程序 (ShellExecuteW 失败)。错误代码: {error_code}\n路径: {core_program_to_launch}\n\n请确保程序可以以管理员身份运行。")
                print(f"start_core_program: ShellExecuteW 启动核心程序失败，错误代码: {error_code}")
                self.core_program_process = None  # 无法启动进程，清除引用
                return

            print(f"start_core_program: 尝试以管理员身份启动核心程序：'{core_program_to_launch}'")

            # 通过 ShellExecuteW 启动的进程没有直接的 Popen 对象，因此我们无法直接跟踪其 PID。
            # 为了控制其窗口，我们需要在一段时间后找到其窗口句柄。
            # 这里我们不设置 self.core_program_process，因为它不再是 Popen 对象。
            # 但是，我们可以模拟一个简单的 QProcess 来处理窗口句柄查找。
            # 为了简化，我们只依赖 QTimer 和 _find_window_handle_by_exe_name。
            # 我们假设如果 ShellExecuteW 成功返回，则进程已开始运行。

            # 使用 QTimer 异步查找窗口句柄，以便后续的显示/隐藏切换
            QTimer.singleShot(2000, lambda: self._get_core_program_console_handle(core_exe_basename))

        except Exception as e:
            print(f"start_core_program: 错误：启动核心程序时发生异常：{e}")
            QMessageBox.critical(self, "核心程序启动错误",
                                 f"未能启动核心程序。\n错误：{e}\n\n请确认 '{os.path.basename(core_program_to_launch)}' 是否存在且是有效的可执行文件。如果它是命令行程序，它应该创建自己的控制台；如果它是 GUI 程序，此错误可能表示无法以这种方式启动。")
            self.core_program_process = None

    def _get_core_program_console_handle(self, exe_name_to_find):  # 确保这里接受 exe_name_to_find
        """
        异步检索核心程序的所有控制台窗口句柄。
        只检索句柄，不执行隐藏/显示操作。
        现在按可执行文件名搜索窗口。
        """
        print(f"_get_core_program_console_handle: 检查核心程序进程状态。尝试查找 '{exe_name_to_find}' 的句柄。")
        # 不再检查 self.core_program_process.poll()，因为 ShellExecuteW 没有 Popen 对象。
        # 我们假设如果 core_program_path 有效，核心程序应该正在运行或即将运行。
        if not self.core_program_path or not os.path.exists(self.core_program_path):
            print("_get_core_program_console_handle: 核心程序路径无效或不存在，无法获取其控制台句柄。")
            self.core_program_window_handles = []
            return

        print(
            f"_get_core_program_console_handle: 尝试获取核心程序控制台窗口句柄（名称以 '{exe_name_to_find}' 开头）...")
        # _find_window_handle_by_exe_name 函数内部已包含重试逻辑
        self.core_program_window_handles = _find_window_handle_by_exe_name(exe_name_to_find, max_attempts=120,
                                                                           delay_s=0.5)

        if self.core_program_window_handles:
            print(f"成功检索到 {len(self.core_program_window_handles)} 个核心程序控制台窗口句柄。")
            # 检查第一个句柄的可见性以设置整体隐藏状态。
            # 假设所有相关窗口都具有相同的初始可见性。
            if user32.IsWindowVisible(self.core_program_window_handles[0]):
                self.core_program_hidden = False
                print("_get_core_program_console_handle: 警告：至少一个核心程序窗口在启动时未按预期隐藏。")
            else:
                self.core_program_hidden = True
        else:
            print(
                "_get_core_program_console_handle: 警告：未能找到任何核心程序控制台窗口句柄。这可能意味着它不是控制台应用程序，或者窗口尚未创建。控制台显示/隐藏功能将不起作用。")
            self.core_program_hidden = False  # 如果未找到句柄，则重置为 False，因为我们无法控制它。

    def load_game_path_setting(self):
        """
        从配置文件加载游戏可执行文件路径。
        如果文件不存在或加载失败，则返回默认路径。
        """
        print("load_game_path_setting: 尝试加载设置。")
        default_path = "D:/dwrg2/dwrg.exe"  # 你的默认游戏路径
        if os.path.exists(self.settings_file_path):
            try:
                with open(self.settings_file_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    path = settings.get("game_exe_path")
                    if path and os.path.isfile(path):  # 检查路径是否存在且是文件
                        print(f"load_game_path_setting: 从 {SETTINGS_FILE} 加载游戏路径: {path}")
                        return path
                    elif path:
                        print(
                            f"load_game_path_setting: 警告：{SETTINGS_FILE} 中的游戏路径 '{path}' 无效或文件不存在。使用默认路径。")
            except json.JSONDecodeError as e:
                print(f"load_game_path_setting: 错误：{SETTINGS_FILE} 中的 JSON 解码错误：{e}。使用默认路径。")
            except Exception as e:
                print(f"load_game_path_setting: 读取 {SETTINGS_FILE} 时出错：{e}。使用默认路径。")
        else:
            print(f"load_game_path_setting: 信息：未找到设置文件 {SETTINGS_FILE}。使用默认游戏路径。")
        return default_path

    def save_game_path_setting(self):
        """
        将当前游戏可执行文件路径保存到配置文件。
        """
        print("save_game_path_setting: 尝试保存游戏路径。")
        settings = {}
        if os.path.exists(self.settings_file_path):
            try:
                with open(self.settings_file_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"save_game_path_setting: 读取现有设置文件时出错，将创建新文件：{e}")

        settings["game_exe_path"] = self.game_exe_path
        try:
            with open(self.settings_file_path, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=4)
            print(f"save_game_path_setting: 游戏路径已保存到 {SETTINGS_FILE}: {self.game_exe_path}")
        except Exception as e:
            print(f"save_game_path_setting: 错误：无法将游戏路径保存到 {SETTINGS_FILE}: {e}")

    def _advance_carousel_and_display(self):
        """推进新闻轮播并显示相应的图像。"""
        if not self.scraped_data["news_list"]:
            return

        num_news = len(self.scraped_data["news_list"])
        if num_news == 0:
            return

        self.current_news_index = (self.current_news_index + 1) % num_news

        if self.current_news_index == 0:
            self.carousel_cycle_count += 1

        if self.carousel_cycle_count >= 2 and num_news > 0:
            self.news_carousel_timer.stop()
            self._set_preview_image(self.current_news_index)
            return

        self._set_preview_image(self.current_news_index)

    def _terminate_process(self, process, name):
        """终止 QProcess 或 subprocess.Popen 进程。"""
        print(f"_terminate_process: 尝试终止 {name} 进程。")
        if isinstance(process, QProcess):
            if process.state() == QProcess.Running:
                process.kill()
                process.waitForFinished(1000)
                print(f"_terminate_process: QProcess {name} 进程已终止。")
        elif isinstance(process, subprocess.Popen):
            if process.poll() is None:  # 检查进程是否仍在运行
                process.terminate()  # 尝试正常终止
                try:
                    process.wait(timeout=1)  # 短暂等待终止
                except subprocess.TimeoutExpired:
                    process.kill()  # 如果未终止，则强制终止
                print(f"_terminate_process: subprocess {name} 进程已终止。")

    def _setup_process_signals(self, process, name, stdout_handler, stderr_handler, finished_handler):
        """为 QProcess 设置信号连接。"""
        print(f"_setup_process_signals: 为 {name} 设置信号。")
        process.readyReadStandardOutput.connect(stdout_handler)
        process.readyReadStandardError.connect(stderr_handler)
        process.finished.connect(finished_handler)
        print(f"为 {name} 设置信号连接。")

    def start_background_scripts(self):
        """启动后台脚本 htmlget.py。"""
        print("start_background_scripts: 检查 htmlget.py 路径。")
        htmlget_script_path = os.path.join(self.current_script_dir, "htmlget.py")
        if os.path.exists(htmlget_script_path):
            self._terminate_process(self.htmlget_process, "htmlget.py")
            self.htmlget_process = QProcess(self)
            self.htmlget_process.setProgram(sys.executable)
            self.html_get_args = [htmlget_script_path]
            # 添加一个标志，指示它是否在冻结的应用程序中运行，如果 htmlget.py 需要它
            if getattr(sys, 'frozen', False):
                self.html_get_args.append('--frozen')
            self.htmlget_process.setArguments(self.html_get_args)
            self._setup_process_signals(
                self.htmlget_process, "htmlget.py",
                self.handle_htmlget_output, self.handle_htmlget_error, self.handle_htmlget_finished
            )
            print(f"start_background_scripts: 启动 htmlget.py: {sys.executable} {' '.join(self.html_get_args)}")
            self.htmlget_process.start()
        else:
            QMessageBox.warning(self, "错误",
                                f"未找到 htmlget.py 文件: {htmlget_script_path}。新闻和背景可能无法加载。")
            print(f"start_background_scripts: 警告：未找到 htmlget.py 文件: {htmlget_script_path}。")

    def handle_htmlget_output(self):
        """处理 htmlget.py 脚本的标准输出。"""
        data = self.htmlget_process.readAllStandardOutput().data().decode('utf-8', errors='ignore')
        print(f"handle_htmlget_output: htmlget.py 标准输出: {data.strip()}")

    def handle_htmlget_error(self):
        """处理 htmlget.py 脚本的标准错误。"""
        data = self.htmlget_process.readAllStandardError().data().decode('utf-8', errors='ignore')
        print(f"handle_htmlget_error: htmlget.py 标准错误: {data.strip()}")

    def handle_htmlget_finished(self, exitCode, exitStatus):
        """处理 htmlget.py 脚本完成时的事件。"""
        print(f"handle_htmlget_finished: htmlget.py 进程已完成，退出代码: {exitCode}, 状态: {exitStatus}")
        if exitCode == 0:
            # 如果 htmlget.py 成功完成，立即尝试加载数据
            self.check_and_reload_data()
            # 如果数据文件尚未完全准备好（例如，文件系统延迟），则启动定时器以继续检查
            data_file_path = os.path.join(self.res_dir, "web_data.json")
            bg_img_path = os.path.join(self.res_dir, "bg_img.jpg")
            first_news_img_path = os.path.join(self.res_dir, "new1_img.jpg")  # 检查第一张新闻图片

            if not (os.path.exists(data_file_path) and os.path.exists(bg_img_path) and os.path.exists(
                    first_news_img_path)):
                if not self.data_check_timer.isActive():
                    self.data_check_timer.start(2000)
                    print("handle_htmlget_finished: htmlget.py 成功完成，启动数据文件检查定时器。")
            else:
                print("handle_htmlget_finished: htmlget.py 成功完成，所有预期数据文件已就绪，无需启动定时器。")

        else:
            QMessageBox.warning(self, "数据抓取失败", "新闻和背景数据抓取脚本失败。请检查命令行输出或日志文件。")
            # 如果进程失败，则停止定时器（如果它正在运行），因为数据不再预期会成功生成
            if self.data_check_timer.isActive():
                self.data_check_timer.stop()
            # 即使失败，也尝试重新加载，以防生成了部分数据或显示旧数据
            self.check_and_reload_data()
        self.update_game_path_ui()  # 确保在数据加载后更新游戏路径 UI

    def load_scraped_data_json(self):
        """
        加载抓取的 JSON 数据。
        如果文件不存在或解析失败，则返回默认数据。
        """
        print("load_scraped_data_json: 尝试加载 web_data.json。")
        data = {
            "season": "加载季节信息...",
            "background_img": "",
            "background_image_local_path": "",
            "news_list": []
        }
        data_file_path = os.path.join(self.res_dir, "web_data.json")

        if not os.path.exists(data_file_path):
            print(f"load_scraped_data_json: 信息：未找到数据文件 {data_file_path}，使用默认值。等待抓取脚本完成。")
            return data

        try:
            with open(data_file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)

                data["season"] = json_data.get("season", "加载季节信息...")
                data["background_img"] = json_data.get("background_img", "")
                data["news_list"] = json_data.get("news_list", [])

                if data["background_img"]:
                    data["background_image_local_path"] = os.path.join(self.res_dir, "bg_img.jpg")

                processed_news_list = []
                # 限制新闻数量并确保图像路径可预测
                for i, news_item in enumerate(data["news_list"][:4]):  # 只处理前 4 条新闻
                    news_item_copy = news_item.copy()
                    news_item_copy["image_local_path"] = os.path.join(self.res_dir, f"new{i + 1}_img.jpg")
                    processed_news_list.append(news_item_copy)
                data["news_list"] = processed_news_list

            print("load_scraped_data_json: JSON 数据文件加载成功。")
            return data
        except json.JSONDecodeError as e:
            print(f"load_scraped_data_json: 错误：web_data.json 中的 JSON 解码错误：{e}，使用默认值。")
        except Exception as e:
            print(f"load_scraped_data_json: 读取或解析 web_data.json 时出错：{e}，使用默认值。")
        return data

    def check_and_reload_data(self):
        """
        检查数据文件和图像是否已生成，如果数据已更改，则重新加载数据并更新 UI。
        """
        print("check_and_reload_data: 检查数据文件和图像。")
        data_file_path = os.path.join(self.res_dir, "web_data.json")
        bg_img_path = os.path.join(self.res_dir, "bg_img.jpg")
        # 检查至少第一张新闻图像是否存在，作为数据完成的标志
        first_news_img_path = os.path.join(self.res_dir, "new1_img.jpg")

        # 仅当数据文件和必要的图像存在时才尝试重新加载和更新 UI
        if os.path.exists(data_file_path) and os.path.exists(bg_img_path) and os.path.exists(first_news_img_path):
            new_scraped_data = self.load_scraped_data_json()
            # 仅当数据实际发生更改时才更新 UI，以避免不必要的重绘
            if new_scraped_data != self.scraped_data:
                self.scraped_data = new_scraped_data
                print("check_and_reload_data: 检测到数据文件更新，重新加载 UI 内容。")
                self.updateDynamicContent()
                self.populateNews()
                self.start_news_carousel()  # 使用新数据重新启动轮播
                self.applyStyles()  # 重新应用样式以更新背景图像
                print("check_and_reload_data: JSON 数据文件成功加载并更新 UI。")
                # 数据已加载且 UI 已更新，停止定时器
                if self.data_check_timer.isActive():
                    self.data_check_timer.stop()
                    print("check_and_reload_data: 数据已加载，数据检查定时器已停止。")
            else:
                # 数据未更改，但如果定时器处于活动状态且数据已完成，则停止定时器
                if self.data_check_timer.isActive() and \
                        self.scraped_data["season"] != "加载季节信息..." and \
                        self.scraped_data["news_list"]:
                    self.data_check_timer.stop()
                    print("check_and_reload_data: 数据未更改但已完成，数据检查定时器已停止。")
        else:
            print("check_and_reload_data: 数据文件或图像仍在等待生成...")

    def initUI(self):
        """初始化用户界面。"""
        print("initUI: 设置主布局。")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.title_bar = CustomTitleBar(self)
        main_layout.addWidget(self.title_bar)

        self.content_frame = QFrame(self)
        self.content_frame.setObjectName("contentFrame")

        content_layout = QHBoxLayout(self.content_frame)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        center_area = QWidget()
        center_layout = QVBoxLayout(center_area)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        top_banner_overlay = QFrame()
        top_banner_overlay.setFixedHeight(180)
        top_banner_overlay_layout = QVBoxLayout(top_banner_overlay)
        top_banner_overlay_layout.setContentsMargins(30, 40, 30, 10)
        top_banner_overlay_layout.setSpacing(10)

        self.version_status_label = QLabel("加载季节信息...")
        # 字体将在 applyStyles 中统一设置
        self.version_status_label.setStyleSheet("color: white; background: transparent;")
        top_banner_overlay_layout.addWidget(self.version_status_label)
        top_banner_overlay_layout.addStretch()

        center_layout.addWidget(top_banner_overlay, alignment=Qt.AlignTop)
        center_layout.addStretch()

        bottom_overlay_frame = QFrame()
        bottom_overlay_layout = QHBoxLayout(bottom_overlay_frame)
        bottom_overlay_layout.setContentsMargins(30, 0, 30, 20)
        bottom_overlay_layout.setSpacing(20)

        info_panel = QFrame()
        info_panel.setFixedWidth(550)
        info_panel.setStyleSheet("background-color: rgba(0, 0, 0, 0.6); border-radius: 10px;")
        info_panel_layout = QVBoxLayout(info_panel)
        info_panel_layout.setContentsMargins(15, 15, 15, 15)
        info_panel_layout.setSpacing(10)

        self.small_preview_label = SwipeableLabel()
        self.small_preview_label.setFixedSize(520, 240)
        self.small_preview_label.setStyleSheet("background-color: #333; border-radius: 5px;")
        self.small_preview_label.setAlignment(Qt.AlignCenter)
        self.small_preview_label.setText("活动预览")
        info_panel_layout.addWidget(self.small_preview_label)
        self.small_preview_label.swipeDetected.connect(self.handle_swipe)

        self.tab_widget = QTabWidget()
        self.tab_widget.setMinimumHeight(320)
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane { /* 移除选项卡面板的边框 */
                border: 0px;
            }
            QTabBar::tab { /* 选项卡样式 */
                background: transparent;
                color: #cccccc;
                padding: 5px 15px;
                border: none;
            }
            QTabBar::tab:selected { /* 选定选项卡样式 */
                color: white;
                border-bottom: 2px solid #ffd700; /* 黄色下划线 */
            }
        """)

        self.activities_and_news_list = QListWidget()
        self.activities_and_news_list.setWordWrap(True)  # 自动换行
        self.activities_and_news_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # 禁用水平滚动条
        self.activities_and_news_list.setStyleSheet("""
            QListWidget {{
                background: transparent;
                color: #cccccc;
                border: none;
            }}
            QListWidget::item {{ /* 列表项样式 */
                padding: 5px;
            }}
            QListWidget::item:hover {{ /* 列表项悬停样式 */
                background-color: rgba(255, 255, 255, 0.1);
            }}
            QListWidget QLabel {{ /* 列表项内 QLabel 样式 */
                color: #cccccc;
            }}
            QListWidget QLabel#time_label {{ /* 列表项内时间标签样式 */
                color: #999999;
            }}
        """)
        self.activities_and_news_list.itemClicked.connect(self.openNewsLink)  # 连接点击信号
        self.tab_widget.addTab(self.activities_and_news_list, "活动")

        info_panel_layout.addWidget(self.tab_widget)
        info_panel_layout.addStretch()

        bottom_overlay_layout.addWidget(info_panel)

        right_play_area = QFrame()
        right_play_area_layout = QVBoxLayout(right_play_area)
        right_play_area_layout.setContentsMargins(0, 0, 0, 0)
        right_play_area_layout.setSpacing(0)

        right_play_area_layout.addStretch(40)  # 顶部垫片用于整体垂直定位

        self.game_status_label = QLabel("")
        # 字体将在 applyStyles 中统一设置
        self.game_status_label.setStyleSheet("color: #cccccc; background: transparent; padding-left: 50px;")
        self.game_status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        right_play_area_layout.addWidget(self.game_status_label)  # 添加到布局

        # 启动游戏按钮
        self.start_game_btn = QPushButton()
        self.start_game_btn.setFixedSize(240, 60)
        # 字体将在 applyStyles 中统一设置
        self.start_game_btn.setContentsMargins(4, 0, 0, 0)

        # 按钮内的图标和文本标签
        self.btn_icon_label = QLabel()
        self.btn_icon_label.setFixedSize(24, 24)
        self.btn_icon_label.setAlignment(Qt.AlignCenter)

        self.btn_text_label = QLabel()
        self.btn_text_label.setAlignment(Qt.AlignCenter)

        # 按钮内的布局
        btn_layout = QHBoxLayout(self.start_game_btn)
        btn_layout.setContentsMargins(40, 0, 40, 0)
        btn_layout.setSpacing(10)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_icon_label)
        btn_layout.addWidget(self.btn_text_label)
        btn_layout.addStretch()

        self.start_game_btn.clicked.connect(self.start_game)

        play_button_row = QHBoxLayout()
        play_button_row.addStretch(30)
        play_button_row.addWidget(self.start_game_btn)
        play_button_row.addStretch(0)

        right_play_area_layout.addLayout(play_button_row)

        # --- “查找游戏”文本框的垂直间距调整变量 ---
        # 调整此值以控制“查找游戏”文本框与其上方按钮之间的距离。
        # 增加值会向下移动，减少值会向上移动。
        vertical_offset_between_buttons = 10
        right_play_area_layout.addSpacing(vertical_offset_between_buttons)

        # --- 查找游戏部分 ---
        self.locate_game_container = QWidget()
        self.locate_game_container.setFixedSize(250, 25)  # 宽度 200 像素
        self.locate_game_container.setCursor(Qt.PointingHandCursor)  # 悬停时显示手形光标
        self.locate_game_container.setStyleSheet("background: transparent;")

        locate_game_vbox = QVBoxLayout(self.locate_game_container)
        locate_game_vbox.setContentsMargins(0, 0, 0, 0)
        locate_game_vbox.setSpacing(0)
        locate_game_vbox.setAlignment(Qt.AlignCenter)  # 容器内容居中，但外部布局将控制其位置

        self.locate_game_label = QLabel("已安装？查找游戏")
        self.locate_game_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)  # 文本右对齐并垂直居中
        self.locate_game_label.setFixedSize(250, 25)  # 标签内部大小保持固定
        self.locate_game_label.move(0, 0)

        locate_game_vbox.addWidget(self.locate_game_label)

        # 覆盖 mousePressEvent 以处理点击
        self.locate_game_container.mousePressEvent = self.locate_game_file
        # --- 查找游戏部分结束 ---

        # 微调“locate_game_container”布局的水平位置
        locate_game_outer_layout = QHBoxLayout()
        locate_game_outer_layout.setContentsMargins(0, 0, 0, 0)
        locate_game_outer_layout.setSpacing(0)

        locate_game_outer_layout.addSpacing(520)  # 左侧添加 520px 固定空间以向右推动
        locate_game_outer_layout.addWidget(self.locate_game_container)
        locate_game_outer_layout.addStretch()  # 添加一个垫片以将剩余空间推向右侧，使文本框左对齐

        right_play_area_layout.addLayout(locate_game_outer_layout)
        right_play_area_layout.addStretch(5)  # 在底部添加一个垫片，确保上方元素尽可能高

        bottom_overlay_layout.addWidget(right_play_area)

        center_layout.addWidget(bottom_overlay_frame)
        content_layout.addWidget(center_area)
        main_layout.addWidget(self.content_frame)
        print("initUI: UI 初始化完成。")

    def open_settings_placeholder(self):
        """
        处理设置按钮点击事件。
        在 Windows 上，用于切换核心程序控制台窗口的可见性。
        在其他平台或核心程序未运行时显示通用消息。
        """
        print("open_settings_placeholder: 设置按钮被点击。")
        # 检查 self.core_program_window_handles 是否为空
        if sys.platform == "win32" and self.core_program_window_handles:
            if self.core_program_hidden:
                for hwnd in self.core_program_window_handles:  # 遍历所有找到的句柄
                    user32.ShowWindow(hwnd, SW_SHOW)
                QMessageBox.information(self, "设置", "所有核心程序命令行已显示。")
                self.core_program_hidden = False
            else:
                for hwnd in self.core_program_window_handles:  # 遍历所有找到的句柄
                    user32.ShowWindow(hwnd, SW_HIDE)
                QMessageBox.information(self, "设置", "所有核心程序命令行已隐藏。")
                self.core_program_hidden = True
        else:
            QMessageBox.information(self, "设置",
                                    "您可以在此处添加启动器设置。\n(非 Windows 平台或核心程序未运行/未找到控制台。)")

    def update_game_path_ui(self):
        """
        根据游戏安装状态更新游戏启动按钮和相关 UI 元素。
        """
        print("update_game_path_ui: 根据游戏安装状态更新游戏路径 UI。")
        # 图标路径
        download_icon_path_dark = os.path.join(self.res_dir, "download_icon_dark.png")
        download_icon_path_yellow = os.path.join(self.res_dir, "download_icon_yellow.png")
        play_icon_path_white = os.path.join(self.res_dir, "play_icon_white.png")
        play_icon_path_green = os.path.join(self.res_dir, "play_icon_green.png")

        # 检查游戏路径是否存在且是文件
        game_installed = os.path.exists(self.game_exe_path) and os.path.isfile(self.game_exe_path)
        print(f"update_game_path_ui: 游戏安装状态: {game_installed}, 路径: {self.game_exe_path}")

        # 确保查找游戏标签样式正确并管理容器可见性
        self.locate_game_label.setStyleSheet(f"color: {self.sub_text_color}; background: transparent;")

        if game_installed:
            # 仅当游戏未启动或未运行时才启用按钮
            if not self.game_check_timer.isActive() and (
                    self.game_process is None or (
                    isinstance(self.game_process, QProcess) and self.game_process.state() != QProcess.Running)):
                self.start_game_btn.setEnabled(True)
                print("update_game_path_ui: 游戏已安装，按钮已启用。")
            else:
                self.start_game_btn.setEnabled(False)  # 游戏正在启动或运行，禁用按钮
                print("update_game_path_ui: 游戏已安装，但游戏检查定时器处于活动状态或进程正在运行，按钮已禁用。")

            self.btn_text_label.setText("启动游戏")
            self.btn_text_label.setStyleSheet(f"color: {self.play_btn_text_color};")  # 默认白色文本

            # 为“启动游戏”按钮设置默认图标
            if os.path.exists(play_icon_path_white):
                pixmap = QPixmap(play_icon_path_white).scaled(36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.btn_icon_label.setPixmap(pixmap)
            else:
                self.btn_icon_label.clear()
                print(f"update_game_path_ui: 警告：未找到播放图标: {play_icon_path_white}")

            # 为“启动游戏”按钮应用 QSS 样式（移除了不支持的 box-shadow 和 transition）
            self.start_game_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.play_btn_bg_color};
                    color: {self.play_btn_text_color};
                    border-radius: 30px;
                    border: none;
                }}
                QPushButton:hover {{
                    background-color: {self.play_btn_hover_bg_color};
                    color: {self.play_btn_hover_text_color};
                }}
                QPushButton:pressed {{
                    background-color: #333333;
                }}
                QPushButton:disabled {{
                    background-color: #555555;
                    color: #aaaaaa;
                }}
            """)
            # 隐藏查找游戏部分
            self.locate_game_container.hide()
            print("update_game_path_ui: 查找游戏容器已隐藏。")

        else:  # 游戏未安装
            self.start_game_btn.setEnabled(True)  # 未安装时，“获取游戏”始终可点击
            print("update_game_path_ui: 游戏未安装，“获取游戏”按钮已启用。")
            self.btn_text_label.setText("获取游戏")
            self.btn_text_label.setStyleSheet(f"color: {self.download_btn_text_color};")  # 默认深色文本

            # 为“获取游戏”按钮设置默认图标
            if os.path.exists(download_icon_path_dark):
                pixmap = QPixmap(download_icon_path_dark).scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.btn_icon_label.setPixmap(pixmap)
            else:
                self.btn_icon_label.clear()
                print(f"update_game_path_ui: 警告：未找到下载图标: {download_icon_path_dark}")

            # 为“获取游戏”按钮应用 QSS 样式（移除了不支持的 box-shadow 和 transition）
            self.start_game_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.download_btn_bg_color};
                    color: {self.download_btn_text_color};
                    border-radius: 30px;
                    border: none;
                }}
                QPushButton:hover {{
                    background-color: {self.download_btn_hover_bg_color};
                    color: {self.download_btn_hover_text_color};
                }}
                QPushButton:pressed {{
                    background-color: #333333;
                }}
                QPushButton:disabled {{
                    background-color: #555555;
                    color: #aaaaaa;
                }}
            """)
            # 显示查找游戏部分
            self.locate_game_container.show()
            print("update_game_path_ui: 查找游戏容器可见。")

        # 确保 btn_text_label 继承父按钮的字体
        btn_font = self.start_game_btn.font()
        self.btn_text_label.setFont(btn_font)
        print("update_game_path_ui: UI 更新完成。")

    def eventFilter(self, obj, event):
        """
        事件过滤器，用于处理按钮悬停事件，切换图标和文本颜色。
        """
        # --- 处理按钮悬停事件（仅颜色和阴影更改，无移动） ---
        if obj == self.start_game_btn:
            if event.type() == QEvent.Enter:
                # 鼠标进入按钮区域
                if self.btn_text_label.text() == "启动游戏":
                    # 切换到悬停图标和文本颜色
                    play_icon_path_green = os.path.join(self.res_dir, "play_icon_green.png")
                    if os.path.exists(play_icon_path_green):
                        pixmap = QPixmap(play_icon_path_green).scaled(24, 24, Qt.KeepAspectRatio,
                                                                      Qt.SmoothTransformation)
                        self.btn_icon_label.setPixmap(pixmap)
                    self.btn_text_label.setStyleSheet(f"color: {self.play_btn_hover_text_color};")
                elif self.btn_text_label.text() == "获取游戏":
                    # 切换到悬停图标和文本颜色
                    download_icon_path_yellow = os.path.join(self.res_dir, "download_icon_yellow.png")
                    if os.path.exists(download_icon_path_yellow):
                        pixmap = QPixmap(download_icon_path_yellow).scaled(24, 24, Qt.KeepAspectRatio,
                                                                           Qt.SmoothTransformation)
                        self.btn_icon_label.setPixmap(pixmap)
                    self.btn_text_label.setStyleSheet(f"color: {self.download_btn_hover_text_color};")
            elif event.type() == QEvent.Leave:
                # 鼠标离开按钮区域
                if self.btn_text_label.text() == "启动游戏":
                    # 切换回默认图标和文本颜色
                    play_icon_path_white = os.path.join(self.res_dir, "play_icon_white.png")
                    if os.path.exists(play_icon_path_white):
                        pixmap = QPixmap(play_icon_path_white).scaled(24, 24, Qt.KeepAspectRatio,
                                                                      Qt.SmoothTransformation)
                        self.btn_icon_label.setPixmap(pixmap)
                    self.btn_text_label.setStyleSheet(f"color: {self.play_btn_text_color};")
                elif self.btn_text_label.text() == "获取游戏":
                    # 切换回默认图标和文本颜色
                    download_icon_path_dark = os.path.join(self.res_dir, "download_icon_dark.png")
                    if os.path.exists(download_icon_path_dark):
                        pixmap = QPixmap(download_icon_path_dark).scaled(24, 24, Qt.KeepAspectRatio,
                                                                         Qt.SmoothTransformation)
                        self.btn_icon_label.setPixmap(pixmap)
                    self.btn_text_label.setStyleSheet(f"color: {self.download_btn_text_color};")

        return super().eventFilter(obj, event)

    def locate_game_file(self, event):
        """
        处理“查找游戏”点击事件，打开文件对话框供用户选择游戏可执行文件。
        """
        # 确保事件被接受以防止传播
        event.accept()

        print("locate_game_file: 用户点击了查找游戏。")
        file_dialog = QFileDialog(self)
        file_dialog.setWindowTitle("查找游戏可执行文件")
        file_dialog.setFileMode(QFileDialog.ExistingFile)
        file_dialog.setNameFilter("可执行文件 (*.exe);;所有文件 (*)")
        # 默认目录为当前已知游戏路径的目录，或 D 盘，或用户主目录
        initial_dir = os.path.dirname(self.game_exe_path) if os.path.exists(
            os.path.dirname(self.game_exe_path)) else "D:/dwrg2"
        if not os.path.exists(initial_dir):
            initial_dir = os.path.expanduser("~")
        file_dialog.setDirectory(initial_dir)

        if file_dialog.exec_():
            selected_files = file_dialog.selectedFiles()
            if selected_files:
                new_path = selected_files[0]
                # 检查是否选择了 dwrg.exe，如果不是则提示用户
                if os.path.basename(new_path).lower() != "dwrg.exe":
                    reply = QMessageBox.question(self, "确认游戏文件",
                                                 f"您选择的文件不是 dwrg.exe ({os.path.basename(new_path)})，您仍然要使用此路径吗？",
                                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if reply == QMessageBox.No:
                        print("locate_game_file: 由于文件名不正确，用户取消了文件选择。")
                        return

                self.game_exe_path = new_path
                self.save_game_path_setting()  # 保存新游戏路径
                QMessageBox.information(self, "路径已更新", f"游戏路径已更新为: {self.game_exe_path}")
                print(f"locate_game_file: 游戏路径已更新为: {self.game_exe_path}")
                self.update_game_path_ui()

    def _check_running_game_status(self):
        """
        Windows 特定：定期检查游戏是否已启动并创建了其主窗口。
        """
        print(
            f"_check_running_game_status: 检查游戏进程状态，尝试 {self.game_check_attempts}/{self.max_game_check_attempts}...")
        self.game_check_attempts += 1

        # 查找 dwrg.exe 的窗口
        game_window_handles = _find_window_handle_by_exe_name("dwrg.exe", max_attempts=1, delay_s=0)

        if game_window_handles:
            print("_check_running_game_status: 检测到游戏窗口，游戏成功启动。")
            self.game_check_timer.stop()
            self.game_check_attempts = 0
            self.game_process = None  # 我们没有 ShellExecuteW 的 Popen 对象，这里重置为 None
            self.update_game_path_ui()  # 重新启用按钮
        elif self.game_check_attempts >= self.max_game_check_attempts:
            print("_check_running_game_status: 达到最大尝试次数，未检测到游戏窗口。假定启动失败。")
            self.game_check_timer.stop()
            self.game_check_attempts = 0
            self.update_game_path_ui()  # 重新启用按钮
            QMessageBox.warning(self, "游戏启动警告", "未能检测到游戏窗口。游戏可能未正确启动或已崩溃。")

    def start_game(self):
        """
        启动游戏。尝试以管理员权限运行。
        如果当前按钮文本是“获取游戏”，则打开下载链接。
        """
        print("start_game: 启动游戏进程。")
        if self.btn_text_label.text() == "获取游戏":
            QDesktopServices.openUrl(QUrl("https://adl.netease.com/d/g/id5/c/gbpc"))
            print("start_game: 打开游戏下载链接。")
            return

        if not os.path.exists(self.game_exe_path) or not os.path.isfile(self.game_exe_path):
            QMessageBox.warning(self, "启动失败", f"未找到游戏可执行文件: {self.game_exe_path}")
            print(f"start_game: 错误：未找到游戏可执行文件: {self.game_exe_path}")
            self.update_game_path_ui()
            return

        self.start_game_btn.setEnabled(False)  # 禁用按钮
        self.btn_text_label.setText("正在启动游戏...")  # 更新按钮文本
        self.btn_icon_label.clear()  # 清除按钮图标
        self.start_game_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #555555;
                color: #aaaaaa;
                border-radius: 30px;
                border: none;
            }}
        """)
        self.btn_text_label.setStyleSheet("color: #aaaaaa;")  # 禁用状态文本颜色

        try:
            if sys.platform == "win32":
                game_dir = os.path.dirname(self.game_exe_path)
                # 使用 ShellExecuteW 启动游戏并请求管理员权限
                ret_val = ctypes.windll.shell32.ShellExecuteW(
                    None,  # 窗口句柄
                    "runas",  # 操作，以管理员身份运行
                    os.fspath(self.game_exe_path),  # 文件名
                    "",  # 参数
                    os.fspath(game_dir),  # 工作目录
                    5  # SW_SHOW 命令，显示窗口
                )
                print(f"start_game: ShellExecuteW 返回: {ret_val}")
                if ret_val <= 32:  # 返回值 <= 32 表示错误
                    error_code = ctypes.windll.kernel32.GetLastError()
                    QMessageBox.critical(self, "启动错误",
                                         f"未能启动游戏 (ShellExecuteW 失败)。错误代码: {error_code}\n路径: {self.game_exe_path}")
                    print(f"start_game: ShellExecuteW 失败，错误代码: {error_code}")
                    self.update_game_path_ui()
                    return

                print(f"start_game: 尝试以管理员身份启动游戏：'{self.game_exe_path}'")
                # 启动定时器定期检查游戏窗口
                self.game_check_attempts = 0  # 重置尝试计数
                self.game_check_timer.start(1000)  # 每秒检查一次
            else:
                # 对于非 Windows，直接使用 QProcess
                self._terminate_process(self.game_process, "游戏")
                self.game_process = QProcess(self)
                self.game_process.setProgram(self.game_exe_path)
                self.game_process.setWorkingDirectory(os.path.dirname(self.game_exe_path))

                # 连接 started 信号以确定游戏是否已启动
                self.game_process.started.connect(self._on_game_process_started)
                self.game_process.finished.connect(self.handle_game_finished)
                self.game_process.start()
                print(f"start_game: 启动游戏 (QProcess): {self.game_exe_path}")

        except Exception as e:
            QMessageBox.critical(self, "启动错误", f"启动游戏时发生错误: {e}")
            print(f"start_game: 启动游戏时发生错误: {e}")
            self.update_game_path_ui()

    def _on_game_process_started(self):
        """
        处理 QProcess 成功启动游戏时的信号。
        """
        print("_on_game_process_started: 游戏进程已启动。")
        self.update_game_path_ui()  # 重新启用按钮

    def handle_game_finished(self):
        """处理游戏进程完成（退出）时的事件。"""
        print("handle_game_finished: 游戏进程已完成。")
        # 确保在游戏退出时（如果定时器仍在运行）停止检查定时器
        if self.game_check_timer.isActive():
            self.game_check_timer.stop()
            self.game_check_attempts = 0
        self.game_process = None  # 清除进程引用
        self.update_game_path_ui()  # 重新启用按钮

    def updateDynamicContent(self):
        """
        更新动态内容，例如季节信息标签。
        """
        print("updateDynamicContent: 更新动态内容（季节信息）。")
        self.version_status_label.setText(self.scraped_data["season"])
        # 在此处统一设置版本状态标签的字体大小和粗细
        self.version_status_label.setFont(QFont("sans-serif", 28, QFont.Bold))  # 使用 sans-serif 字体
        if not self.scraped_data["news_list"]:
            print("updateDynamicContent: 没有新闻数据，活动预览图像使用默认占位符。")
            self.small_preview_label.setText("活动预览")

    def populateNews(self):
        """
        填充新闻和活动列表。
        """
        print("populateNews: 填充新闻和活动列表。")
        self.activities_and_news_list.clear()

        if self.scraped_data["news_list"]:
            for news_item in self.scraped_data["news_list"]:
                title = news_item.get("title", "无标题")
                news_time = news_item.get("time", "")
                url = news_item.get("link_url")

                item_widget = QWidget()
                item_layout = QHBoxLayout(item_widget)
                item_layout.setContentsMargins(5, 5, 5, 5)
                item_layout.setSpacing(1)

                title_label = QLabel(title)
                # 统一设置标签字体
                title_label.setFont(QFont("sans-serif", 10))  # 使用 sans-serif 字体
                title_label.setStyleSheet("color: #cccccc;")
                title_label.setWordWrap(True)  # 自动换行
                title_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
                title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

                time_label = QLabel(news_time)
                time_label.setObjectName("time_label")
                # 统一设置标签字体
                time_label.setFont(QFont("sans-serif", 9))  # 使用 sans-serif 字体
                time_label.setStyleSheet("color: #999999;")
                time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                time_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

                item_layout.addWidget(title_label)
                item_layout.addWidget(time_label)

                item = QListWidgetItem(self.activities_and_news_list)
                item_widget.adjustSize()
                item.setSizeHint(item_widget.sizeHint())

                self.activities_and_news_list.addItem(item)
                self.activities_and_news_list.setItemWidget(item, item_widget)

                if url:
                    item.setData(Qt.UserRole, url)  # 在用户角色数据中存储 URL
                else:
                    item.setData(Qt.UserRole, "")
        else:
            item = QListWidgetItem("没有可用的新闻。请等待数据加载或检查脚本执行。")
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)  # 使占位符不可选择
            self.activities_and_news_list.addItem(item)
        print("populateNews: 新闻列表已填充。")

    def _set_preview_image(self, index):
        """
        设置新闻轮播的预览图像。
        """
        if not self.scraped_data["news_list"] or not (0 <= index < len(self.scraped_data["news_list"])):
            self.small_preview_label.setText("无图像或索引无效")
            return

        news_item = self.scraped_data["news_list"][index]
        image_path = news_item.get("image_local_path")

        if image_path and os.path.exists(image_path):
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(self.small_preview_label.size(),
                                              Qt.KeepAspectRatioByExpanding,
                                              Qt.SmoothTransformation)
                self.small_preview_label.setPixmap(scaled_pixmap)
                self.small_preview_label.setText("")  # 清除占位符文本
            else:
                self.small_preview_label.setText("图像加载失败")
        else:
            self.small_preview_label.setText("图像缺失")

    def start_news_carousel(self):
        """
        启动新闻轮播定时器。
        """
        if self.scraped_data["news_list"]:
            self.carousel_cycle_count = 0  # 启动或重新启动时重置循环计数
            self.current_news_index = 0  # 启动或重新启动时从第一条新闻开始

            self._set_preview_image(self.current_news_index)
            if self.news_carousel_timer.isActive():
                self.news_carousel_timer.stop()  # 如果已在运行则停止
            self.news_carousel_timer.start(5000)  # 每 5 秒切换一次
        else:
            self.small_preview_label.setText("活动预览")

    def handle_swipe(self, direction):
        """
        处理 SwipeableLabel 的滑动事件，手动更改新闻轮播图像。
        """
        if not self.scraped_data["news_list"]:
            return

        self.news_carousel_timer.stop()  # 停止自动轮播
        self.carousel_cycle_count = 0  # 手动滑动时重置循环计数

        num_news = len(self.scraped_data["news_list"])
        # 计算新索引，确保它在有效范围内
        self.current_news_index = (self.current_news_index + direction + num_news) % num_news

        self._set_preview_image(self.current_news_index)

        self.news_carousel_timer.start(5000)  # 重新启动自动轮播

    def openNewsLink(self, item):
        """
        打开点击的新闻项的链接。
        """
        news_url = item.data(Qt.UserRole)
        if news_url:
            QDesktopServices.openUrl(QUrl(news_url))
            print(f"openNewsLink: 打开链接: {news_url}")
        else:
            print(f"openNewsLink: 点击的列表项没有有效的链接。")

    def _set_background_image(self):
        """
        设置主窗口的背景图像。
        """
        print("_set_background_image: 设置主窗口背景图像。")
        background_image_path = self.scraped_data["background_image_local_path"]
        if background_image_path and os.path.exists(background_image_path):
            pixmap = QPixmap(background_image_path)
            if not pixmap.isNull():
                palette = self.palette()
                scaled_pixmap = pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                palette.setBrush(QPalette.Background, QBrush(scaled_pixmap))
                self.setPalette(palette)
                self.setAutoFillBackground(True)  # 启用自动背景填充
                print(f"_set_background_image: 背景图像已成功从本地路径加载并应用: {background_image_path}")
            else:
                print(f"_set_background_image: 错误：无法从 {background_image_path} 加载背景图像。")
        else:
            print(f"_set_background_image: 错误：未找到背景图像文件或路径无效: {background_image_path}，使用默认纯色背景。")
            palette = self.palette()
            palette.setColor(QPalette.Background, QColor("#1a1a2a"))  # 设置默认背景颜色
            self.setPalette(palette)

    def applyStyles(self):
        """
        应用全局和特定小部件样式，包括字体和背景。
        """
        print("applyStyles: 应用全局和特定小部件样式。")
        # 设置常用字体系列
        common_font_family = "'Segoe UI', 'Helvetica Neue', 'Arial', 'PingFang SC', 'Microsoft YaHei UI', sans-serif"

        self.setStyleSheet(f"""
            GameLauncher {{
                background-color: #1a1a2a;
                font-family: {common_font_family};
            }}
            QFrame, QWidget {{
                background: transparent;
                border: none;
            }}
            #contentFrame {{
                background-repeat: no-repeat;
                background-position: center;
                /* 移除了不支持的 background-size 属性 */
            }}

            /* 确保所有标签字体都继承或明确设置为 sans-serif */
            QLabel {{
                font-family: {common_font_family};
            }}
            QPushButton {{
                font-family: {common_font_family};
            }}
            QListWidget {{
                font-family: {common_font_family};
            }}
            QTabBar::tab {{
                font-family: {common_font_family};
            }}
            QMessageBox {{
                font-family: {common_font_family};
            }}
            QFileDialog {{
                font-family: {common_font_family};
            }}
        """)

        # 为特定标签设置字体大小和粗细，覆盖通用设置
        self.version_status_label.setFont(QFont("sans-serif", 28, QFont.Bold))
        self.game_status_label.setFont(QFont("sans-serif", 12))
        # 更新单个 locate_game_label 字体设置
        self.locate_game_label.setFont(QFont("sans-serif", 12, QFont.Bold))
        self.start_game_btn.setFont(QFont("sans-serif", 16, QFont.Bold))

        self._set_background_image()
        print("applyStyles: 样式已应用。")

    def resizeEvent(self, event):
        """
        处理窗口大小调整事件，重置背景和预览图像。
        """
        super().resizeEvent(event)
        print("resizeEvent: 窗口已调整大小，重置背景和预览图像。")
        self._set_background_image()
        self._set_preview_image(self.current_news_index)

    def closeEvent(self, event):
        """
        处理窗口关闭事件，终止所有后台进程。
        """
        print("closeEvent: 主应用程序正在关闭，正在终止后台进程...")
        self._terminate_process(self.htmlget_process, "htmlget.py")
        # 确保游戏进程检查定时器已停止
        if self.game_check_timer.isActive():
            self.game_check_timer.stop()
        self._terminate_process(self.game_process, "游戏")  # 终止实际游戏进程（如果存在）

        # 新增：尝试关闭核心程序
        if sys.platform == "win32" and self.core_program_path:
            core_exe_basename = os.path.basename(self.core_program_path)
            print(f"closeEvent: 尝试查找并关闭核心程序 '{core_exe_basename}' 的窗口。")
            # 重新查找所有核心程序窗口句柄
            current_core_program_handles = _find_window_handle_by_exe_name(core_exe_basename, max_attempts=1, delay_s=0)

            if current_core_program_handles:
                print(f"closeEvent: 找到 {len(current_core_program_handles)} 个核心程序窗口句柄，尝试发送 WM_CLOSE 消息。")
                for hwnd in current_core_program_handles:
                    try:
                        # 尝试发送 WM_CLOSE 消息，请求窗口优雅地关闭
                        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                        print(f"closeEvent: 已向句柄 {hwnd} 发送 WM_CLOSE 消息。")
                        # 额外等待一小段时间，让进程有机会响应 WM_CLOSE
                        time.sleep(0.1)
                        # 验证窗口是否已关闭
                        if not user32.IsWindow(hwnd):
                            print(f"closeEvent: 句柄 {hwnd} 对应的窗口已关闭。")
                        else:
                            print(f"closeEvent: 警告：句柄 {hwnd} 对应的窗口在发送 WM_CLOSE 后未关闭，尝试终止进程。")
                            # 如果 WM_CLOSE 未关闭窗口，尝试获取 PID 并终止进程
                            process_id = ctypes.c_ulong()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                            if process_id.value != 0:
                                p_handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, process_id.value)
                                if p_handle:
                                    kernel32.TerminateProcess(p_handle, 0) # 0 表示正常退出代码
                                    kernel32.CloseHandle(p_handle)
                                    print(f"closeEvent: 已终止进程 ID {process_id.value}。")
                                else:
                                    print(f"closeEvent: 错误：无法打开进程 ID {process_id.value} 的句柄以终止。")
                            else:
                                print(f"closeEvent: 错误：无法获取句柄 {hwnd} 的进程 ID。")

                    except Exception as e:
                        print(f"closeEvent: 关闭核心程序窗口 {hwnd} 时出错：{e}")
            else:
                print("closeEvent: 未找到核心程序窗口，无需关闭。")
        else:
            print("closeEvent: 非 Windows 平台或核心程序路径未设置，跳过关闭核心程序。")


        # 释放单例锁
        release_single_instance_lock()  # 释放应用程序单例锁
        print("closeEvent: 单例锁已释放。")
        super().closeEvent(event)


# --- 单例模式实现 ---
_app_lock_file = None
LOCK_FILE_NAME = "gamelaucher_single_instance.lock"  # 应用程序的唯一锁文件名


def acquire_single_instance_lock():
    """
    尝试获取应用程序的单例锁。
    如果成功获取锁（表示这是第一个实例），则返回 False。
    如果无法获取锁（因为另一个实例已在运行），则返回 True。
    此功能仅适用于 Windows 平台。
    """
    global _app_lock_file
    print("acquire_single_instance_lock: 尝试获取单例锁。")
    if sys.platform != "win32":
        print("acquire_single_instance_lock: 非 Windows 平台，跳过单例锁检测。")
        return False

    lock_file_path = os.path.join(tempfile.gettempdir(), LOCK_FILE_NAME)
    try:
        # 尝试以独占写入模式打开文件，并立即锁定它
        # buffering=0 禁用缓冲，确保立即写入。模式 'wb' 解决 'can't have unbuffered text I/O' 错误
        _app_lock_file = open(lock_file_path, 'wb', buffering=0)
        msvcrt.locking(_app_lock_file.fileno(), msvcrt.LK_NBLCK, 1)  # LK_NBLCK (非阻塞)
        print(f"acquire_single_instance_lock: 成功获取单例锁: {lock_file_path}")
        return False  # 成功获取锁，这是第一个实例
    except IOError:
        print(f"acquire_single_instance_lock: 获取单例锁失败，可能另一个实例正在运行: {lock_file_path}")
        return True  # 无法获取锁，另一个实例正在运行
    except Exception as e:
        print(f"acquire_single_instance_lock: 获取单例锁时出错: {e}")
        return True  # 发生错误，视为已在运行以防止多个实例


def release_single_instance_lock():
    """
    释放应用程序的单例锁。
    此功能仅适用于 Windows 平台。
    """
    global _app_lock_file
    print("release_single_instance_lock: 尝试释放单例锁。")
    if _app_lock_file:
        try:
            msvcrt.locking(_app_lock_file.fileno(), msvcrt.LK_UNLCK, 1)  # LK_UNLCK (解锁)
            _app_lock_file.close()
            print("release_single_instance_lock: 单例锁已释放。")
        except Exception as e:
            print(f"release_single_instance_lock: 释放单例锁时出错: {e}")
        _app_lock_file = None


def excepthook(exc_type, exc_value, exc_traceback):
    """
    全局异常钩子，用于捕获未处理的异常。
    """
    import traceback
    error_message = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print(f"excepthook 捕获到未处理的异常:\n{error_message}")
    # 您可以将错误写入日志文件，或显示 QMessageBox
    QMessageBox.critical(None, "应用程序错误", f"发生未处理的错误:\n{error_message}\n\n应用程序将退出。")
    # 确保在退出前释放锁，以防应用程序在初始化期间崩溃
    release_single_instance_lock()
    sys.exit(1)


if __name__ == '__main__':
    # 设置全局异常钩子
    sys.excepthook = excepthook

    # 解决 PyInstaller 捆绑在 Windows 上时的 multiprocessing 模块问题
    # 必须放在 if __name__ == '__main__': 之后，以确保在所有进程入口点都被调用
    multiprocessing.freeze_support()
    print("__main__: 启动 QApplication。")

    # 尝试设置高 DPI 缩放，以便在高分辨率屏幕上获得更清晰的 UI
    # 注意：这些属性必须在 QCoreApplication（QApplication 的基类）实例创建之前设置。
    try:
        if hasattr(Qt, 'AA_EnableHighDpiScaling'):
            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
            print("__main__: 高 DPI 缩放已启用。")
        if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
            QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
            print("__main__: 高 DPI 像素图已启用。")
        # 对于 Windows 10+，确保应用程序是 DPI 感知的
        myappid = 'mycompany.myproduct.gamelaucher.1_0'  # 任意字符串
        if sys.platform == "win32":  # 仅适用于 Windows
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            print(f"__main__: AppUserModelID 已设置为 {myappid}。")
    except AttributeError as e:
        print(f"__main__: DPI 设置属性错误: {e}。跳过 DPI 设置。")
    except Exception as e:
        print(f"__main__: DPI 设置或 AppUserModelID 期间发生一般错误: {e}")

    app = QApplication(sys.argv)  # 在 DPI 设置后创建 QApplication 实例

    print("__main__: 检查单例。")
    # --- 检查是否已有实例正在运行 ---
    if acquire_single_instance_lock():
        # 如果已有实例正在运行，则显示消息并退出。
        # 由于 QApplication 已初始化，我们可以直接使用 QMessageBox。
        # QMessageBox.warning(None, "启动器已在运行", "游戏启动器已在运行。请检查您的任务栏或系统托盘。")
        print("__main__: 另一个实例正在运行。退出。")
        sys.exit(0)  # 正常退出

    print("__main__: 创建 GameLauncher 实例。")
    # 应用程序主窗口的正常启动
    launcher = GameLauncher()
    print("__main__: 显示 GameLauncher 窗口。")
    launcher.show()
    print("__main__: 启动应用程序事件循环。")
    sys.exit(app.exec_())
