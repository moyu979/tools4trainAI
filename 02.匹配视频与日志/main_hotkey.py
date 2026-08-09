#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════
# tools4trainAI - 02.匹配视频与日志
# 状态（2026-08-09）：代码已人工检查，尚未跑实际数据验证，请实测后再使用。
# TODO: 尚未跑实际数据验证，请实测后再使用（2026-08-09）
# ═══════════════════════════════════════════════════════════════════════════
"""
多对多·录制热键对齐：以日志中"录制开始快捷键"为锚点，直接对齐视频与日志。

场景（多对多）:
    日志记录一定先打开，因此日志里必然保存了"开始录制"的快捷键事件：
        - NVIDIA（ShadowPlay/ReLive 等）: Alt+F9
        - AMD（ReLive）               : Ctrl+Shift+E
    每个热键按下 = 一次录制的开始时间点。据此在 ±2 秒容差内去找视频文件，
    找到后直接把该视频与日志中从热键起的区间对齐裁剪。

日志格式（txt）:
    # 会话信息头（以 # 开头，解析时自动跳过）
    [2026-05-30T20:22:54.425688] K PRESS key=alt_l vk=164
    [2026-05-30T20:22:54.425688] K PRESS key=f9 vk=120
    [2026-05-30T20:22:54.425688] M MOVE x=803 y=482

视频文件名格式（支持多种）:
    Yuan Shen 原神 2026.05.28 - 22.09.59.03.mp4   点号日期+短横+点号时间(秒+厘秒)
    Honkai Star Rail_2026.07.20-00.01.mp4          点号日期+短横+点号时间(仅时分/带秒)
    2026-07-11 00-08-45.mp4                        短横日期+空格+短横时间(含秒)
    Screenrecorder-2026-06-13-13-21-10-525.mp4     短横日期-时间-毫秒

输出:
    对每个 (热键时间点, 视频) 匹配对生成一条 ffmpeg 剪切命令（流复制，只生成
    不运行），同时把日志裁剪到同一区间；视频与日志按对齐开始时间（热键时间）
    命名成对输出。

用法:
    python main_hotkey.py <log_dir> <video_dir> <result_dir> [aligned_dir]
"""

from __future__ import annotations

import re
import sys
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# 常量与配置
# ---------------------------------------------------------------------------

TOLERANCE = 2.0              # 找视频的容差（秒），±2
ALIGNED_SUBDIR = "aligned"          # 对齐输出子目录（相对 result_dir）
SCRIPT_FILENAME = "cut_ffmpeg_hotkey.bat"  # 汇总脚本文件名（只生成，不运行）
COPY_CODEC = "copy"                 # 剪切方式：流复制（快、无损；起点可能在关键帧处）
DEFAULT_MAX_VIDEO_HOURS = 2         # 无法获取时长时的保守假设

# 录制开始快捷键配置：trigger 为触发键，mods 为所需修饰键组（每组满足其一即可）
RECORD_HOTKEYS = [
    {"name": "NVIDIA Alt+F9", "trigger": "f9",
     "mods": [("alt_l", "alt_r")]},
    {"name": "AMD Ctrl+Shift+E", "trigger": "e",
     "mods": [("ctrl_l", "ctrl_r"), ("shift_l", "shift_r")]},
]


# ---------------------------------------------------------------------------
# 日志解析（兼容以 # 开头的会话信息头）
# ---------------------------------------------------------------------------

LOG_TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]")
# 键盘事件：K PRESS / K RELEASE + key=<名字>（与鼠标 M PRESS 区分，用 K 前缀）
KEY_EVENT_RE = re.compile(r"\bK (PRESS|RELEASE) key=(\S+)")


@dataclass
class LogRange:
    """单个日志文件的时间信息。"""
    path: Path                            # 日志文件路径
    start: datetime                       # 首个事件时间
    end: datetime                         # 最后一个事件时间
    hotkeys: list[tuple[datetime, str]] = field(default_factory=list)  # (时间, 名称)


