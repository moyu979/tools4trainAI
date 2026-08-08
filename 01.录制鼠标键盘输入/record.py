#!/usr/bin/env python3
"""
统一输入记录器 - 同时录制键盘 (K)、鼠标 (M) 和手柄 (G) 输入。

功能：
- 键盘：记录所有按键的按下与释放（组合键按裸格式记录：ctrl 与字母分开，
  如 Ctrl+A 记作 ctrl、a 各自的 PRESS/RELEASE），支持自动连发过滤；
  日志含 key=<名字> 与 vk=<物理键码> 双字段（vk 同一平台内唯一）
- 鼠标：记录左/右/中键、侧键（x1/x2）点击、移动（时间+位移双重降噪过滤）
  和滚轮滚动
- 手柄：记录按钮（含 L3/R3 摇杆按下）、方向键（D-Pad）、摇杆轴；左/右摇杆
  X/Y 合并为一行输出（stick=L x=.. y=..），扳机保持单轴（axis=L_Trigger val=..）；
  轴事件含死区过滤、最短记录间隔限频、回正强制归零
- 支持热插拔与"后连"：启动时无手柄不退出线程，靠 JOYDEVICEADDED 事件 +
  每 1 秒兜底扫描发现后插入的手柄
- 统一输出到运行目录下 data/ 子目录中，以启动时间命名的 .txt 文件；
  文件头为 # 开头的会话信息（平台/CPU/GPU/依赖版本/降噪配置）
- 日志行前缀：K=键盘, M=鼠标, G[品牌]=手柄；Ctrl+C 停止所有录制

目标平台：Windows / macOS / Linux（手柄在 Linux 上暂不支持）。

TODO（供 Todo Tree 识别）：
- Windows 上待测试更多手柄型号（PS4 / PS5 等）的布局映射
- macOS / Linux 上待测试全部功能（键盘、鼠标、手柄）

依赖：
- pynput (键盘/鼠标)
- pygame (手柄，可选：未安装时仅禁用手柄录制)
"""
# ===========================================================================
# TODO: Windows 上待测试更多手柄型号（PS4 / PS5 等）的布局映射
# TODO: macOS / Linux 上待测试全部功能（键盘、鼠标、手柄）
# ===========================================================================

import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# pynput — 键盘 & 鼠标
# ---------------------------------------------------------------------------
try:
    from pynput import keyboard as pynput_keyboard
    from pynput import mouse as pynput_mouse
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False

# ---------------------------------------------------------------------------
# pygame — 手柄
# ---------------------------------------------------------------------------
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False


# ---------------------------------------------------------------------------
# 可调常量（按需修改，类似 C++ #define）
# ---------------------------------------------------------------------------

# 日志
LOG_FILE_PREFIX = "input"               # 日志文件名前缀
LOG_DIR = "data"                        # 日志输出目录（运行目录下的 data 子目录）

# 键盘降噪
IGNORE_KEY_AUTO_REPEAT = True           # 忽略按键自动连发

# 鼠标降噪
MOVE_MIN_INTERVAL_MS = 50               # 鼠标移动最小记录间隔（毫秒）
MOVE_MIN_DISTANCE = 5                   # 鼠标移动最小位移（像素）

# 手柄降噪
DEADZONE = 0.05                         # 摇杆死区（0.0 ~ 1.0）
MIN_DELTA = 0.01                        # 轴数值最小变化阈值
POLL_INTERVAL = 0.01                    # 事件轮询间隔（秒）
AXIS_MIN_INTERVAL_MS = 20               # 摇杆最短记录间隔（毫秒，类似鼠标 MOVE_MIN_INTERVAL_MS）
AXIS_BIG_DELTA = 0.3                    # 摇杆大幅变化阈值：超过则不受最短间隔限制（快速推杆不丢帧）

# ---------------------------------------------------------------------------
# 平台预定义手柄映射表
# ---------------------------------------------------------------------------
CONTROLLER_MAPPINGS_MACOS = {
    "Xbox": {
        "buttons": {0: "A", 1: "B", 2: "X", 3: "Y", 4: "View", 5: "Xbox",
                    6: "Menu", 9: "LB", 10: "RB", 11: "D_Up", 12: "D_Down",
                    13: "D_Left", 14: "D_Right"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y",
                 4: "L_Trigger", 5: "R_Trigger"}
    },
    "DualSense": {
        "buttons": {0: "Cross", 1: "Circle", 2: "Square", 3: "Triangle",
                    4: "Share", 5: "PS", 6: "Options", 7: "L3", 8: "R3",
                    9: "L1", 10: "R1", 11: "Up", 12: "Down", 13: "Left",
                    14: "Right", 15: "Touchpad"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y",
                 4: "L2", 5: "R2"}
    },
    "PS4": {
        "buttons": {0: "Cross", 1: "Circle", 2: "Square", 3: "Triangle",
                    4: "Share", 5: "PS", 6: "Options", 7: "L3", 8: "R3",
                    9: "L1", 10: "R1"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y",
                 4: "L2", 5: "R2"}
    }
}

