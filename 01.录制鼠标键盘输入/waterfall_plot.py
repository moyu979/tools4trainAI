#!/usr/bin/env python3
"""
从 input_YYYYMMDD_HHMMSS.txt 日志分别绘制键盘、鼠标、手柄三幅瀑布图。

横轴为时间（秒），纵轴为事件名，每个事件的按下-抬起区间以水平条显示。

用法：
  python waterfall_plot.py <日志文件路径>

日志格式（由 record.py 生成）：
  [ISO] K PRESS key=a vk=65
  [ISO] K RELEASE key=a vk=65
  [ISO] M PRESS button=left|right|middle|x1|x2
  [ISO] M RELEASE button=left|right|middle|x1|x2
  [ISO] M MOVE x=.. y=..            # 移动被忽略
  [ISO] G[Xbox] BUTTON_DOWN id=0 key=A
  [ISO] G[Xbox] BUTTON_UP id=0 key=A
  [ISO] G[Xbox] AXIS_MOVE id=0 stick=L x=.. y=..   # 摇杆/扳机轴事件被忽略
  # 开头的行是会话信息头，解析时自动跳过
"""

import sys
import re
from datetime import datetime
from collections import defaultdict
from typing import List, Tuple, Dict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# 预置键顺序列表
# ---------------------------------------------------------------------------

KEYBOARD_KEYS = [
    "esc", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
    "`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "=",
    "tab", "q", "w", "e", "r", "t", "y", "u", "i", "o", "p", "[", "]", "\\",
    "caps_lock", "a", "s", "d", "f", "g", "h", "j", "k", "l", ";", "'",
    "left_shift", "z", "x", "c", "v", "b", "n", "m", ",", ".", "/", "right_shift",
    "left_ctrl", "left_alt", "left_cmd", "space", "right_cmd", "right_alt", "right_ctrl",
    "insert", "delete", "home", "end", "page_up", "page_down",
    "up", "down", "left", "right", "enter", "backspace",
]

MOUSE_KEYS = ["mouse:left", "mouse:middle", "mouse:right", "mouse:x1", "mouse:x2"]

GAMEPAD_KEYS = [
    "A", "B", "X", "Y",
    "LB", "RB", "L1", "R1",
    "View", "Menu", "Share", "Options",
    "Xbox", "PS",
    "L3", "R3",
    "D_Up", "D_Down", "D_Left", "D_Right",
    "Cross", "Circle", "Square", "Triangle",
    "Up", "Down", "Left", "Right",
    "Touchpad",
]

# 统一正则：设备前缀支持 K / M / G[品牌]
line_re = re.compile(
    r"^\[(?P<iso>[^\]]+)\]\s+"
    r"(?P<dev>[KM]|G\[[^\]]*\])"        # K / M / G[Xbox]
    r"\s+(?P<act>\w+)\s+"
    r"(?P<detail>.+)$"
)


def parse_time(iso_str: str) -> float:
    """将 ISO 格式时间字符串解析为 POSIX 时间戳。

    Args:
        iso_str: ISO 8601 格式的时间字符串。

    Returns:
        float: POSIX 时间戳（秒）。
    """
    return datetime.fromisoformat(iso_str).timestamp()


def device_category(dev: str) -> str:
    """将设备前缀映射为分类名。

    Args:
        dev: 设备前缀，如 'K'、'M'、'G[Xbox]'。

    Returns:
        str: 'K'、'M' 或 'G'。
    """
    if dev.startswith("G"):
        return "G"
    return dev


def parse_log(filepath: str) -> Dict[str, List[Tuple[float, str, str]]]:
    """解析日志文件，按设备分类提取按键按下/释放事件。

    Args:
        filepath: 日志文件的路径。

    Returns:
        Dict[str, List[Tuple[float, str, str]]]: 按设备分类的事件列表。
           键为 'K'（键盘）、'M'（鼠标）、'G'（手柄）。
    """
    all_events: Dict[str, List[Tuple[float, str, str]]] = {
        "K": [], "M": [], "G": []
    }

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = line_re.match(line)
            if not m:
                continue

            dev = m.group("dev")
            act = m.group("act").upper()
            iso = m.group("iso")
            detail = m.group("detail")
            ts = parse_time(iso)
            cat = device_category(dev)

            if cat == "K":
                # [ISO] K PRESS key=a vk=65 → 提取 key 名（到空格为止）
                key_match = re.search(r"key=(\S+)", detail)
                if not key_match:
                    continue
                key_name = key_match.group(1)
                all_events["K"].append((ts, act, key_name))

            elif cat == "M":
                # 仅处理按键（不处理 MOVE/SCROLL）
                if act not in ("PRESS", "RELEASE"):
                    continue
                if not detail.startswith("button="):
                    continue
                btn = detail.split("=", 1)[1]
                all_events["M"].append((ts, act, f"mouse:{btn}"))

            elif cat == "G":
                # 仅处理按钮（不处理 AXIS_MOVE）
                if act not in ("BUTTON_DOWN", "BUTTON_UP"):
                    continue
                # detail: id=0 key=A
                key_match = re.search(r"key=(\S+)", detail)
                if not key_match:
                    continue
                key_name = key_match.group(1)
                # 统一动作名
                g_act = "PRESS" if act == "BUTTON_DOWN" else "RELEASE"
                all_events["G"].append((ts, g_act, key_name))

    return all_events


