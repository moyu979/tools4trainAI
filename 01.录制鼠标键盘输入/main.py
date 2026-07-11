#!/usr/bin/env python3
"""
统一输入记录器 - 同时录制键盘 (K)、鼠标 (M) 和手柄 (G) 输入。

功能：
- 自动枚举并检测键盘、鼠标、手柄设备，仅录制实际存在的设备
- 键盘：记录所有按键的按下与释放，支持自动连发过滤
- 鼠标：记录左/右/中键点击、移动（降噪过滤）和滚轮滚动
- 手柄：记录摇杆轴（死区过滤）、按钮和方向键（D-Pad）
- 统一输出到一个以启动时间命名的 .txt 文件
- 日志行前缀：K=键盘, M=鼠标, G=手柄
- Ctrl+C 停止所有录制

依赖：
- pynput (键盘/鼠标)
- pygame (手柄，可选：未安装时仅禁用手柄录制)
"""

import os
import sys
import time
import threading
from datetime import datetime

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
LOG_FILE_PREFIX = "unified_input"       # 日志文件名前缀

# 键盘降噪
IGNORE_KEY_AUTO_REPEAT = True           # 忽略按键自动连发

# 鼠标降噪
MOVE_MIN_INTERVAL_MS = 50               # 鼠标移动最小记录间隔（毫秒）
MOVE_MIN_DISTANCE = 5                   # 鼠标移动最小位移（像素）

# 手柄降噪
DEADZONE = 0.05                         # 摇杆死区（0.0 ~ 1.0）
MIN_DELTA = 0.01                        # 轴数值最小变化阈值
POLL_INTERVAL = 0.01                    # 事件轮询间隔（秒）

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
                    6: "View", 7: "Menu", 10: "Xbox", 11: "D_Up", 12: "D_Down",
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


def now_iso() -> str:
    """获取当前时间的 ISO 格式字符串。

    Returns:
        str: 当前时间的 ISO 8601 格式字符串。
    """
    return datetime.now().isoformat()


