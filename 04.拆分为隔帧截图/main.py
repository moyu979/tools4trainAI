#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════════════════
# AIGEN  ⚠️ 警告：本文件由 AI 生成，未经完整人工审查。
#        可能存在逻辑错误、边界问题或安全隐患，请在使用前仔细核对，切勿盲目信任。
# ═══════════════════════════════════════════════════════════════════════════
"""
视频隔帧截图工具（split.bat 的 Python 版本）

功能：使用 FFmpeg 将指定目录下的所有 MP4 视频文件按每秒 1 帧的频率
      提取截图（JPEG），每部视频的截图存放在独立的文件夹中。

输入：input_folder  - 源视频文件夹路径
      output_folder - 截图输出文件夹路径
输出：每个视频对应的截图文件夹，内含 frame_0001.jpg, frame_0002.jpg ...

依赖：系统需预装 FFmpeg 并添加到 PATH 环境变量。

用法（命令行模式）：
    python main.py
    python main.py --input C:/path/to/videos --output C:/path/to/screenshots
    python main.py -i videos -o shots --fps 2
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# 默认输入 / 输出路径（不传参时使用）
DEFAULT_INPUT_FOLDER = "videos"
DEFAULT_OUTPUT_FOLDER = "screenshots"

# 每秒钟截取的帧数
DEFAULT_FPS = 1

# 支持的视频扩展名
VIDEO_EXTENSIONS = {".mp4"}


def check_ffmpeg() -> Path:
    """检查 ffmpeg 是否可用。

    Returns:
        Path: ffmpeg 可执行文件的绝对路径。

    Raises:
        SystemExit: 未找到 ffmpeg 时退出程序。
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        print("错误：未找到 ffmpeg，请先安装 FFmpeg 并添加到系统 PATH 中。", file=sys.stderr)
        raise SystemExit(1)
    return Path(ffmpeg)


def extract_frames(ffmpeg: Path, video: Path, output_dir: Path, fps: float) -> bool:
    """使用 ffmpeg 从单个视频提取隔帧截图。

    Args:
        ffmpeg: ffmpeg 可执行文件路径。
        video: 源视频文件路径。
        output_dir: 截图输出目录（须已存在）。
        fps: 每秒钟提取的帧数。

    Returns:
        bool: 成功返回 True，失败返回 False。
    """
    # 注意：% 需要转义为 %% 以防 ffmpeg 将其解析为占位符
    out_pattern = str(output_dir / "frame_%04d.jpg")
    cmd = [str(ffmpeg), "-y", "-i", str(video), "-vf", f"fps={fps}", out_pattern]
    print(f"  执行: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  错误：ffmpeg 处理失败，退出码 {result.returncode}", file=sys.stderr)
            print(result.stderr.strip(), file=sys.stderr)
            return False
        return True
    except OSError as exc:
        print(f"  错误：无法运行 ffmpeg：{exc}", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将指定目录下所有 MP4 视频按每秒 1 帧提取 JPEG 截图。"
    )
    parser.add_argument(
        "-i", "--input", default=DEFAULT_INPUT_FOLDER,
        help=f"源视频文件夹路径（默认: {DEFAULT_INPUT_FOLDER}）",
    )
    parser.add_argument(
        "-o", "--output", default=DEFAULT_OUTPUT_FOLDER,
        help=f"截图输出文件夹路径（默认: {DEFAULT_OUTPUT_FOLDER}）",
    )
    parser.add_argument(
        "--fps", type=float, default=DEFAULT_FPS,
        help=f"每秒钟截取的帧数（默认: {DEFAULT_FPS}）",
    )
    args = parser.parse_args()

    input_folder = Path(args.input)
    output_folder = Path(args.output)

    if not input_folder.is_dir():
        print(f"错误：输入文件夹不存在：{input_folder}", file=sys.stderr)
        raise SystemExit(1)

    # 检查 ffmpeg 是否可用
    ffmpeg = check_ffmpeg()

    # 收集所有视频文件（按名称排序，保证处理顺序稳定）
    videos = sorted(
        p for p in input_folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not videos:
        print(f"未在 {input_folder} 中找到任何 {sorted(VIDEO_EXTENSIONS)} 视频文件。")
        return

    print(f"共发现 {len(videos)} 个视频文件，开始处理……")
    succeeded = 0
    skipped = 0
    failed = 0

    for video in videos:
        filename = video.stem
        screenshot_dir = output_folder / filename

        # 目标文件夹已存在则跳过
        if screenshot_dir.is_dir():
            print(f"跳过 {filename}，截图文件夹已存在。")
            skipped += 1
            continue

        # 创建目标文件夹
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        print(f"正在处理 {filename}……")

        if extract_frames(ffmpeg, video, screenshot_dir, args.fps):
            succeeded += 1
            print(f"  完成 {filename}")
        else:
            failed += 1

    print(
        f"所有视频已处理完成：成功 {succeeded} 个，跳过 {skipped} 个，失败 {failed} 个。"
    )


if __name__ == "__main__":
    main()