def build_intervals(events: List[Tuple[float, str, str]]) -> Dict[str, List[Tuple[float, float]]]:
    """根据事件列表为每个按键构建按下-释放的时间区间。

    Args:
        events: 事件列表，每个元素为 (时间戳, 动作, 名称)。

    Returns:
        Dict[str, List[Tuple[float, float]]]: 按键名到时间区间列表的映射。
    """
    intervals: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    pressed_at: Dict[str, float] = {}

    if not events:
        return intervals

    events_sorted = sorted(events, key=lambda x: x[0])
    last_ts = events_sorted[-1][0]

    for ts, act, name in events_sorted:
        if act == "PRESS":
            pressed_at[name] = ts
        elif act == "RELEASE":
            start = pressed_at.pop(name, None)
            if start is not None and ts >= start:
                intervals[name].append((start, ts))

    # 闭合仍在按下的键
    tail = last_ts + 0.01
    for name, start in pressed_at.items():
        if tail >= start:
            intervals[name].append((start, tail))

    return intervals


def plot_waterfall(
    intervals: Dict[str, List[Tuple[float, float]]],
    default_keys: List[str],
    title: str,
    ax: plt.Axes,
) -> None:
    """在指定坐标轴上绘制瀑布图。

    Args:
        intervals: 按键名到时间区间列表的映射。
        default_keys: 纵轴默认键顺序列表。
        title: 子图标题。
        ax: matplotlib 坐标轴对象。
    """
    if not intervals:
        # 用英文提示，避免 matplotlib 默认字体缺中文字形（跨平台显示为方框）
        ax.text(0.5, 0.5, "No events", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        return

    # 时间归一化
    min_ts = min(s for iv in intervals.values() for s, _ in iv)
    normalized: Dict[str, List[Tuple[float, float]]] = {
        k: [(s - min_ts, e - min_ts) for (s, e) in v] for k, v in intervals.items()
    }

    observed = sorted(set(normalized.keys()) - set(default_keys))
    keys = list(default_keys) + observed

    max_end = 0.0
    for i, key_name in enumerate(keys):
        spans = [(s, d - s) for (s, d) in normalized.get(key_name, []) if d > s]
        if spans:
            ax.broken_barh(spans, (i - 0.4, 0.8))
            max_end = max(max_end, max(s + w for s, w in spans))

    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Key/Button")
    ax.set_yticks(range(len(keys)))
    ax.set_yticklabels(keys, fontsize=8)
    ax.set_title(title)
    ax.grid(True, axis="x", linestyle=":", alpha=0.5)
    ax.set_xlim(0, max_end if max_end > 0 else 1.0)
    ax.set_ylim(-1, len(keys))


def main() -> None:
    """主函数：解析日志，绘制键盘/鼠标/手柄三幅瀑布图。"""
    if len(sys.argv) < 2:
        print("用法: python waterfall_plot.py <日志文件路径>")
        return

    filepath = sys.argv[1]

    # 解析日志
    all_events = parse_log(filepath)

    # 为每种设备构建区间
    kb_intervals = build_intervals(all_events["K"])
    ms_intervals = build_intervals(all_events["M"])
    gp_intervals = build_intervals(all_events["G"])

    # 分别绘制三幅独立窗口
    from pathlib import Path
    log_path = Path(filepath)
    stem = log_path.stem  # 如 input_20260711_120000

    datasets = [
        (kb_intervals, KEYBOARD_KEYS, "keyboard"),
        (ms_intervals, MOUSE_KEYS, "mouse"),
        (gp_intervals, GAMEPAD_KEYS, "gamepad"),
    ]

    for intervals, default_keys, name in datasets:
        fig, ax = plt.subplots(figsize=(14, max(4, 0.35 * len(default_keys) + 2)))
        plot_waterfall(intervals, default_keys,
                       f"{name.title()} Waterfall — {filepath}", ax)

        png_path = log_path.with_name(f"{name}_waterfall_{stem}.png")
        plt.tight_layout()
        plt.savefig(str(png_path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"已保存: {png_path}")


if __name__ == "__main__":
    main()