class UnifiedInputRecorder:
    """统一输入记录器 — 同时录制键盘 (K)、鼠标 (M) 和手柄 (G)。"""

    def __init__(self) -> None:
        """初始化统一输入记录器。

        检查可用设备（键盘/鼠标/手柄），创建日志文件，
        配置各设备的降噪参数和状态存储。
        """
        start_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_filename = f"{LOG_FILE_PREFIX}_{start_str}.txt"
        self._log_file = None  # start() 时打开，stop() 时关闭
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

    # ------------------------------------------------------------------
    # 日志写入
    # ------------------------------------------------------------------

    def _write_line(self, line: str) -> None:
        """向已打开的日志文件写入一行内容。"""
        if self._log_file is not None:
            self._log_file.write(line + "\n")
            self._log_file.flush()

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

    # ==================================================================
    # 键盘事件 (pynput)
    # ==================================================================

    def on_key_press(self, key):
        """键盘按键按下事件回调。

        优先处理 Ctrl 组合键（控制字符），支持自动连发忽略。
        Ctrl+C 组合键触发停止录制。

        Args:
            key: pynput 键盘事件对象。

        Returns:
            bool | None: 返回 False 可停止监听器。
        """
        ctrl_combo = self._try_parse_control_combo(key)
        if ctrl_combo is not None:
            key_name, letter = ctrl_combo
            if IGNORE_KEY_AUTO_REPEAT:
                if key_name in self._pressed_keys:
                    return
                self._pressed_keys.add(key_name)
            self._log("K", "PRESS", f"key={key_name}")
            if letter == "c":
                self.stop()
                return False
            return

        key_name = self._key_to_string(key)
        if IGNORE_KEY_AUTO_REPEAT:
            if key_name in self._pressed_keys:
                return
            self._pressed_keys.add(key_name)
        self._log("K", "PRESS", f"key={key_name}")

        if self._is_ctrl_active() and key_name.lower() == "c":
            self.stop()
            return False

    def on_key_release(self, key) -> None:
        """键盘按键释放事件回调。

        Args:
            key: pynput 键盘事件对象。
        """
        key_name = self._key_to_string(key)
        self._log("K", "RELEASE", f"key={key_name}")
        if key_name in self._pressed_keys:
            self._pressed_keys.remove(key_name)

    @staticmethod
    def _key_to_string(key) -> str:
        """将 pynput 按键对象转换为可读字符串。

        Args:
            key: pynput 键盘事件中的键对象。

        Returns:
            str: 按键的字符串表示。
        """
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
        """尝试解析控制字符组合键（如 Ctrl+C）。

        Args:
            key: pynput 键盘事件对象。

        Returns:
            tuple[str, str] | None: ("ctrl+<letter>", "<letter>") 或 None。
        """
        try:
            if hasattr(key, "char") and key.char is not None:
                ch = key.char
                if self._is_control_char(ch):
                    letter = self._control_char_to_letter(ch)
                    return f"ctrl+{letter}", letter
        except Exception:
            pass
        return None

    # ==================================================================
    # 鼠标事件 (pynput)
    # ==================================================================

    def on_mouse_click(self, x: int, y: int, button, pressed: bool) -> None:
        """鼠标按键点击事件回调。

        仅记录左键、右键和中键的按下/释放。

        Args:
            x: 点击时鼠标 X 坐标。
            y: 点击时鼠标 Y 坐标。
            button: pynput 鼠标按钮对象。
            pressed: True 为按下，False 为释放。
        """
        btn_name = self._button_to_lrm(button)
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
    def _button_to_lrm(button) -> str | None:
        """将 pynput 鼠标按钮转换为字符串标识。

        Args:
            button: pynput 鼠标按钮对象。

        Returns:
            str | None: 'left'、'right'、'middle' 或 None。
        """
        if button == pynput_mouse.Button.left:
            return "left"
        if button == pynput_mouse.Button.right:
            return "right"
        if button == pynput_mouse.Button.middle:
            return "middle"
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
        else:
            raise NotImplementedError("Linux 平台手柄捕捉逻辑暂未实现")

        for key in mappings:
            if key.lower() in joy_name.lower():
                return key, mappings[key]
        return None, None

    def _init_joysticks(self) -> bool:
        """重新扫描并初始化所有已连接的手柄。

        仅用于获取手柄名称以确定按键映射，不持有 Joystick 对象引用。
        之后完全依赖 SDL 事件驱动，不再轮询轴。

        Returns:
            bool: 当前至少有一个有效手柄返回 True。
        """
        self.mappings.clear()
        self.mapping_names.clear()
        self.last_hats.clear()
        self.last_axes.clear()

        if not pygame.joystick.get_init():
            pygame.joystick.init()

        pygame.event.pump()
        time.sleep(0.1)
        count = pygame.joystick.get_count()

        if count == 0:
            for _ in range(10):
                time.sleep(0.5)
                pygame.event.pump()
                count = pygame.joystick.get_count()
                if count > 0:
                    break

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
                # 初始化 last_axes 用于轴变化率过滤（通过 JOYAXISMOTION 事件更新）
                self.last_axes[i] = [0.0] * joy.get_numaxes()
                self.last_hats[i] = (0, 0)
                status = f"Mapped as {name}" if mapping else "No specific mapping"
                self._log("G", "INFO", f"Initialized [ID={i}] {name} ({status})", to_file=False)
                joy.quit()
            except Exception as e:
                print(f"初始化手柄 {i} 失败: {e}")

        return True

    def _gamepad_loop(self) -> None:
        """手柄录制后台线程循环。

        完全依赖 SDL 事件驱动（JOYBUTTONDOWN/UP、JOYHATMOTION、JOYAXISMOTION），
        不再主动轮询 Joystick 对象。插拔手柄由 SDL 自动管理，
        代码只需处理事件本身，与 pynput 的处理方式一致。
        """
        pygame.init()
        if not self._init_joysticks():
            pygame.quit()
            return

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
                    # 死区过滤
                    if abs(val) < DEADZONE:
                        val = 0.0
                    # 变化率过滤
                    last_val = self.last_axes.get(jid, [0.0] * (axis + 1))[axis] \
                        if len(self.last_axes.get(jid, [])) > axis else 0.0
                    if abs(val - last_val) > MIN_DELTA:
                        axis_label = mapping['axes'].get(axis, axis) if mapping else axis
                        self._log(dev, "AXIS_MOVE", f"id={jid} axis={axis_label} val={val:.4f}")
                        if jid not in self.last_axes:
                            self.last_axes[jid] = []
                        while len(self.last_axes[jid]) <= axis:
                            self.last_axes[jid].append(0.0)
                        self.last_axes[jid][axis] = val

                elif event.type == pygame.JOYDEVICEADDED:
                    self._init_joysticks()
                elif event.type == pygame.JOYDEVICEREMOVED:
                    jid = event.joy if hasattr(event, 'joy') else None
                    if jid is not None:
                        self.mappings.pop(jid, None)
                        self.mapping_names.pop(jid, None)
                        self.last_hats.pop(jid, None)
                        self.last_axes.pop(jid, None)

            time.sleep(POLL_INTERVAL)

        pygame.quit()

    # ==================================================================
    # 控制方法
    # ==================================================================

    def start(self) -> None:
        """启动所有可用输入设备的录制。

        依次启动键盘、鼠标（pynput 监听器）和手柄（pygame 后台线程）。
        键盘监听器会阻塞主线程，按 Ctrl+C 停止全部录制。
        """
        if not HAS_PYNPUT:
            print("错误: pynput 未安装，无法启动键盘/鼠标录制。")
            print("请运行 'pip install pynput' 安装。")
            return

        self.running = True

        # 打开日志文件（保持打开，stop() 时关闭）
        self._log_file = open(self.log_filename, "a", encoding="utf-8")

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

        # 启动 pynput 监听器
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

    def stop(self) -> None:
        """停止所有输入录制，释放资源。"""
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

        # 关闭日志文件
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        print("\n记录已停止。")


def main() -> None:
    """主函数：创建统一记录器并启动录制。"""
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