CONTROLLER_MAPPINGS_WINDOWS = {
    "Xbox": {
        "buttons": {0: "A", 1: "B", 2: "X", 3: "Y", 4: "LB", 5: "RB",
                    6: "View", 7: "Menu", 8: "L3", 9: "R3", 10: "Xbox",
                    11: "D_Up", 12: "D_Down", 13: "D_Left", 14: "D_Right"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y",
                 4: "L_Trigger", 5: "R_Trigger"}
    },
    "DualSense": {
        "buttons": {0: "Cross", 1: "Circle", 2: "Square", 3: "Triangle",
                    4: "Share", 5: "PS", 6: "Options", 7: "L3", 8: "R3",
                    9: "L1", 10: "R1", 11: "Up", 12: "Down", 13: "Left",
                    14: "Right", 15: "Touchpad"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y",
                 4: "L2", 5: "R2"}
    },
    "PS4": {
        "buttons": {0: "Cross", 1: "Circle", 2: "Square", 3: "Triangle",
                    4: "Share", 5: "PS", 6: "Options", 7: "L3", 8: "R3",
                    9: "L1", 10: "R1"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y",
                 4: "L2", 5: "R2"}
    }
}


# ---------------------------------------------------------------------------
# 小键盘虚拟键码 → 名字（仅 Windows 平台使用）
# pynput 对数字小键盘的键 char 为 None、name 也不存在，只暴露 vk（96~105=数字、
# 106~111=运算符），若不做映射，日志会出现 <96> 这样的原始对象字符串。
# 注意：这些是 Windows VK 码，只应在 win32 下查表；macOS/Linux 的 vk 数值不同，
# 直接用此表会把普通键误命名为 numpadX，所以调用处加了 sys.platform 判断。
# ---------------------------------------------------------------------------
NUMPAD_VK_NAMES_WINDOWS = {
    96: "numpad0", 97: "numpad1", 98: "numpad2", 99: "numpad3",
    100: "numpad4", 101: "numpad5", 102: "numpad6", 103: "numpad7",
    104: "numpad8", 105: "numpad9",
    106: "numpad_multiply",  # *
    107: "numpad_add",       # +
    108: "numpad_separator",
    109: "numpad_subtract",  # -
    110: "numpad_decimal",   # .
    111: "numpad_divide",    # /
    144: "numlock",
}


def now_iso() -> str:
    """获取当前时间的 ISO 格式字符串。

    Returns:
        str: 当前时间的 ISO 8601 格式字符串。
    """
    return datetime.now().isoformat()


def _get_cpu_model() -> str:
    """获取 CPU 型号（跨平台）。

    优先用 platform.processor()；Linux 上为空时回退读 /proc/cpuinfo。

    Returns:
        str: CPU 型号字符串；获取失败返回 '未知'。
    """
    try:
        import platform
        cpu = platform.processor()
        if cpu:
            return cpu
    except Exception:
        pass
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "未知"


def _get_gpu_model() -> str:
    """获取显卡型号（跨平台）。

    Windows 用 PowerShell 查 Win32_VideoController；macOS 用
    system_profiler；Linux 用 lspci。多显卡用 ' / ' 连接。

    Returns:
        str: 显卡型号字符串；获取失败返回 '未知'。
    """
    import subprocess
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController).Name"],
                capture_output=True, text=True, timeout=10)
            names = [l.strip() for l in out.stdout.splitlines() if l.strip()]
            if names:
                return " / ".join(names)
        elif sys.platform == "darwin":
            out = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=10)
            for line in out.stdout.splitlines():
                if "Chipset Model" in line:
                    return line.split(":", 1)[1].strip()
        else:  # Linux
            out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
            for line in out.stdout.splitlines():
                low = line.lower()
                if "vga" in low or "3d controller" in low or "display controller" in low:
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "未知"


