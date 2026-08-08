#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════════════════
# AIGEN  ⚠️ 警告：本文件由 AI 生成，未经完整人工审查。
#        可能存在逻辑错误、边界问题或安全隐患，请在使用前仔细核对，切勿盲目信任。
# ═══════════════════════════════════════════════════════════════════════════
"""
根据日志文件的时间范围，筛选并复制有对应记录的视频文件。

日志格式（txt）:
    [2026-05-30T20:22:54.425688] M MOVE x=803 y=482

视频文件名格式:
    Yuan Shen 原神 2026.05.28 - 22.09.59.03.mp4
    └──名称──┘ └───日期───┘ └───时间───┘

用法:
    python main.py <log_dir> <video_dir> <result_dir>
"""

from __future__ import annotations

import re
import sys
import json
import subprocess
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 日志解析
# ---------------------------------------------------------------------------

LOG_TS_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]")


def parse_log_timestamp(line: str) -> datetime | None:
    """从日志行头部提取 ISO 格式时间戳。

    从形如 "[2026-05-30T20:22:54.425688] M MOVE x=803 y=482" 的行中
    提取并解析方括号内的时间戳部分。

    Args:
        line: 日志文件中的一行文本。

    Returns:
        datetime | None: 解析成功返回 datetime 对象，失败返回 None。
    """
    m = LOG_TS_RE.match(line)
    if m:
        try:
            return datetime.fromisoformat(m.group(1))
        except ValueError:
            return None
    return None


def collect_log_ranges(log_dir: Path) -> list[tuple[datetime, datetime]]:
    """扫描日志目录，收集每个日志文件的时间区间。

    遍历指定目录下所有 .txt 文件，解析每行的时间戳，
    提取每个文件中最早和最晚的时间戳作为该文件的记录区间。

    Args:
        log_dir: 包含日志 .txt 文件的目录路径。

    Returns:
        list[tuple[datetime, datetime]]: 时间区间列表，每个元素为 (起始时间, 结束时间)，
        按起始时间升序排列。
    """
    ranges: list[tuple[datetime, datetime]] = []

    for txt_path in sorted(log_dir.glob("*.txt")):
        try:
            text = txt_path.read_text(encoding="utf-8")
        except Exception:
            continue

        file_min: datetime | None = None
        file_max: datetime | None = None

        for line in text.splitlines():
            ts = parse_log_timestamp(line)
            if ts is None:
                continue
            if ts.tzinfo is not None:
                ts = ts.replace(tzinfo=None)
            if file_min is None or ts < file_min:
                file_min = ts
            if file_max is None or ts > file_max:
                file_max = ts

        if file_min is not None and file_max is not None:
            ranges.append((file_min, file_max))

    return ranges


# ---------------------------------------------------------------------------
# 视频文件名解析
# ---------------------------------------------------------------------------

# 匹配形如: 任意名称 2026.05.28 - 22.09.59.03.mp4
# 时间尾部 ".03" 为厘秒（百分之一秒）
VIDEO_FNAME_RE = re.compile(
    r"^"
    r".+?"                          # 视频名称（非贪婪）
    r"(\d{4})\.(\d{2})\.(\d{2})"   # 日期: 2026.05.28
    r"\s*-\s*"                      # 分隔符
    r"(\d{2})\.(\d{2})\.(\d{2})"   # 时间: 22.09.59
    r"\.(\d{2})"                    # 厘秒: .03
    r"\.mp4$",
    re.IGNORECASE,
)


def parse_video_start_time(filename: str) -> datetime | None:
    """从视频文件名解析录制开始时间。

    支持文件名格式如 "Yuan Shen 原神 2026.05.28 - 22.09.59.03.mp4"，
    从文件名中提取日期、时间和厘秒信息。

    Args:
        filename: 视频文件名（不含路径）。

    Returns:
        datetime | None: 解析成功返回视频开始时间的 datetime 对象，失败返回 None。
    """
    m = VIDEO_FNAME_RE.match(filename)
    if not m:
        return None

    year, month, day = int(m[1]), int(m[2]), int(m[3])
    hour, minute, second = int(m[4]), int(m[5]), int(m[6])
    centisecond = int(m[7])  # 厘秒 (0-99)

    microsecond = centisecond * 10000  # 厘秒 → 微秒
    return datetime(year, month, day, hour, minute, second, microsecond)


