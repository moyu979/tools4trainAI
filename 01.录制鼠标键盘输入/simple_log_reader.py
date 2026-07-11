#!/usr/bin/env python3
"""
简化的日志读取器
用于读取分离的键盘和鼠标日志
"""

import json
import sys
from datetime import datetime

def load_keyboard_log(filename="keyboard_log.jsonl"):
    """从 JSONL 文件加载键盘日志事件。

    Args:
        filename: 键盘日志 JSONL 文件路径，默认为 "keyboard_log.jsonl"。

    Returns:
        list: 键盘事件列表，每个事件为字典格式。文件不存在时返回空列表。
    """
    events = []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except FileNotFoundError:
        print(f"键盘日志文件不存在: {filename}")
    return events

def load_mouse_log(filename="mouse_log.jsonl"):
    """从 JSONL 文件加载鼠标日志事件。

    Args:
        filename: 鼠标日志 JSONL 文件路径，默认为 "mouse_log.jsonl"。

    Returns:
        list: 鼠标事件列表，每个事件为字典格式。文件不存在时返回空列表。
    """
    events = []
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    except FileNotFoundError:
        print(f"鼠标日志文件不存在: {filename}")
    return events

def analyze_keyboard(events):
    """分析键盘事件并打印统计信息。

    统计事件总数、按下/释放次数，以及出现频率最高的前 10 个按键。

    Args:
        events: 键盘事件列表，每个事件应包含 'key' 和 'k'（动作类型）字段。
    """
    if not events:
        print("没有键盘事件")
        return
    
    print(f"键盘事件总数: {len(events)}")
    
    # 按键统计
    key_counts = {}
    press_count = 0
    release_count = 0
    
    for event in events:
        key = event['key']
        action = event['k']
        
        if action == 'press':
            press_count += 1
        else:
            release_count += 1
        
        key_counts[key] = key_counts.get(key, 0) + 1
    
    print(f"按键按下: {press_count}, 按键释放: {release_count}")
    print("最常用按键:")
    for key, count in sorted(key_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {key}: {count}")

def analyze_mouse(events):
    """分析鼠标事件并打印统计信息。

    统计事件总数和各类动作（如 MOVE、PRESS、RELEASE、SCROLL）的分布。

    Args:
        events: 鼠标事件列表，每个事件应包含 'm'（动作类型）字段。
    """
    if not events:
        print("没有鼠标事件")
        return
    
    print(f"鼠标事件总数: {len(events)}")
    
    # 动作统计
    action_counts = {}
    for event in events:
        action = event['m']
        action_counts[action] = action_counts.get(action, 0) + 1
    
    print("鼠标动作统计:")
    for action, count in action_counts.items():
        print(f"  {action}: {count}")

def replay_keyboard(events):
    """回放键盘事件（仅显示前 20 条）。

    按顺序打印事件的序号、动作类型（PRESS/RELEASE）和按键名称。

    Args:
        events: 键盘事件列表，每个事件应包含 'k'（动作类型）和 'key' 字段。
    """
    print("键盘事件重放:")
    for i, event in enumerate(events[:20]):  # 只显示前20个
        action = event['k'].upper()
        key = event['key']
        print(f"{i+1:3d}. {action} {key}")

def replay_mouse(events):
    """回放鼠标事件（仅显示前 20 条）。

    按顺序打印事件的序号和详情，区分 MOVE、PRESS/RELEASE 和 SCROLL 三种类型。

    Args:
        events: 鼠标事件列表，每个事件应包含动作、坐标、按钮等字段。
    """
    print("鼠标事件重放:")
    for i, event in enumerate(events[:20]):  # 只显示前20个
        action = event['m'].upper()
        if action == "MOVE":
            print(f"{i+1:3d}. MOVE ({event['x']}, {event['y']})")
        elif action in ["PRESS", "RELEASE"]:
            print(f"{i+1:3d}. {action} {event['button']} at ({event['x']}, {event['y']})")
        elif action == "SCROLL":
            print(f"{i+1:3d}. SCROLL ({event['dx']}, {event['dy']}) at ({event['x']}, {event['y']})")

def main():
    """主函数：解析命令行参数，执行对应的日志分析或回放操作。

    支持命令:
    - keyboard: 分析键盘日志
    - mouse: 分析鼠标日志
    - replay-k: 回放键盘事件
    - replay-m: 回放鼠标事件
    - all: 分析所有日志
    """
    if len(sys.argv) < 2:
        print("用法: python simple_log_reader.py <命令>")
        print("命令:")
        print("  keyboard    分析键盘日志")
        print("  mouse       分析鼠标日志")
        print("  replay-k    重放键盘事件")
        print("  replay-m    重放鼠标事件")
        print("  all         分析所有日志")
        return
    
    command = sys.argv[1]
    
    if command == "keyboard":
        events = load_keyboard_log()
        analyze_keyboard(events)
    
    elif command == "mouse":
        events = load_mouse_log()
        analyze_mouse(events)
    
    elif command == "replay-k":
        events = load_keyboard_log()
        replay_keyboard(events)
    
    elif command == "replay-m":
        events = load_mouse_log()
        replay_mouse(events)
    
    elif command == "all":
        print("=== 键盘日志分析 ===")
        k_events = load_keyboard_log()
        analyze_keyboard(k_events)
        
        print("\n=== 鼠标日志分析 ===")
        m_events = load_mouse_log()
        analyze_mouse(m_events)
    
    else:
        print(f"未知命令: {command}")

if __name__ == "__main__":
    main()