class UnifiedInputRecorder:
    """统一输入记录器 — 同时录制键盘 (K)、鼠标 (M) 和手柄 (G)。

    对外主要接口：
        start(): 启动录制（阻塞，Ctrl+C 停止）。
        stop():  停止录制并释放资源。

    日志输出到 CWD 下 data/input_<时间戳>.txt，文件头为会话信息（# 开头），
    事件行格式：[ISO时间] 设备 动作 详情。
    """

    def __init__(self) -> None:
        """初始化统一输入记录器。

        检查可用设备（键盘/鼠标/手柄），创建日志文件，
        配置各设备的降噪参数和状态存储。
        """
        start_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 默认输出到运行目录（CWD）下的 data 子目录，如 ./data/input_....txt
        self.log_filename = str(Path(LOG_DIR) / f"{LOG_FILE_PREFIX}_{start_str}.txt")
        self._log_file = None  # start() 时打开，stop() 时关闭
        self._log_lock = threading.Lock()  # 保护多线程写入日志文件
        self.running = False

        # ---- 设备可用状态（仅依赖库是否存在，不扫描物理设备） ----
        self.has_device = {"K": HAS_PYNPUT, "M": HAS_PYNPUT, "G": HAS_PYGAME}

        # ---- 监听器（统一 dict，K/M 为 pynput Listener，G 为 daemon 线程） ----
        self.listeners: dict[str, threading.Thread | pynput_keyboard.Listener | pynput_mouse.Listener | None] = {
            "K": None, "M": None, "G": None
        }

        # ---- 键盘降噪 ----
        self._pressed_keys: set[str] = set()

        # ---- 鼠标降噪 ----
        self._last_move_ts_ms: int = 0
        self._last_move_pos: tuple[int, int] | None = None

        # ---- 手柄配置（pygame 没有原生 Listener，用 daemon 线程模拟） ----
        self.mappings: dict = {}
        self.mapping_names: dict = {}  # 手柄 ID → 品牌名（如 "Xbox"、"DualSense"）
        self.last_axes: dict = {}
        self.last_hats: dict = {}
        # 摇杆"组"定义：手柄 id → {组名: {...}}，用于把摇杆 X/Y 合并成一行输出 +
        # 记录组级"最短记录间隔"限频时间戳（见 _build_stick_groups）。
        self.stick_groups: dict = {}
        # 手柄 ID → Joystick 对象。必须保持打开，SDL 只对"打开"的手柄轮询并生成事件，
        # 关闭（quit）后该设备的 JOY* 事件将不再产生，导致无法录制。
        self.joysticks: dict = {}

    # ------------------------------------------------------------------
    # 日志写入
    # ------------------------------------------------------------------

    def _write_line(self, line: str) -> None:
        """向已打开的日志文件写入一行内容（线程安全）。

        键盘/鼠标/手柄三个线程都会调用本方法，使用互斥锁保证
        单行写入不交错；stop() 关闭文件后的并发写入会被捕获忽略。

        Args:
            line: 要写入日志文件的单行文本（不含换行符）。
        """
        with self._log_lock:
            if self._log_file is None:
                return
            try:
                self._log_file.write(line + "\n")
                self._log_file.flush()
            except (ValueError, OSError):
                # 文件已关闭等竞态情况，忽略即可
                pass

    def _log(self, device: str, action: str, detail: str, to_file: bool = True) -> None:
        """记录一条格式化输入事件日志。

        日志格式: [ISO时间] 设备前缀 动作 详情
        设备前缀: K=键盘, M=鼠标, G=手柄

        Args:
            device: 设备前缀，'K'、'M' 或 'G'。
            action: 动作类型，如 'PRESS'、'RELEASE'、'MOVE'、'BUTTON_DOWN' 等。
            detail: 事件详情字符串。
            to_file: 是否写入日志文件。初始化信息等辅助日志设为 False，仅输出到控制台。
        """
        line = f"[{now_iso()}] {device} {action} {detail}"
        print(line)
        if to_file:
            self._write_line(line)

    def _write_header(self) -> str:
        """写入会话/系统信息头并返回其文本（便于同时打印到控制台）。

        header 用 `#` 开头，与事件日志（以 [ 时间戳 开头）区分，
        下游解析时跳过 # 行即可。

        Returns:
            str: 写入文件的 header 文本。
        """
        try:
            import platform
            pyver = platform.python_version()
            osinfo = platform.platform()
        except Exception:
            pyver = sys.version.split()[0]
            osinfo = sys.platform
        cpu = _get_cpu_model()
        gpu = _get_gpu_model()
        try:
            from importlib import metadata as _metadata
            pynput_ver = _metadata.version("pynput")
        except Exception:
            pynput_ver = "?"
        pygame_ver = pygame.version.ver if HAS_PYGAME else "未安装"

        labels = {"K": "键盘", "M": "鼠标", "G": "手柄"}
        dev_str = " ".join(
            f"{labels[d]}={'可用' if self.has_device[d] else '不可用'}" for d in ("K", "M", "G")
        )
        cfg = (
            f"IGNORE_KEY_AUTO_REPEAT={IGNORE_KEY_AUTO_REPEAT} "
            f"MOVE_MIN_INTERVAL_MS={MOVE_MIN_INTERVAL_MS} "
            f"MOVE_MIN_DISTANCE={MOVE_MIN_DISTANCE} "
            f"DEADZONE={DEADZONE} MIN_DELTA={MIN_DELTA} POLL_INTERVAL={POLL_INTERVAL} "
            f"AXIS_MIN_INTERVAL_MS={AXIS_MIN_INTERVAL_MS} AXIS_BIG_DELTA={AXIS_BIG_DELTA}"
        )
        lines = [
            "# ================================================================",
            "# tools4trainAI 输入录制 - 会话信息",
            f"# 创建时间: {now_iso()}",
            f"# 平台: {sys.platform} / {osinfo}",
            f"# CPU: {cpu}",
            f"# GPU: {gpu}",
            f"# Python: {pyver}",
            f"# pynput: {pynput_ver}",
            f"# pygame: {pygame_ver}",
            f"# 设备: {dev_str}",
            f"# 配置: {cfg}",
            "# 注: 以 # 开头的行是会话信息；事件日志从首个 [ 时间戳 行开始",
            "# ================================================================",
            "",
        ]
        header_text = "\n".join(lines)
        with self._log_lock:
            if self._log_file is not None:
                try:
                    self._log_file.write(header_text)
                    self._log_file.flush()
                except (ValueError, OSError):
                    pass
        return header_text

    # ==================================================================
    # 键盘事件 (pynput)
    # ==================================================================

    def on_key_press(self, key):
        """键盘按键按下事件回调。

        采用"裸格式"记录：Ctrl 键本身由 pynput 作为普通键单独上报
        （如 ctrl / ctrl_l / ctrl_r），Ctrl+字母产生的控制字符则还原为
        字母记录。例如 Ctrl+A 最终记录 ctrl、a 各自的 PRESS / RELEASE
        共四个事件，而不是合成一条 ctrl+a。支持自动连发忽略；
        Ctrl+C 触发停止录制。

        Args:
            key: pynput 键盘事件对象。

        Returns:
            bool | None: 返回 False 可停止监听器。
        """
        letter = self._try_parse_control_combo(key)
        # 统一键名：Ctrl+字母 用还原后的字母，其余走普通键名（与 on_key_release 一致）
        key_name = letter if letter is not None else self._key_to_string(key)

        if IGNORE_KEY_AUTO_REPEAT:
            if key_name in self._pressed_keys:
                return
            self._pressed_keys.add(key_name)

        self._log("K", "PRESS", self._key_detail(key_name, key))

        # Ctrl+C 停止（跨平台）：
        #  - Windows：Ctrl+C 上报控制字符 \x03（_try_parse_control_combo 还原成 'c'）
        #  - macOS/Linux：Ctrl+C 上报普通字符 'c'，需检查 Ctrl 是否处于按下状态
        if letter == "c" or (self._is_ctrl_active() and key_name.lower() == "c"):
            self.stop()
            return False

    def on_key_release(self, key) -> None:
        """键盘按键释放事件回调。

        与 on_key_press 一致：控制字符还原为字母记录（如 RELEASE key=a），
        并从 _pressed_keys 中移除，避免状态泄漏导致后续同组合键
        被误判为自动连发。

        Args:
            key: pynput 键盘事件对象。
        """
        letter = self._try_parse_control_combo(key)
        key_name = letter if letter is not None else self._key_to_string(key)
        self._log("K", "RELEASE", self._key_detail(key_name, key))
        self._pressed_keys.discard(key_name)

    def _key_detail(self, key_name: str, key) -> str:
        """构造键盘日志 detail：key=<名字>，附带 vk 物理键码（pynput 提供时）。

        vk 是各平台的物理键码（Windows VK / macOS 虚拟键码 / Linux keysym），
        同一平台内唯一标识物理键，不受大小写、Shift 修饰或键盘布局影响；
        平台间数值不通用，但读取逻辑一致（getattr 兜底，缺失时省略）。

        Args:
            key_name: 可读键名（来自 _key_to_string / 控制字符还原）。
            key: pynput 键盘事件对象。

        Returns:
            str: 日志 detail 字符串。
        """
        detail = f"key={key_name}"
        vk = getattr(key, "vk", None)
        if vk is not None:
            detail += f" vk={vk}"
        return detail

    @staticmethod
    def _key_to_string(key) -> str:
        """将 pynput 按键对象转换为可读字符串。

        小键盘键（如 Numpad0）的 char 为 None、name 不存在，只有 vk 虚拟键码，
        先按 vk 映射成可读名字（numpad0/numpad_decimal 等），再走普通 char/name 路径。
        注意：NUMPAD_VK_NAMES_WINDOWS 是 Windows VK 码，只在 win32 平台查表；
        否则 macOS/Linux 上普通键的 vk 落在 96~111 会被误命名为 numpadX。

        Args:
            key: pynput 键盘事件中的键对象。

        Returns:
            str: 按键的字符串表示。
        """
        vk = getattr(key, "vk", None)
        if vk is not None and sys.platform == "win32":
            numpad_name = NUMPAD_VK_NAMES_WINDOWS.get(vk)
            if numpad_name:
                return numpad_name
        try:
            if hasattr(key, "char") and key.char is not None:
                return key.char
        except Exception:
            pass
        try:
            return key.name
        except Exception:
            return str(key)

    def _is_ctrl_active(self) -> bool:
        """检查当前是否有任意 Ctrl 键处于按下状态。

        跨平台需要：macOS/Linux 上 Ctrl+C 上报的是普通字符 'c' 而非控制字符
        \\x03，必须结合 Ctrl 按下状态才能判定 Ctrl+C。

        Returns:
            bool: 任意 Ctrl 键被按下返回 True。
        """
        ctrl_names = {"ctrl", "ctrl_l", "ctrl_r", "left_ctrl", "right_ctrl"}
        return any(name in self._pressed_keys for name in ctrl_names)

    @staticmethod
    def _is_control_char(ch: str) -> bool:
        """判断字符是否为 ASCII 控制字符（\\x01 ~ \\x1a）。

        Args:
            ch: 待判断字符。

        Returns:
            bool: 是控制字符返回 True。
        """
        return len(ch) == 1 and 1 <= ord(ch) <= 26

    @staticmethod
    def _control_char_to_letter(ch: str) -> str:
        """将 ASCII 控制字符转换为对应字母。

        \\x01 → 'a', \\x02 → 'b', ..., \\x1a → 'z'

        Args:
            ch: ASCII 控制字符。

        Returns:
            str: 对应小写字母。
        """
        return chr(ord('a') + (ord(ch) - 1))

    def _try_parse_control_combo(self, key):
        """尝试解析控制字符（如 Ctrl+C 产生的 \\x03）为对应字母。

        Windows 下按住 Ctrl 再按字母键时，pynput 上报的字符是 ASCII
        控制字符（\\x01 ~ \\x1a），例如 Ctrl+A → '\\x01'、Ctrl+C → '\\x03'。
        这里将其还原为对应的小写字母，以便按"裸格式"记录——
        即 ctrl 键与字母键分别记录各自的 PRESS / RELEASE。

        Args:
            key: pynput 键盘事件对象。

        Returns:
            str | None: 还原后的小写字母（如 'a'、'c'），非控制字符返回 None。
        """
        try:
            if hasattr(key, "char") and key.char is not None:
                ch = key.char
                if self._is_control_char(ch):
                    return self._control_char_to_letter(ch)
        except Exception:
            pass
        return None

    # ==================================================================
    # 鼠标事件 (pynput)
    # ==================================================================

    def on_mouse_click(self, x: int, y: int, button, pressed: bool) -> None:
        """鼠标按键点击事件回调。

        记录左键、右键、中键以及侧键（x1/x2）的按下/释放。

        Args:
            x: 点击时鼠标 X 坐标。
            y: 点击时鼠标 Y 坐标。
            button: pynput 鼠标按钮对象。
            pressed: True 为按下，False 为释放。
        """
        btn_name = self._button_to_name(button)
        if btn_name is None:
            return
        action = "PRESS" if pressed else "RELEASE"
        self._log("M", action, f"button={btn_name}")

    def on_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        """鼠标滚轮滚动事件回调。

        Args:
            x: 滚动时鼠标 X 坐标。
            y: 滚动时鼠标 Y 坐标。
            dx: 水平滚动量（正=右，负=左）。
            dy: 垂直滚动量（正=上，负=下）。
        """
        self._log("M", "SCROLL", f"x={x} y={y} dx={dx} dy={dy}")

    def on_mouse_move(self, x: int, y: int) -> None:
        """鼠标移动事件回调（含降噪过滤）。

        曼哈顿距离和最小时间间隔双重过滤，减少冗余日志。

        Args:
            x: 当前鼠标 X 坐标。
            y: 当前鼠标 Y 坐标。
        """
        now_ms = self._now_ms()
        last_pos = self._last_move_pos

        if last_pos is None:
            should_log = True
        else:
            dt_ok = (now_ms - self._last_move_ts_ms) >= MOVE_MIN_INTERVAL_MS
            dist_ok = self._manhattan_distance(last_pos, (x, y)) >= MOVE_MIN_DISTANCE
            should_log = dt_ok or dist_ok

        if should_log:
            self._log("M", "MOVE", f"x={x} y={y}")
            self._last_move_ts_ms = now_ms
            self._last_move_pos = (x, y)

    @staticmethod
    def _button_to_name(button) -> str | None:
        """将 pynput 鼠标按钮转换为字符串标识。

        支持左/右/中键以及侧键 x1（后退）/x2（前进）。

        Args:
            button: pynput 鼠标按钮对象。

        Returns:
            str | None: 'left'、'right'、'middle'、'x1'、'x2' 或 None。
        """
        if button == pynput_mouse.Button.left:
            return "left"
        if button == pynput_mouse.Button.right:
            return "right"
        if button == pynput_mouse.Button.middle:
            return "middle"
        if hasattr(pynput_mouse.Button, "x1") and button == pynput_mouse.Button.x1:
            return "x1"
        if hasattr(pynput_mouse.Button, "x2") and button == pynput_mouse.Button.x2:
            return "x2"
        return None

    @staticmethod
    def _manhattan_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
        """计算两点的曼哈顿距离。

        Args:
            a: 第一个点 (x, y)。
            b: 第二个点 (x, y)。

        Returns:
            int: 曼哈顿距离。
        """
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    @staticmethod
    def _now_ms() -> int:
        """获取当前 Unix 毫秒时间戳。

        Returns:
            int: 毫秒级时间戳。
        """
        return int(datetime.now().timestamp() * 1000)

    # ==================================================================
    # 手柄事件 (pygame 轮询)
    # ==================================================================

    def _set_axis_last(self, jid: int, axis: int, val: float) -> None:
        """更新某手柄某轴的最新值缓存（无条件更新）。

        缓存始终保存"该轴最近一次事件的值"，供摇杆合并输出（配对轴
        读取最新姿态）和变化量计算使用。列表按需扩容，缺失轴按 0.0。

        Args:
            jid: 手柄 id。
            axis: 轴索引。
            val: 最新轴值（已过死区处理）。
        """
        arr = self.last_axes.get(jid)
        if arr is None:
            arr = [0.0] * (axis + 1)
            self.last_axes[jid] = arr
        elif len(arr) <= axis:
            arr.extend([0.0] * (axis + 1 - len(arr)))
        arr[axis] = val

    def _get_axis_last(self, jid: int, axis: int) -> float:
        """读取某手柄某轴的最新值缓存（缺失按 0.0）。

        Args:
            jid: 手柄 id。
            axis: 轴索引。

        Returns:
            float: 该轴最近一次事件的值；未记录过返回 0.0。
        """
        arr = self.last_axes.get(jid)
        if not arr or axis >= len(arr):
            return 0.0
        return arr[axis]

    @staticmethod
    def _get_controller_mapping(joy_name: str) -> tuple[str | None, dict | None]:
        """根据手柄名称模糊匹配映射表。

        Args:
            joy_name: 手柄设备名称。

        Returns:
            tuple[str | None, dict | None]: (品牌名, 映射表)，
            例如 ("Xbox", {buttons: ..., axes: ...})，未匹配返回 (None, None)。

        Raises:
            NotImplementedError: Linux 平台暂不支持。
        """
        if sys.platform == "darwin":
            mappings = CONTROLLER_MAPPINGS_MACOS
        elif sys.platform == "win32":
            mappings = CONTROLLER_MAPPINGS_WINDOWS
            # Windows 下 XInput 协议手柄（设备名常含 "XINPUT"，如北通手柄）
            # 布局与 Xbox 完全一致，即使设备名不含 "xbox" 也按 Xbox 映射。
            if "xinput" in joy_name.lower():
                return "Xbox", mappings["Xbox"]
        else:
            raise NotImplementedError("Linux 平台手柄捕捉逻辑暂未实现")

        for key in mappings:
            if key.lower() in joy_name.lower():
                return key, mappings[key]
        return None, None

    @staticmethod
    def _build_stick_groups(axes_map: dict | None) -> dict:
        """根据轴映射表把连续轴分成"记录组"（用于合并输出 + 限频）。

        规则（基于 Xbox/DualSense/PS4 的轴命名约定）：
          - 含 'Stick' 的轴：按轴名前缀（'L'/'R'）把 X/Y 配对成摇杆组，
            记录时合并为一行 `stick=L x=.. y=..`；X/Y 由轴名后缀决定。
          - 含 'Trigger' 的轴：每个扳机自成单轴组，记录 `axis=L_Trigger val=..`。
          - 其他轴名或 axes_map 为 None（未知布局）：不分组，
            该手柄保持"每轴独立记录"的原逻辑。

        Args:
            axes_map: 轴映射表 {轴索引: 轴名}，未知布局为 None。

        Returns:
            dict: {组名: 组信息 dict}，例如
            {"L": {"type": "stick", "name": "L", "x_axis": 0, "y_axis": 1, "ts": 0}, ...}
            其中 ts 为该组上次记录的时间戳（毫秒），用于最短记录间隔限频。
        """
        groups: dict = {}
        if not axes_map:
            return groups
        label_of = {label: axis for axis, label in axes_map.items()}
        # 1) 摇杆配对：同前缀的 Stick 轴合为一组
        by_prefix: dict[str, dict[str, int]] = {}
        for label, axis in label_of.items():
            if "Stick" in label:
                by_prefix.setdefault(label.split("_")[0], {})[label] = axis
        for prefix, members in by_prefix.items():
            x_axis = members.get(f"{prefix}_Stick_X")
            y_axis = members.get(f"{prefix}_Stick_Y")
            if x_axis is not None and y_axis is not None:
                groups[prefix] = {
                    "type": "stick", "name": prefix,
                    "x_axis": x_axis, "y_axis": y_axis, "ts": 0,
                }
        # 2) 扳机单独成组（单轴输出）
        for label, axis in label_of.items():
            if "Trigger" in label:
                groups[label] = {"type": "axis", "name": label, "axis": axis, "ts": 0}
        return groups

    def _init_joysticks(self) -> bool:
        """重新扫描并初始化所有已连接的手柄。

        保持每个 Joystick 对象打开并持有引用（self.joysticks）。
        关键：SDL 只对"打开"的手柄轮询状态并生成 JOY* 事件，
        若像早期版本那样拿到设备名后就 quit() 关闭对象，
        该手柄将不再产生任何事件，导致无法录制。

        Returns:
            bool: 当前至少有一个有效手柄返回 True。
        """
        # 关闭并清除上一轮持有的手柄对象
        for old in self.joysticks.values():
            try:
                old.quit()
            except Exception:
                pass
        self.joysticks.clear()

        self.mappings.clear()
        self.mapping_names.clear()
        self.last_hats.clear()
        self.last_axes.clear()
        self.stick_groups.clear()

        if not pygame.joystick.get_init():
            pygame.joystick.init()

        pygame.event.pump()
        time.sleep(0.1)
        count = pygame.joystick.get_count()

        # 不再阻塞等待手柄插入（原逻辑最多等 5 秒，会卡住事件循环）。
        # "启动时无手柄、之后后插"的场景由 _gamepad_loop 的
        # JOYDEVICEADDED 事件 + 定期兜底扫描负责，这里只做快速扫描。
        if count == 0:
            return False

        for i in range(count):
            try:
                joy = pygame.joystick.Joystick(i)
                joy.init()
                name = joy.get_name()
                matched_key, mapping = self._get_controller_mapping(name)
                self.mappings[i] = mapping
                self.mapping_names[i] = matched_key or "Unknown"
                num_axes = joy.get_numaxes()
                num_buttons = joy.get_numbuttons()
                # 初始化 last_axes 用于轴变化率过滤（通过 JOYAXISMOTION 事件更新）
                self.last_axes[i] = [0.0] * num_axes
                self.last_hats[i] = (0, 0)
                # 根据轴名构建摇杆"组"（X/Y 合并 + 限频）；未知布局返回空 dict
                # 注意：_build_stick_groups 需要的是轴映射表 mapping['axes']
                # （{轴索引: 轴名}），传整个 mapping 会把 buttons/axes 两个
                # dict 当 key，触发 TypeError: unhashable type: 'dict'。
                axes_map = mapping['axes'] if mapping else None
                self.stick_groups[i] = self._build_stick_groups(axes_map)
                if mapping:
                    self._log(f"G[{matched_key}]", "INFO",
                              f"init id={i} device={name} layout={matched_key} "
                              f"buttons={num_buttons} axes={num_axes}",
                              to_file=False)
                else:
                    # 未匹配到已知布局：提示厂商名（设备名）和原始编号范围，便于后续补充映射表。
                    # 该设备的事件将按原始按钮/轴编号记录（如 BUTTON_DOWN id=0 key=3）。
                    self._log("G[Unknown]", "WARN",
                              f"未匹配布局 id={i} device={name} buttons={num_buttons} "
                              f"axes={num_axes}（事件将记录原始编号，请把 {name!r} 补充进 "
                              f"CONTROLLER_MAPPINGS_* 表）",
                              to_file=False)
                # 保持 Joystick 对象打开并持有引用（不 quit），SDL 才会持续生成该手柄事件
                self.joysticks[i] = joy
            except Exception as e:
                print(f"初始化手柄 {i} 失败: {e}")

        return True

    def _gamepad_loop(self) -> None:
        """手柄录制后台线程循环（daemon 线程入口）。

        完全依赖 SDL 事件驱动（JOYBUTTONDOWN/UP、JOYHATMOTION、JOYAXISMOTION），
        不再主动轮询 Joystick 对象。插拔手柄由 SDL 自动管理，
        代码只需处理事件本身，与 pynput 的处理方式一致。
        支持"后连"：启动时无手柄也会持续运行事件循环，通过
        JOYDEVICEADDED 事件 + 每 1 秒的兜底扫描发现后插入的手柄。
        循环运行直到 self.running 为 False，退出时 pygame.quit() 释放资源。
        """
        pygame.init()
        # 必须先初始化 joystick 子系统，SDL 才会产生 JOYDEVICEADDED/REMOVED 事件，
        # 否则"程序先启动、手柄后插入"时收不到任何热插拔事件，手柄永远无法被发现。
        pygame.joystick.init()
        # 首次扫描：有手柄就初始化；没有也不退出，留待热插拔事件/定期扫描发现。
        self._init_joysticks()

        last_scan_time = time.time()
        while self.running:
            pygame.event.pump()

            for event in pygame.event.get():
                joy_id = getattr(event, 'joy', None)
                mapping = self.mappings.get(joy_id) if joy_id is not None else None

                # 每次事件都检查手柄是否已知，未知则刷新映射表（应对热插拔后 ID 变化）
                if joy_id is not None and mapping is None:
                    self._init_joysticks()
                    mapping = self.mappings.get(joy_id)

                # 构造带品牌的设备前缀，如 "G[Xbox]"、"G[DualSense]"、"G[?]"
                brand = self.mapping_names.get(joy_id, "?") if joy_id is not None else "?"
                dev = f"G[{brand}]"

                if event.type == pygame.JOYBUTTONDOWN:
                    btn = mapping['buttons'].get(event.button, event.button) if mapping else event.button
                    self._log(dev, "BUTTON_DOWN", f"id={event.joy} key={btn}")

                elif event.type == pygame.JOYBUTTONUP:
                    btn = mapping['buttons'].get(event.button, event.button) if mapping else event.button
                    self._log(dev, "BUTTON_UP", f"id={event.joy} key={btn}")

                elif event.type == pygame.JOYHATMOTION:
                    jid = event.joy
                    cx, cy = event.value
                    px, py = self.last_hats.get(jid, (0, 0))
                    checks = [
                        ("D_Up", cy == 1, py == 1),
                        ("D_Down", cy == -1, py == -1),
                        ("D_Left", cx == -1, px == -1),
                        ("D_Right", cx == 1, px == 1),
                    ]
                    for label, is_active, was_active in checks:
                        if is_active and not was_active:
                            self._log(dev, "BUTTON_DOWN", f"id={jid} key={label}")
                        elif was_active and not is_active:
                            self._log(dev, "BUTTON_UP", f"id={jid} key={label}")
                    self.last_hats[jid] = event.value

                elif event.type == pygame.JOYAXISMOTION:
                    jid = event.joy
                    axis = event.axis
                    val = event.value
                    mapping = self.mappings.get(jid)
                    # 死区过滤：死区内归零，防摇杆回中漂移
                    if abs(val) < DEADZONE:
                        val = 0.0
                    # 该轴变化量（基于事件前的缓存值）
                    old_val = self._get_axis_last(jid, axis)
                    delta = abs(val - old_val)
                    # 无条件更新该轴缓存（合并输出/配对读取需要最新姿态）
                    self._set_axis_last(jid, axis, val)

                    # 定位该轴所属"组"（摇杆 X/Y 合并组 / 扳机单轴组）；
                    # 未知布局无组 → 走下方单轴原逻辑
                    group = None
                    groups = self.stick_groups.get(jid)
                    if groups:
                        for g in groups.values():
                            axes_here = (g["x_axis"], g["y_axis"]) if g["type"] == "stick" \
                                else (g["axis"],)
                            if axis in axes_here:
                                group = g
                                break

                    if group is not None:
                        # 变化太小：不记录（缓存已更新，供配对读取）
                        if delta <= MIN_DELTA:
                            continue
                        now_ms = self._now_ms()
                        # 限频：间隔太短且变化不够大 → 跳过（类似鼠标最短记录间隔）
                        time_ok = (now_ms - group["ts"]) >= AXIS_MIN_INTERVAL_MS
                        big = delta > AXIS_BIG_DELTA
                        # 回中特例：本事件把轴带入死区（val 归零）说明摇杆回正，
                        # 必须记录，否则回弹最后一步"归零"会被限频吞掉，
                        # 导致日志停在非零值（手柄回正数据不回 0）。
                        returning_to_zero = (val == 0.0 and old_val != 0.0)
                        if not (time_ok or big or returning_to_zero):
                            continue
                        if group["type"] == "stick":
                            x = self._get_axis_last(jid, group["x_axis"])
                            y = self._get_axis_last(jid, group["y_axis"])
                            self._log(dev, "AXIS_MOVE",
                                      f"id={jid} stick={group['name']} x={x:.4f} y={y:.4f}")
                        else:
                            self._log(dev, "AXIS_MOVE",
                                      f"id={jid} axis={group['name']} val={val:.4f}")
                        group["ts"] = now_ms
                    else:
                        # 未知布局：保持单轴记录 + 变化率过滤（不额外限频）
                        if delta > MIN_DELTA:
                            axis_label = mapping['axes'].get(axis, axis) if mapping else axis
                            self._log(dev, "AXIS_MOVE",
                                      f"id={jid} axis={axis_label} val={val:.4f}")

                elif event.type == pygame.JOYDEVICEADDED:
                    self._init_joysticks()
                elif event.type == pygame.JOYDEVICEREMOVED:
                    # pygame 2.x 中 JOYDEVICEREMOVED 使用 instance_id
                    # （JOYDEVICEADDED 用 device_index），两者都没有 joy 属性
                    jid = getattr(event, 'instance_id', None)
                    if jid is not None:
                        old = self.joysticks.pop(jid, None)
                        if old is not None:
                            try:
                                old.quit()
                            except Exception:
                                pass
                        self.mappings.pop(jid, None)
                        self.mapping_names.pop(jid, None)
                        self.last_hats.pop(jid, None)
                        self.last_axes.pop(jid, None)
                        self.stick_groups.pop(jid, None)

            # 兜底：定期扫描（每 1 秒），防止 JOYDEVICEADDED 事件因队列
            # 积压/丢失而漏掉后插入的手柄，保证"后连"场景稳定可用。
            now = time.time()
            if now - last_scan_time >= 1.0:
                last_scan_time = now
                if pygame.joystick.get_count() > len(self.joysticks):
                    self._init_joysticks()

            time.sleep(POLL_INTERVAL)

        pygame.quit()

    # ==================================================================
    # 控制方法
    # ==================================================================

    def start(self) -> None:
        """启动所有可用输入设备的录制。

        打开日志文件并写入会话信息头，然后依次启动键盘、鼠标
        （pynput 监听器）和手柄（pygame 后台线程）。键盘监听器会阻塞
        主线程，按 Ctrl+C 停止全部录制；若未安装 pynput，则降级为
        仅录制手柄，主线程等待手柄线程结束。
        """
        if not (HAS_PYNPUT or HAS_PYGAME):
            print("错误: 未安装任何输入库，无法录制。")
            print("请运行 'pip install pynput pygame' 安装。")
            return

        self.running = True

        # 打开日志文件前确保输出目录存在（保持打开，stop() 时关闭）
        Path(self.log_filename).parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.log_filename, "a", encoding="utf-8")

        # 写入会话/系统信息头（含平台/CPU/GPU 等），便于下游定位环境，
        # 同时打印到控制台方便启动时一眼确认
        header = self._write_header()
        if header:
            print(header)

        print("=" * 50)
        print("统一输入记录器已启动")
        print(f"日志文件: {self.log_filename}")
        print("-" * 50)
        labels = {"K": "键盘", "M": "鼠标", "G": "手柄"}
        for dev in ("K", "M", "G"):
            status = "✓ 已就绪" if self.has_device[dev] else ("✗ 未安装" if dev == "G" else "✗ 不可用")
            print(f"  {labels[dev]} [{dev}] : {status}")
        print("-" * 50)
        print("按 Ctrl+C 停止录制")
        print("=" * 50)

        # 启动手柄监听器（daemon 线程）
        if self.has_device["G"]:
            self.listeners["G"] = threading.Thread(target=self._gamepad_loop, daemon=True)
            self.listeners["G"].start()

        # 启动 pynput 监听器（pynput 缺失时仅录制手柄）
        if HAS_PYNPUT:
            self.listeners["K"] = pynput_keyboard.Listener(
                on_press=self.on_key_press, on_release=self.on_key_release,
            )
            self.listeners["M"] = pynput_mouse.Listener(
                on_click=self.on_mouse_click, on_move=self.on_mouse_move,
                on_scroll=self.on_mouse_scroll,
            )
            self.listeners["K"].start()
            self.listeners["M"].start()
            self.listeners["K"].join()
        else:
            # 仅手柄模式：主线程等待手柄线程结束（Ctrl+C 触发 stop 后退出）
            if self.listeners["G"] is not None:
                self.listeners["G"].join()

    def stop(self) -> None:
        """停止所有输入录制并释放资源。

        停止键盘/鼠标监听器、关闭手柄 Joystick 对象和日志文件。
        幂等：若未在录制中（running 为 False）则直接返回。
        """
        if not self.running:
            return
        self.running = False

        # 停止所有监听器
        for dev in ("K", "M"):
            if self.listeners.get(dev) is not None:
                try:
                    self.listeners[dev].stop()
                except Exception:
                    pass

        # 关闭手柄 Joystick 对象（SDL 停止轮询，后续由 gamepad 线程 pygame.quit() 兜底）
        for old in self.joysticks.values():
            try:
                old.quit()
            except Exception:
                pass
        self.joysticks.clear()

        # 关闭日志文件（加锁避免与其它线程的写入竞争）
        with self._log_lock:
            if self._log_file is not None:
                try:
                    self._log_file.close()
                except Exception:
                    pass
                self._log_file = None

        print("\n记录已停止。")


def main() -> None:
    """程序入口：创建统一输入记录器并启动录制。

    启动后阻塞运行；按 Ctrl+C（KeyboardInterrupt）或发生异常时
    调用 stop() 停止并释放资源。
    """
    recorder = UnifiedInputRecorder()
    try:
        recorder.start()
    except KeyboardInterrupt:
        recorder.stop()
    except Exception as e:
        print(f"发生错误: {e}")
        recorder.stop()

if __name__ == "__main__":
    main()
