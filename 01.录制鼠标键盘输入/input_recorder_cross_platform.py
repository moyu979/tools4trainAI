#!/usr/bin/env python3
"""
跨平台输入记录器（统一使用 pynput）
需求：
- 鼠标：仅监听左键、右键与中键的按下和抬起时间
- 键盘：监听所有按键的按下与抬起时间
- 以启动时间命名一个 .txt 文件，逐行写入事件
"""

import sys
from datetime import datetime

from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse


def now_iso() -> str:
    """获取当前时间的 ISO 格式字符串。

    Returns:
        str: 当前时间的 ISO 8601 格式字符串，例如 2026-05-30T20:22:54.425688。
    """
    return datetime.now().isoformat()


class InputRecorder:
    def __init__(self) -> None:
        """初始化输入记录器。

        创建以启动时间命名的日志文件，初始化键盘/鼠标监听器占位符，
        设置鼠标移动降噪参数（最小记录间隔、最小位移）和键盘连发忽略配置。
        """
        # 以启动时间创建日志文件名
        start_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_filename = f"input_{start_str}.txt"
        self.running = False

        # 监听器占位
        self.keyboard_listener: pynput_keyboard.Listener | None = None
        self.mouse_listener: pynput_mouse.Listener | None = None

        # 降噪配置
        self.move_min_interval_ms: int = 50   # 鼠标移动最小记录间隔（毫秒）
        self.move_min_distance: int = 5       # 鼠标移动最小位移（像素）
        self.ignore_key_auto_repeat: bool = True  # 忽略按键自动连发（长按时不重复记 press）

        # 降噪状态
        self._last_move_ts_ms: int = 0
        self._last_move_pos: tuple[int, int] | None = None
        self._pressed_keys: set[str] = set()

    def _write_line(self, line: str) -> None:
        """向日志文件追加写入一行内容。

        Args:
            line: 要写入的日志行字符串（不含换行符）。
        """
        with open(self.log_filename, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def _log(self, device: str, action: str, detail: str) -> None:
        """记录一条格式化日志（同时输出到控制台和文件）。

        日志格式: [ISO时间] 设备 动作 详情
        例如: [2025-09-29T12:00:00.123456] K PRESS key=A

        Args:
            device: 设备类型，'K' 表示键盘，'M' 表示鼠标。
            action: 动作类型，如 'PRESS'、'RELEASE'、'MOVE'、'SCROLL'。
            detail: 详细描述，如 'key=A'、'button=left'。
        """
        line = f"[{now_iso()}] {device} {action} {detail}"
        print(line)
        self._write_line(line)

    # ------------------- 键盘事件 -------------------
    def on_key_press(self, key) -> None:
        """键盘按键按下事件回调。

        优先处理控制字符（如 Ctrl+C），支持自动连发忽略（仅记录第一次按下）。
        当检测到 Ctrl+C 组合键时停止录制。

        Args:
            key: pynput 键盘事件对象，包含按键信息。

        Returns:
            bool | None: 返回 False 可停止监听器。
        """
        # 优先处理控制字符（如 Ctrl+C -> '\x03'）
        ctrl_combo = self._try_parse_control_combo(key)
        if ctrl_combo is not None:
            key_name, letter = ctrl_combo  # e.g. ("ctrl+c", "c")
            if self.ignore_key_auto_repeat:
                if key_name in self._pressed_keys:
                    return
                self._pressed_keys.add(key_name)
            self._log("K", "PRESS", f"key={key_name}")
            if letter == "c":
                self.stop()
                return False
            return

        key_name = self._key_to_string(key)
        # 忽略自动连发：仅第一次 press 记录，直到 release
        if self.ignore_key_auto_repeat:
            if key_name in self._pressed_keys:
                return
            self._pressed_keys.add(key_name)
        self._log("K", "PRESS", f"key={key_name}")

        # 组合键退出：Ctrl + C
        if self._is_ctrl_active() and key_name.lower() == "c":
            self.stop()
            return False

    def on_key_release(self, key):
        """键盘按键释放事件回调。

        记录按键释放日志，并从按下的状态集合中移除该按键。

        Args:
            key: pynput 键盘事件对象，包含按键信息。
        """
        key_name = self._key_to_string(key)
        self._log("K", "RELEASE", f"key={key_name}")
        # 释放时从集合移除
        if key_name in self._pressed_keys:
            self._pressed_keys.remove(key_name)

        # 不再使用 ESC 退出，避免误触

    @staticmethod
    def _key_to_string(key) -> str:
        """将 pynput 按键对象转换为可读的字符串表示。

        优先返回字符键本身，否则返回特殊键的名称。

        Args:
            key: pynput 键盘事件中的键对象。

        Returns:
            str: 按键的字符串表示，如 'a'、'shift'、'enter'。
        """
        try:
            if hasattr(key, "char") and key.char is not None:
                return key.char
        except Exception:
            pass
        try:
            return key.name  # type: ignore[attr-defined]
        except Exception:
            return str(key)

    def _is_ctrl_active(self) -> bool:
        """检查当前是否有任意 Ctrl 键处于按下状态。

        兼容多种 Ctrl 键名称（左/右 Ctrl）。

        Returns:
            bool: 如果有任意 Ctrl 键被按下则返回 True。
        """
        ctrl_names = {"ctrl", "ctrl_l", "ctrl_r", "left_ctrl", "right_ctrl"}
        return any(name in self._pressed_keys for name in ctrl_names)

    @staticmethod
    def _is_control_char(ch: str) -> bool:
        """判断一个字符是否为 ASCII 控制字符。

        Args:
            ch: 待判断的字符。

        Returns:
            bool: 如果是 ASCII 控制字符（\x01 ~ \x1a）则返回 True。
        """
        return len(ch) == 1 and 1 <= ord(ch) <= 26

    @staticmethod
    def _control_char_to_letter(ch: str) -> str:
        """将 ASCII 控制字符转换为对应的字母。

        例如 \x01 -> 'a', \x02 -> 'b', ..., \x1a -> 'z'。

        Args:
            ch: ASCII 控制字符。

        Returns:
            str: 对应的小写字母。
        """
        return chr(ord('a') + (ord(ch) - 1))

    def _try_parse_control_combo(self, key):
        """尝试解析控制字符组合键（如 Ctrl+C）。

        Args:
            key: pynput 键盘事件中的键对象。

        Returns:
            tuple[str, str] | None: 如果检测到控制字符，返回 ("ctrl+<letter>", "<letter>")，
            否则返回 None。
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

    # ------------------- 鼠标事件 -------------------
    def on_mouse_click(self, x: int, y: int, button, pressed: bool):
        """鼠标按键点击事件回调。

        仅记录左键、右键和中键的按下与释放事件。

        Args:
            x: 点击时的鼠标 X 坐标。
            y: 点击时的鼠标 Y 坐标。
            button: pynput 鼠标按钮对象。
            pressed: True 表示按下，False 表示释放。
        """
        btn_name = self._button_to_lrm(button)
        if btn_name is None:
            return
        action = "PRESS" if pressed else "RELEASE"
        self._log("M", action, f"button={btn_name}")

    def on_mouse_scroll(self, x: int, y: int, dx: int, dy: int):
        """鼠标滚轮滚动事件回调。

        记录滚轮滚动的位置和方向。
        dy > 0 表示向上滚动，dy < 0 表示向下滚动。
        dx > 0 表示向右滚动，dx < 0 表示向左滚动。

        Args:
            x: 滚动时的鼠标 X 坐标。
            y: 滚动时的鼠标 Y 坐标。
            dx: 水平滚动量。
            dy: 垂直滚动量。
        """
        self._log("M", "SCROLL", f"x={x} y={y} dx={dx} dy={dy}")

    @staticmethod
    def _button_to_lrm(button) -> str | None:
        """将 pynput 鼠标按钮对象转换为字符串标识。

        Args:
            button: pynput 鼠标按钮对象。

        Returns:
            str | None: 'left'、'right'、'middle' 之一，非三键返回 None。
        """
        if button == pynput_mouse.Button.left:
            return "left"
        if button == pynput_mouse.Button.right:
            return "right"
        if button == pynput_mouse.Button.middle:
            return "middle"
        return None

    # ------------------- 控制 -------------------
    def start(self) -> None:
        """启动键盘和鼠标的输入监听。

        创建并启动 pynput 的键盘和鼠标监听器，
        键盘监听器会阻塞主线程直到录制结束（Ctrl+C 触发停止）。
        鼠标移动事件经过降噪过滤（最小间隔和最小位移）。
        """
        self.running = True
        print("开始监听（ESC 退出）")
        print(f"日志文件: {self.log_filename}")

        self.keyboard_listener = pynput_keyboard.Listener(
            on_press=self.on_key_press,
            on_release=self.on_key_release,
        )
        self.mouse_listener = pynput_mouse.Listener(
            on_click=self.on_mouse_click,
            on_move=self.on_mouse_move,
            on_scroll=self.on_mouse_scroll,
        )

        self.keyboard_listener.start()
        self.mouse_listener.start()

        # 阻塞直到键盘监听结束（例如 ESC 触发 stop）
        self.keyboard_listener.join()

    def stop(self) -> None:
        """停止所有输入监听器。

        安全地停止键盘和鼠标监听器，重置运行状态。
        """
        if not self.running:
            return
        self.running = False
        if self.keyboard_listener is not None:
            try:
                self.keyboard_listener.stop()
            except Exception:
                pass
        if self.mouse_listener is not None:
            try:
                self.mouse_listener.stop()
            except Exception:
                pass
        print("记录已停止")

    # 移动事件放在 stop 下方仅为布局，不影响功能
    def on_mouse_move(self, x: int, y: int) -> None:
        """鼠标移动事件回调（含降噪过滤）。

        通过最小时间间隔和最小曼哈顿距离双重过滤，减少冗余的移动日志。

        Args:
            x: 当前鼠标 X 坐标。
            y: 当前鼠标 Y 坐标。
        """
        now_ms = self._now_ms()
        last_ts = self._last_move_ts_ms
        last_pos = self._last_move_pos

        if last_pos is None:
            should_log = True
        else:
            dt_ok = (now_ms - last_ts) >= self.move_min_interval_ms
            dist_ok = self._manhattan_distance(last_pos, (x, y)) >= self.move_min_distance
            should_log = dt_ok or dist_ok

        if should_log:
            self._log("M", "MOVE", f"x={x} y={y}")
            self._last_move_ts_ms = now_ms
            self._last_move_pos = (x, y)

    @staticmethod
    def _manhattan_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
        """计算两点之间的曼哈顿距离。

        Args:
            a: 第一个点的 (x, y) 坐标。
            b: 第二个点的 (x, y) 坐标。

        Returns:
            int: 曼哈顿距离 |x1-x2| + |y1-y2|。
        """
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    @staticmethod
    def _now_ms() -> int:
        """获取当前时间的毫秒级时间戳。

        Returns:
            int: 当前时间的 Unix 毫秒时间戳。
        """
        return int(datetime.now().timestamp() * 1000)


def main() -> None:
    """主函数：创建输入记录器实例并启动监听。

    捕获 KeyboardInterrupt 和常规异常，确保记录器能够正常停止。
    """
    recorder = InputRecorder()
    try:
        recorder.start()
    except KeyboardInterrupt:
        recorder.stop()
    except Exception as e:
        print(f"发生错误: {e}")
        recorder.stop()


if __name__ == "__main__":
    main()