# ---------------------------------------------------------------------------
# 视频时长获取（通过 ffprobe）
# ---------------------------------------------------------------------------

def get_video_duration(video_path: Path) -> float | None:
    """通过 ffprobe 获取视频文件的时长。

    使用 ffprobe 命令解析视频文件格式信息，提取时长字段。

    Args:
        video_path: 视频文件的完整路径。

    Returns:
        float | None: 视频时长（秒），获取失败时返回 None。
    """
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
# 主逻辑
# ---------------------------------------------------------------------------

DEFAULT_MAX_VIDEO_HOURS = 2  # 无法获取时长时的保守假设


def main() -> None:
    """主函数：将视频文件与日志文件进行时间匹配。

    流程：
    1. 从命令行参数获取日志目录、视频目录和结果目录。
    2. 扫描所有日志文件，提取每个文件的时间区间。
    3. 遍历所有视频文件，解析文件名中的开始时间并用 ffprobe 获取时长。
    4. 判断视频时段是否与任意日志区间有重叠，有则复制到结果目录。
    5. 输出匹配统计信息。
    """
    if len(sys.argv) != 4:
        print("用法: python main.py <log_dir> <video_dir> <result_dir>")
        print("示例: python main.py ./log ./video ./result")
        sys.exit(1)

    log_dir = Path(sys.argv[1])
    video_dir = Path(sys.argv[2])
    result_dir = Path(sys.argv[3])

    # ---- 检查目录 ----
    if not log_dir.is_dir():
        print(f"错误: 日志目录不存在 -> {log_dir}")
        sys.exit(1)
    if not video_dir.is_dir():
        print(f"错误: 视频目录不存在 -> {video_dir}")
        sys.exit(1)

    # ---- 1. 收集每个 log 文件的时间区间 ----
    print("正在扫描日志文件...")
    log_ranges = collect_log_ranges(log_dir)
    if not log_ranges:
        print("错误: 日志文件中未找到有效时间戳")
        sys.exit(1)

    print(f"共发现 {len(log_ranges)} 个日志文件，时间区间如下:")
    for s, e in log_ranges:
        print(f"  {s}  ~  {e}")
    print()

    # ---- 2. 遍历视频文件 ----
    result_dir.mkdir(parents=True, exist_ok=True)
    matched: list[Path] = []
    skipped: list[str] = []

    video_files = sorted(video_dir.glob("*.mp4"))
    if not video_files:
        print("警告: 视频目录中没有 .mp4 文件")
        sys.exit(0)

    print(f"共发现 {len(video_files)} 个视频文件，正在匹配...\n")

    for vp in video_files:
        video_start = parse_video_start_time(vp.name)
        if video_start is None:
            skipped.append(f"无法解析文件名: {vp.name}")
            continue

        # 获取视频时长
        duration = get_video_duration(vp)
        if duration is not None:
            video_end = video_start + timedelta(seconds=duration)
            duration_str = f"{duration:.1f}s"
        else:
            # ffprobe 不可用时保守估计
            video_end = video_start + timedelta(hours=DEFAULT_MAX_VIDEO_HOURS)
            duration_str = f"未知（默认 {DEFAULT_MAX_VIDEO_HOURS}h）"

        # 3. 判断视频时段是否与任意一个 log 文件区间有重合
        has_overlap = any(
            video_start <= log_end and video_end >= log_start
            for log_start, log_end in log_ranges
        )

        if has_overlap:
            shutil.copy2(vp, result_dir / vp.name)
            matched.append(vp)
            status = "✅ 匹配"
        else:
            status = "  跳过"

        print(f"  {status}  {vp.name}")
        print(f"         视频时段: {video_start} ~ {video_end}  (时长 {duration_str})")

    # ---- 输出汇总 ----
    print()
    print("=" * 60)
    print(f"结果目录: {result_dir}")
    print(f"匹配并复制: {len(matched)} / {len(video_files)} 个视频")
    if skipped:
        print(f"跳过的文件 ({len(skipped)}):")
        for s in skipped:
            print(f"  - {s}")
    print("完成!")


if __name__ == "__main__":
    main()
