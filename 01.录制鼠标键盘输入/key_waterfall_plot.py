#!/usr/bin/env python3
"""
从 input_YYYYMMDD_HHMMSS.txt 日志绘制键盘/鼠标瀑布图：
横轴为时间（秒），纵轴为事件名（键名或鼠标按钮），每个事件的按下-抬起区间以水平条显示。

用法：
  python key_waterfall_plot.py <日志文件路径>

日志格式（由 input_recorder_cross_platform.py 生成）：
  [ISO] K PRESS key=...
  [ISO] K RELEASE key=...
  [ISO] M PRESS button=left|right|middle
  [ISO] M RELEASE button=left|right|middle
  [ISO] M MOVE x=.. y=..   # 移动会被忽略
"""

import sys
import re
from datetime import datetime
from collections import defaultdict
from typing import List, Tuple, Dict

import matplotlib.pyplot as plt

# 预置键集合（可按需扩展），纵轴顺序使用此列表
DEFAULT_KEYS = (
    [
        "esc", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    ]
    + [
        "`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=",
    ]
    + [
        "tab", "q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "[", "]", "\\",
    ]
    + [
        "caps_lock", "a", "s", "d", "f", "g", "h", "j", "k", "l", ";", "'",
    ]
    + [
        "left_shift", "z", "x", "c", "v", "b", "n", "m", ",", ".", "/", "right_shift",
    ]
    + [
        "left_ctrl", "left_alt", "left_cmd", "space", "right_cmd", "right_alt", "right_ctrl",
    ]
    + [
        "insert", "delete", "home", "end", "page_up", "page_down",
        "up", "down", "left", "right", "enter", "backspace",
    ]
    + [
        "mouse:left", "mouse:middle", "mouse:right",
    ]
)


line_re = re.compile(r"^\[(?P<iso>[^\]]+)\]\s+(?P<dev>[KM])\s+(?P<act>\w+)\s+(?P<detail>.+)$")


def parse_time(iso_str: str) -> float:
    """将 ISO 格式时间字符串解析为 POSIX 时间戳。

    Args:
        iso_str: ISO 8601 格式的时间字符串。

    Returns:
        float: POSIX 时间戳（秒）。
    """
    return datetime.fromisoformat(iso_str).timestamp()


def parse_log(filepath: str) -> List[Tuple[float, str, str]]:
    """解析日志文件，提取键盘和鼠标的按下/释放事件。

    根据正则匹配日志行格式，提取时间戳、动作类型和按键/按钮名称。
    忽略鼠标移动和滚轮事件。

    Args:
        filepath: 日志文件的路径。

    Returns:
        List[Tuple[float, str, str]]: 事件列表，每个元素为 (时间戳, 动作, 名称)。
    """
    events: List[Tuple[float, str, str]] = []  # (ts, action, name)
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = line_re.match(line)
            if not m:
                continue
            dev = m.group("dev")  # "K" or "M"
            act = m.group("act").upper()
            iso = m.group("iso")
            detail = m.group("detail")
            ts = parse_time(iso)

            if dev == "K":
                # detail 示例: key=A 或 key=shift
                if not detail.startswith("key="):
                    continue
                key_name = detail.split("=", 1)[1]
                events.append((ts, act, key_name))
            elif dev == "M":
                # 仅处理按键（不处理 MOVE/SCROLL）
                if act not in ("PRESS", "RELEASE"):
                    continue
                if not detail.startswith("button="):
                    continue
                btn = detail.split("=", 1)[1]
                name = f"mouse:{btn}"
                events.append((ts, act, name))
    return events


def build_intervals(events: List[Tuple[float, str, str]]) -> Dict[str, List[Tuple[float, float]]]:
    """根据事件列表为每个按键构建按下-释放的时间区间。

    对每个按键，匹配 PRESS 和 RELEASE 事件形成 (start, end) 区间。
    若存在未匹配的 PRESS（未释放），则用最后事件时间近似闭合。

    Args:
        events: 事件列表，每个元素为 (时间戳, 动作, 名称)。

    Returns:
        Dict[str, List[Tuple[float, float]]]: 按键名到时间区间列表的映射。
    """
    by_key_intervals: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    pressed_at: Dict[str, float] = {}

    if not events:
        return by_key_intervals

    events_sorted = sorted(events, key=lambda x: x[0])
    last_ts = events_sorted[-1][0]

    for ts, act, key_name in events_sorted:
        if act == "PRESS":
            # 若重复 PRESS 未 RELEASE，覆盖为最新一次按下
            pressed_at[key_name] = ts
        elif act == "RELEASE":
            start = pressed_at.pop(key_name, None)
            if start is not None and ts >= start:
                by_key_intervals[key_name].append((start, ts))

    # 闭合仍在按下的键（以最后事件时间+少许偏移近似）
    tail = last_ts + 0.01
    for key_name, start in pressed_at.items():
        if tail >= start:
            by_key_intervals[key_name].append((start, tail))

    return by_key_intervals


def plot_waterfall(by_key_intervals: Dict[str, List[Tuple[float, float]]], title: str = "Key/Mouse Waterfall") -> None:
    """绘制按键/鼠标事件的瀑布图。

    横轴为时间（秒），纵轴为按键/按钮名称，每个按键的按下-抬起区间以水平条显示。
    时间从第一个事件开始归一化。

    Args:
        by_key_intervals: 按键名到时间区间列表的映射。
        title: 图表标题，默认为 "Key/Mouse Waterfall"。
    """
    if not by_key_intervals:
        print("没有可绘制的键盘事件区间")
        return

    # 将时间归一化到从 0 开始
    min_ts = min(start for intervals in by_key_intervals.values() for start, _ in intervals)
    normalized: Dict[str, List[Tuple[float, float]]] = {
        k: [(s - min_ts, e - min_ts) for (s, e) in v] for k, v in by_key_intervals.items()
    }

    # 构建纵轴键顺序：以默认列表为主，补上日志中出现但不在默认表的键
    observed_keys = sorted(set(normalized.keys()) - set(DEFAULT_KEYS))
    keys = list(DEFAULT_KEYS) + observed_keys

    fig, ax = plt.subplots(figsize=(12, max(4, 0.4 * len(keys))))

    y_ticks = []
    y_labels = []
    max_end = 0.0
    for i, key_name in enumerate(keys):
        intervals = normalized.get(key_name, [])
        spans = [(s, e - s) for (s, e) in intervals if e > s]
        if spans:
            ax.broken_barh(spans, (i - 0.4, 0.8))
            max_end = max(max_end, max(s + d for s, d in spans))
        y_ticks.append(i)
        y_labels.append(key_name)

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Key/Button")
    ax.set_yticks(y_ticks)
    ax.set_yticklabels(y_labels)
    ax.set_title(title)
    ax.grid(True, axis="x", linestyle=":", alpha=0.5)

    # 若没有任何 span，也给出一个最小范围
    if max_end <= 0:
        max_end = 1.0
    ax.set_xlim(0, max_end)
    ax.set_ylim(-1, len(keys))

    plt.tight_layout()
    plt.show()


def main() -> None:
    """主函数：解析命令行参数，读取日志并绘制瀑布图。

    从命令行获取日志文件路径，解析事件，构建时间区间，
    最后调用 matplotlib 绘制并显示瀑布图。
    """
    if len(sys.argv) < 2:
        print("用法: python key_waterfall_plot.py <日志文件路径>")
        return
    filepath = sys.argv[1]
    events = parse_log(filepath)
    intervals = build_intervals(events)
    plot_waterfall(intervals, title=f"Key Waterfall - {filepath}")


if __name__ == "__main__":
    main()