def parse_log_timestamp(line: str) -> datetime | None:
    """从日志行头部提取 ISO 格式时间戳。"""
    m = LOG_TS_RE.match(line)
    if m:
        try:
            ts = datetime.fromisoformat(m.group(1))
            return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts
        except ValueError:
            return None
    return None


def _hotkey_complete(h: dict, held: set[str]) -> bool:
    """判断录制热键 h 此刻是否已全部按下。

    Args:
        h: 热键配置，含 trigger（触发键）与 mods（修饰键组列表）。
        held: 当前仍处于按下状态的键集合。

    Returns:
        bool: 触发键已按住，且每组修饰键中至少有一个被按住时返回 True。
    """
    if h["trigger"] not in held:
        return False
    # 每个修饰键组（如 (ctrl_l, ctrl_r)）只需其中任意一个键被按住即可
    for group in h["mods"]:
        if not any(key in held for key in group):
            return False
    return True


def detect_record_hotkeys(text: str) -> list[tuple[datetime, str]]:
    """在日志中检测"录制开始"快捷键（如 Alt+F9 / Ctrl+Shift+E）。

    用"按下保持集合"跟踪按键状态，在组合键补全（上升沿）的瞬间记录热键时间点，
    自动兼容按键先后顺序不同（先按修饰键再按触发键，或反之）。

    Args:
        text: 日志文件的完整文本。

    Returns:
        list[tuple[datetime, str]]: [(热键时间点, 热键名称), ...]，按时间升序。
    """
    held: set[str] = set()
    prev: dict[str, bool] = {h["name"]: False for h in RECORD_HOTKEYS}
    detections: list[tuple[datetime, str]] = []

    for line in text.splitlines():
        ts = parse_log_timestamp(line)
        if ts is None:
            continue
        m = KEY_EVENT_RE.search(line)
        if not m:
            continue
        action, key = m.group(1), m.group(2)
        if action == "PRESS":
            held.add(key)
        elif action == "RELEASE":
            held.discard(key)
        else:
            continue

        for h in RECORD_HOTKEYS:
            complete = _hotkey_complete(h, held)
            if complete and not prev[h["name"]]:
                detections.append((ts, h["name"]))
            prev[h["name"]] = complete

    return detections


def collect_log_ranges(log_dir: Path) -> list[LogRange]:
    """扫描日志目录，收集每个日志的时间区间与录制热键时间点。"""
    ranges: list[LogRange] = []

    for txt_path in sorted(log_dir.glob("*.txt")):
        try:
            text = txt_path.read_text(encoding="utf-8")
        except Exception:
            continue
        if text.startswith("\ufeff"):
            text = text[1:]

        file_min: datetime | None = None
        file_max: datetime | None = None

        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            ts = parse_log_timestamp(line)
            if ts is None:
                continue
            if file_min is None or ts < file_min:
                file_min = ts
            if file_max is None or ts > file_max:
                file_max = ts

        if file_min is not None and file_max is not None:
            ranges.append(LogRange(
                path=txt_path,
                start=file_min,
                end=file_max,
                hotkeys=detect_record_hotkeys(text),
            ))

    return ranges


# ---------------------------------------------------------------------------
# 视频文件名解析（支持多种时间格式）
# ---------------------------------------------------------------------------

