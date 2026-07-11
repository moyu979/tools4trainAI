#!/usr/bin/env python3
"""
手柄输入记录器 (基于 pygame) - macOS 兼容优化版
需求：
- 跨平台支持 (Windows, macOS, Linux)
- 记录所有手柄的轴 (Axes)、按钮 (Buttons) 和方向键 (Hats)
- 自动过滤摇杆死区及微小抖动
- 以启动时间命名 .txt 文件，逐行写入事件
- 可与游戏同时运行，互不干扰
"""

import os
import sys
import time
from datetime import datetime

# 隐藏 pygame 欢迎信息
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"

try:
    import pygame
except ImportError:
    print("错误: 未找到 pygame 库。请运行 'pip install pygame' 安装。")
    sys.exit(1)


# --- 平台预定义手柄映射表 ---
# 由于手中无PS4/PS5手柄，不保证相关手柄内容对应正确
# TODO: 完善验证PS4/PS5手柄映射规则
CONTROLLER_MAPPINGS_MACOS = {
    "Xbox": {
        "buttons": {0: "A", 1: "B", 2: "X", 3: "Y", 4: "View", 5: "Xbox", 6: "Menu", 9: "LB", 10: "RB", 11: "D_Up", 12: "D_Down", 13: "D_Left", 14: "D_Right"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y", 4: "L_Trigger", 5: "R_Trigger"}
    },
    "DualSense": { # PS5
        "buttons": {0: "Cross", 1: "Circle", 2: "Square", 3: "Triangle", 4: "Share", 5: "PS", 6: "Options", 7: "L3", 8: "R3", 9: "L1", 10: "R1", 11: "Up", 12: "Down", 13: "Left", 14: "Right", 15: "Touchpad"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y", 4: "L2", 5: "R2"}
    },
    "PS4": {
        "buttons": {0: "Cross", 1: "Circle", 2: "Square", 3: "Triangle", 4: "Share", 5: "PS", 6: "Options", 7: "L3", 8: "R3", 9: "L1", 10: "R1"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y", 4: "L2", 5: "R2"}
    }
}

CONTROLLER_MAPPINGS_WINDOWS = {
    "Xbox": {
        "buttons": {0: "A", 1: "B", 2: "X", 3: "Y", 4: "LB", 5: "RB", 6: "View", 7: "Menu", 10: "Xbox", 11: "D_Up", 12: "D_Down", 13: "D_Left", 14: "D_Right"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y", 4: "L_Trigger", 5: "R_Trigger"}
    },
    "DualSense": { # PS5
        "buttons": {0: "Cross", 1: "Circle", 2: "Square", 3: "Triangle", 4: "Share", 5: "PS", 6: "Options", 7: "L3", 8: "R3", 9: "L1", 10: "R1", 11: "Up", 12: "Down", 13: "Left", 14: "Right", 15: "Touchpad"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y", 4: "L2", 5: "R2"}
    },
    "PS4": {
        "buttons": {0: "Cross", 1: "Circle", 2: "Square", 3: "Triangle", 4: "Share", 5: "PS", 6: "Options", 7: "L3", 8: "R3", 9: "L1", 10: "R1"},
        "axes": {0: "L_Stick_X", 1: "L_Stick_Y", 2: "R_Stick_X", 3: "R_Stick_Y", 4: "L2", 5: "R2"}
    }
}

def get_mapping(joy_name):
    """根据手柄名称模糊匹配平台对应的按键映射表。

    根据当前操作系统选择对应的映射表（macOS / Windows），
    然后通过手柄名称关键词（如 'xbox'、'dualsense'）进行模糊匹配。

    Args:
        joy_name: 手柄的设备名称字符串。

    Returns:
        dict | None: 匹配到的映射表字典（包含 buttons 和 axes），未匹配返回 None。

    Raises:
        NotImplementedError: 当前系统为 Linux 时抛出，暂不支持。
    """
    CONTROLLER_MAPPINGS = None
    if sys.platform == "darwin":
        CONTROLLER_MAPPINGS = CONTROLLER_MAPPINGS_MACOS
    elif sys.platform == "win32":
        CONTROLLER_MAPPINGS = CONTROLLER_MAPPINGS_WINDOWS
    else:
        raise NotImplementedError("linux平台手柄捕捉逻辑暂未实现，error")

    for key in CONTROLLER_MAPPINGS:
        if key.lower() in joy_name.lower():
            return CONTROLLER_MAPPINGS[key]
    return None


def now_iso() -> str:
    """获取当前时间的 ISO 格式字符串。

    Returns:
        str: 当前时间的 ISO 8601 格式字符串。
    """
    return datetime.now().isoformat()


class GamepadRecorder:
    def __init__(self) -> None:
        """初始化手柄输入记录器。

        创建以启动时间命名的日志文件，设置摇杆死区、轴变化阈值和采样间隔等参数。
        初始化手柄实例、映射表和状态存储字典。
        """
        # 日志配置
        start_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_filename = f"gamepad_{start_str}.txt"
        
        # 记录配置
        self.deadzone = 0.05       # 摇杆死区（0.0-1.0）
        self.min_delta = 0.01      # 轴数值最小变化阈值
        self.poll_interval = 0.01  # 采样间隔 (秒)
        
        self.running = False
        self.joysticks = {}        # 存储已初始化的手柄实例
        self.mappings = {}         # 存储每个手柄对应的映射表
        self.last_axes = {}        # 存储上一次的轴数值
        self.last_hats = {}        # 存储上一次的方向键状态 (按 joy_id 索引)

    def _write_line(self, line: str) -> None:
        """向日志文件追加写入一行内容。

        Args:
            line: 要写入的日志行字符串（不含换行符）。
        """
        try:
            with open(self.log_filename, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        except Exception as e:
            print(f"写入日志失败: {e}")

    def _log(self, action: str, detail: str) -> None:
        """记录一条格式化手柄事件日志（同时输出到控制台和文件）。

        日志格式: [ISO时间] G 动作 详情

        Args:
            action: 事件类型，如 'BUTTON_DOWN'、'AXIS_MOVE'、'INFO'。
            detail: 事件详情，如 'id=0 key=A'。
        """
        line = f"[{now_iso()}] G {action} {detail}"
        print(line)
        self._write_line(line)

    def init_joysticks(self):
        """初始化手柄，增加 macOS 兼容性扫描逻辑"""
        if not pygame.joystick.get_init():
            pygame.joystick.init()
        
        # 强制刷新事件队列，给系统响应时间
        pygame.event.pump()
        time.sleep(0.1)

        count = pygame.joystick.get_count()
        
        # 如果没检测到，尝试重扫描几次
        if count == 0:
            print("正在扫描手柄，请确保手柄已连接...")
            for i in range(10):
                time.sleep(0.5)
                pygame.event.pump()
                count = pygame.joystick.get_count()
                if count > 0:
                    break
                print(f"扫描中... ({i+1}/10)", end="\r")
        
        if count == 0:
            print("\n" + "="*40)
            print("错误: 未检测到任何手柄。")
            if sys.platform == "win32":
                print("当前系统为 Windows，请检查以下设置：")
                print("1. 确保手柄已正确连接到电脑")
                print("2. 确保手柄驱动已正确安装")
                print("3. 如果已经开启，请尝试彻底重启终端应用")
            elif sys.platform == "linux":
                print("当前系统为 Linux，请检查以下设置：")
                print("1. 确保手柄已正确连接到电脑")
                print("2. 确保手柄驱动已正确安装")
                print("3. 如果已经开启，请尝试彻底重启终端应用")
            if sys.platform == "darwin":
                print("当前系统为 macOS，请检查以下设置：")
                print("1. [系统设置] -> [隐私与安全性] -> [输入监听]")
                print("2. 确保您当前的终端 (Terminal/VSCode) 已勾选并开启")
                print("3. 如果已经开启，请尝试彻底重启终端应用")
            print("="*40)
            return False
        
        print(f"\n检测到 {count} 个手柄。")
        for i in range(count):
            try:
                joy = pygame.joystick.Joystick(i)
                joy.init()
                name = joy.get_name()
                guid = joy.get_guid()
                self.joysticks[i] = joy
                
                # 识别型号并获取映射
                mapping = get_mapping(name)
                self.mappings[i] = mapping
                
                self.last_axes[i] = [0.0] * joy.get_numaxes()
                self.last_hats[i] = (0, 0)
                
                status = f"Mapped as {name}" if mapping else "No specific mapping found"
                self._log("INFO", f"Initialized [ID={i}] {name} ({status})")
            except Exception as e:
                print(f"初始化手柄 {i} 失败: {e}")
        
        return True

    def start(self):
        """启动手柄输入录制主循环。

        初始化 pygame 环境，检测并初始化所有已连接的手柄，
        进入事件轮询循环，处理按钮、方向键和摇杆轴事件。
        支持热插拔检测（设备插入/拔出）。
        按 Ctrl+C 停止录制。
        """
        # 初始化 pygame 全局环境
        pygame.init()
        
        # 尝试初始化手柄
        if not self.init_joysticks():
            pygame.quit()
            return

        self.running = True
        print(f"开始录制手柄输入... (按 Ctrl+C 停止)")
        print(f"日志文件: {self.log_filename}")

        try:
            while self.running:
                # 必须定期调用 pump() 以刷新底层 SDL 状态
                pygame.event.pump()
                
                # 处理所有待处理事件
                for event in pygame.event.get():
                    # 获取当前手柄的映射表
                    joy_id = getattr(event, 'joy', None)
                    mapping = self.mappings.get(joy_id) if joy_id is not None else None
                    
                    if event.type == pygame.JOYBUTTONDOWN:
                        btn_label = mapping['buttons'].get(event.button, event.button) if mapping else event.button
                        self._log("BUTTON_DOWN", f"id={event.joy} key={btn_label}")
                    elif event.type == pygame.JOYBUTTONUP:
                        btn_label = mapping['buttons'].get(event.button, event.button) if mapping else event.button
                        self._log("BUTTON_UP", f"id={event.joy} key={btn_label}")
                    elif event.type == pygame.JOYHATMOTION:
                        joy_id = event.joy
                        curr_x, curr_y = event.value
                        prev_x, prev_y = self.last_hats.get(joy_id, (0, 0))

                        # 定义四个方向的当前状态与之前的状态
                        checks = [
                            ("D_Up",    curr_y == 1,  prev_y == 1),
                            ("D_Down",  curr_y == -1, prev_y == -1),
                            ("D_Left",  curr_x == -1, prev_x == -1),
                            ("D_Right", curr_x == 1,  prev_x == 1),
                        ]

                        for label, is_active, was_active in checks:
                            if is_active and not was_active:
                                self._log("BUTTON_DOWN", f"id={joy_id} key={label}")
                            elif was_active and not is_active:
                                self._log("BUTTON_UP", f"id={joy_id} key={label}")

                        self.last_hats[joy_id] = event.value

                    elif event.type == pygame.JOYDEVICEADDED:
                        print(f"\n[检测到新设备插入]")
                        self.init_joysticks()
                    elif event.type == pygame.JOYDEVICEREMOVED:
                        print(f"\n[检测到设备拔出]")
                        self.init_joysticks()

                # 处理轴 (Axes) - 通过轮询获取连续值
                for joy_id, joy in self.joysticks.items():
                    mapping = self.mappings.get(joy_id)
                    try:
                        for a in range(joy.get_numaxes()):
                            val = joy.get_axis(a)
                            
                            # 死区过滤
                            if abs(val) < self.deadzone:
                                val = 0.0
                            
                            # 变化率过滤
                            last_val = self.last_axes[joy_id][a]
                            if abs(val - last_val) > self.min_delta:
                                axis_label = mapping['axes'].get(a, a) if mapping else a
                                self._log("AXIS_MOVE", f"id={joy_id} axis={axis_label} val={val:.4f}")
                                self.last_axes[joy_id][a] = val
                    except:
                        continue

                time.sleep(self.poll_interval)

        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            print(f"运行出错: {e}")
            self.stop()

    def stop(self):
        """停止手柄输入录制。

        重置运行状态，释放 pygame 的摇杆和全局资源。
        """
        if not self.running:
            return
        self.running = False
        self._log("INFO", "Recording stopped.")
        pygame.joystick.quit()
        pygame.quit()
        print("\n记录已停止。")


if __name__ == "__main__":
    recorder = GamepadRecorder()
    recorder.start()
