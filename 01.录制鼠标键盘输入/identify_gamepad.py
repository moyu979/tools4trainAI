"""临时脚本：枚举当前系统识别到的所有手柄，输出设备详情。

用于排查手柄被误识别为哪种设备（如北通被 SDL 认成 NS 手柄）。
"""
import sys

import pygame

pygame.init()
pygame.joystick.init()

count = pygame.joystick.get_count()
print(f"检测到手柄数量: {count}")
print("=" * 70)

if count == 0:
    print("未检测到任何手柄，请确认设备已连接。")
    sys.exit(0)

for i in range(count):
    joy = pygame.joystick.Joystick(i)
    joy.init()
    try:
        guid = joy.get_guid()
    except Exception:
        guid = "(获取失败)"
    print(f"[{i}]")
    print(f"  名称            : {joy.get_name()}")
    print(f"  GUID            : {guid}")
    print(f"  按键数          : {joy.get_numbuttons()}")
    print(f"  轴数            : {joy.get_numaxes()}")
    print(f"  帽(方向键)数    : {joy.get_numhats()}")
    print(f"  轨迹球数        : {joy.get_numballs()}")
    try:
        print(f"  SDL 设备实例ID  : {joy.get_instance_id()}")
    except Exception:
        pass
    print("  --- 轴的初始值 ---")
    try:
        axis_vals = [joy.get_axis(a) for a in range(joy.get_numaxes())]
        print("  " + ", ".join(f"axis{a}={v:.3f}" for a, v in enumerate(axis_vals)))
    except Exception as e:
        print(f"  读取轴值失败: {e}")
    print()

pygame.quit()