# 支持的视频文件名时间格式（按优先级尝试，取第一个匹配）：
#   1) 名称 2026.05.28 - 22.09.59.03.mp4     点号日期 + 短横 + 点号时间（秒+厘秒）
#   2) 名称_2026.07.20-00.01.mp4 / 2026.07.26-18.22.mp4
#                                            点号日期 + 短横 + 点号时间（仅时分/带秒）
#   3) 2026-07-11 00-08-45.mp4               短横日期 + 空格 + 短横时间（含秒）
#   4) 名称-2026-06-13-13-21-10-525.mp4      短横日期-短横时间-毫秒（无空格）
_VIDEO_FNAME_PATTERNS = [
    re.compile(
        r"(?P<y>\d{4})\.(?P<mo>\d{2})\.(?P<d>\d{2})\s*-\s*"
        r"(?P<h>\d{2})\.(?P<mi>\d{2})\.(?P<s>\d{2})\.(?P<cs>\d{2})"
    ),
    re.compile(
        r"(?P<y>\d{4})\.(?P<mo>\d{2})\.(?P<d>\d{2})\s*-\s*"
        r"(?P<h>\d{2})\.(?P<mi>\d{2})(?:\.(?P<s>\d{2}))?(?!\.\d)"
    ),
    re.compile(
        r"(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})\s+"
        r"(?P<h>\d{2})-(?P<mi>\d{2})-(?P<s>\d{2})"
    ),
    re.compile(
        r"(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})-"
        r"(?P<h>\d{2})-(?P<mi>\d{2})-(?P<s>\d{2})-(?P<ms>\d{3})"
    ),
]


def parse_video_start_time(filename: str) -> datetime | None:
    """从视频文件名解析录制开始时间（支持多种时间格式）。

    支持格式：
      1) "Yuan Shen 原神 2026.05.28 - 22.09.59.03.mp4"  （点号，厘秒）
      2) "Honkai Star Rail_2026.07.20-00.01.mp4" / "2026.07.26-18.22.mp4"
                                                        （点号，仅时分/带秒）
      3) "2026-07-11 00-08-45.mp4"                     （短横日期+空格+短横时间）
      4) "Screenrecorder-2026-06-13-13-21-10-525.mp4"  （短横日期-时间-毫秒）

    Args:
        filename: 视频文件名（不含路径）。

    Returns:
        datetime | None: 解析成功返回视频开始时间的 datetime 对象，失败返回 None。
    """
    for pat in _VIDEO_FNAME_PATTERNS:
        m = pat.search(filename)
        if not m:
            continue

        year, month, day = int(m["y"]), int(m["mo"]), int(m["d"])
        hour, minute = int(m["h"]), int(m["mi"])
        second = int(m["s"]) if m["s"] else 0

        groups = m.groupdict()
        if groups.get("cs") is not None:
            microsecond = int(groups["cs"]) * 10000   # 厘秒 → 微秒
        elif groups.get("ms") is not None:
            microsecond = int(groups["ms"]) * 1000    # 毫秒 → 微秒
        else:
            microsecond = 0

        return datetime(year, month, day, hour, minute, second, microsecond)

    return None


def get_video_duration(video_path: Path) -> float | None:
    """通过 ffprobe 获取视频文件的时长。"""
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(video_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return None
        info = json.loads(proc.stdout)
        return float(info["format"]["duration"])
    except (FileNotFoundError, subprocess.TimeoutExpired,
            subprocess.SubprocessError, json.JSONDecodeError,
            KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 对齐输出：生成 ffmpeg 剪切命令 + 裁剪日志
# ---------------------------------------------------------------------------

def aligned_name(dt: datetime) -> str:
    """把对齐开始时间转成 Windows 安全的文件名（无冒号，可排序）。"""
    return dt.strftime("%Y-%m-%dT%H-%M-%S.%f")


def build_ffmpeg_cut(input_video: Path, offset: float, duration: float,
                     output_video: Path) -> str:
    """生成一条 ffmpeg 剪切命令字符串（仅生成，不执行）。"""
    return (
        f'ffmpeg -y -ss {offset:.3f} -i "{input_video}" '
        f'-t {duration:.3f} -c {COPY_CODEC} '
        f'-avoid_negative_ts make_zero "{output_video}"'
    )


def trim_log(log_path: Path, seg_start: datetime, seg_end: datetime,
             out_path: Path) -> int:
    """把日志裁剪到 [seg_start, seg_end] 区间，保留 # 开头的会话信息头。"""
    try:
        text = log_path.read_text(encoding="utf-8")
    except Exception:
        return 0
    if text.startswith("\ufeff"):
        text = text[1:]

    written = 0
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# 对齐裁剪区间: {seg_start.isoformat()} ~ {seg_end.isoformat()}\n")
        for line in text.splitlines():
            if not line:
                continue
            if line.startswith("#"):
                f.write(line + "\n")
                continue
            ts = parse_log_timestamp(line)
            if ts is None:
                continue
            if seg_start <= ts <= seg_end:
                f.write(line + "\n")
                written += 1
    return written


def _write_script(script_path: Path, commands: list[str]) -> None:
    """把 ffmpeg 剪切命令写入一个汇总批处理脚本（只生成，不运行）。"""
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "REM ============================================================",
        "REM tools4trainAI - 录制热键对齐裁剪 ffmpeg 脚本（自动生成，请核对后运行）",
        f"REM 共 {len(commands)} 条剪切命令。",
        "REM ============================================================",
        "",
    ]
    for cmd in commands:
        lines.append(cmd)
        lines.append("")
    if commands:
        lines.append("echo 全部剪切命令执行完毕")
    lines.append("pause")
    script_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def main() -> None:
    """主函数：按录制热键定位视频并生成对齐裁剪脚本（只生成不运行）。

    流程：
    1. 从命令行参数获取日志目录、视频目录、结果目录（可选第 4 个参数为对齐目录）。
    2. 扫描日志，检测每个日志中的"录制开始"快捷键时间点（NVIDIA Alt+F9 / AMD Ctrl+Shift+E）。
    3. 遍历视频文件，解析文件名开始时间并用 ffprobe 获取时长。
    4. 对每个热键时间点，在 ±2 秒容差内找最近的视频并匹配（一个视频只匹配一次）。
    5. 对每个匹配对，以热键时间为对齐起点，终点取（同日志下一个热键 / 日志结束 / 视频结束）
       的最小值；生成 ffmpeg 剪切命令（流复制，只生成不运行），并把日志裁剪到同一区间。
    6. 汇总生成一个 ffmpeg 脚本文件，输出统计（含未匹配项）。
    """
    if len(sys.argv) not in (4, 5):
        print("用法: python main_hotkey.py <log_dir> <video_dir> <result_dir> [aligned_dir]")
        print("示例: python main_hotkey.py ./log ./video ./result")
        sys.exit(1)

    log_dir = Path(sys.argv[1])
    video_dir = Path(sys.argv[2])
    result_dir = Path(sys.argv[3])
    aligned_dir = (Path(sys.argv[4]) if len(sys.argv) == 5
                   else result_dir / ALIGNED_SUBDIR)

    if not log_dir.is_dir():
        print(f"错误: 日志目录不存在 -> {log_dir}")
        sys.exit(1)
    if not video_dir.is_dir():
        print(f"错误: 视频目录不存在 -> {video_dir}")
        sys.exit(1)

    # ---- 1. 收集日志区间 + 录制热键 ----
    print("正在扫描日志文件并检测录制热键...")
    log_ranges = collect_log_ranges(log_dir)
    if not log_ranges:
        print("错误: 日志文件中未找到有效时间戳")
        sys.exit(1)

    all_hotkeys: list[tuple[datetime, str, LogRange]] = []
    for lr in log_ranges:
        for ht, hname in lr.hotkeys:
            all_hotkeys.append((ht, hname, lr))
    all_hotkeys.sort(key=lambda x: x[0])

    print(f"共发现 {len(log_ranges)} 个日志文件、{len(all_hotkeys)} 个录制热键:")
    for lr in log_ranges:
        hk = ", ".join(f"{t.isoformat()}({n})" for t, n in lr.hotkeys) or "无"
        print(f"  {lr.path.name}: {lr.start} ~ {lr.end}  热键[{hk}]")
    print()

    # ---- 2. 遍历视频文件 ----
    result_dir.mkdir(parents=True, exist_ok=True)
    aligned_dir.mkdir(parents=True, exist_ok=True)

    video_files = sorted(video_dir.glob("*.mp4"))
    if not video_files:
        print("警告: 视频目录中没有 .mp4 文件")
        sys.exit(0)

    videos: list[tuple[Path, datetime, datetime]] = []
    for vp in video_files:
        vstart = parse_video_start_time(vp.name)
        if vstart is None:
            continue
        dur = get_video_duration(vp)
        vend = (vstart + timedelta(seconds=dur)) if dur is not None \
            else vstart + timedelta(hours=DEFAULT_MAX_VIDEO_HOURS)
        videos.append((vp, vstart, vend))

    print(f"共解析 {len(videos)} 个视频文件，正在按热键匹配...\n")

    # ---- 3. 热键 → 视频匹配（±2 秒，一个视频只匹配一次）----
    used: set[int] = set()
    cut_commands: list[str] = []
    cut_summary: list[tuple[str, str, str, str, str]] = []
    unmatched_hotkey: list[tuple[datetime, str, Path]] = []
    unmatched_video: list[Path] = []

    for ht, hname, lr in all_hotkeys:
        best = None
        best_diff = float("inf")
        for idx, (vp, vstart, vend) in enumerate(videos):
            if idx in used:
                continue
            diff = abs((vstart - ht).total_seconds())
            if diff <= TOLERANCE and diff < best_diff:
                best = (idx, vp, vstart, vend)
                best_diff = diff

        if best is None:
            unmatched_hotkey.append((ht, hname, lr.path))
            continue

        idx, vp, vstart, vend = best
        used.add(idx)

        # 对齐区间：起点=热键时间，终点=同日志下一个热键 / 日志结束 / 视频结束 的最小值
        seg_start = ht
        # 找同一日志里当前热键之后的下一个热键时间点（作为这段录制的自然结束点）
        next_hk = None
        for t, _ in lr.hotkeys:
            if t > ht:
                next_hk = t
                break
        seg_end = min(next_hk if next_hk else lr.end, lr.end, vend)
        if seg_end <= seg_start:
            continue

        name = aligned_name(seg_start)
        out_mp4 = aligned_dir / f"{name}.mp4"
        out_txt = aligned_dir / f"{name}.txt"

        offset = max(0.0, (seg_start - vstart).total_seconds())
        seg_dur = (seg_end - seg_start).total_seconds()

        cut_commands.append(build_ffmpeg_cut(
            vp.resolve(), offset, seg_dur, out_mp4.resolve(),
        ))
        trim_log(lr.path, seg_start, seg_end, out_txt)
        cut_summary.append((name, vp.name, lr.path.name, hname,
                            seg_start.isoformat(), seg_end.isoformat()))

    for idx, (vp, vstart, _) in enumerate(videos):
        if idx not in used:
            unmatched_video.append(vp)

    # ---- 4. 写汇总脚本（只生成，不运行）----
    script_path = result_dir / SCRIPT_FILENAME
    _write_script(script_path, cut_commands)

    # ---- 5. 输出汇总 ----
    print()
    print("=" * 60)
    print(f"对齐输出目录: {aligned_dir}")
    print(f"匹配对齐片段: {len(cut_summary)}")
    for name, vname, lname, hname, s, e in cut_summary:
        print(f"  {name}.mp4/.txt  <-  {vname} × {lname} ({hname})  [{s} ~ {e}]")
    print(f"ffmpeg 脚本（已生成，未运行）: {script_path}")
    if unmatched_hotkey:
        print(f"有热键但未找到视频 ({len(unmatched_hotkey)}):")
        for ht, hname, lpath in unmatched_hotkey:
            print(f"  {ht.isoformat()} ({hname})  <-  {lpath.name}")
    if unmatched_video:
        print(f"有视频但未匹配到热键 ({len(unmatched_video)}):")
        for vp in unmatched_video:
            print(f"  {vp.name}")
    print("完成!")


if __name__ == "__main__":
    main()
