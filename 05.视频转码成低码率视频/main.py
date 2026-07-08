#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频转码脚本
输入：源路径和目标路径
功能：判断本地显卡设备以选择编码器，遍历源路径所有视频文件，按高度640等比例缩放后编码。
已存在目标文件则跳过。
"""

import argparse
import signal
import subprocess
import sys
from pathlib import Path

# 全局状态，用于 Ctrl+C 时终止当前编码并清理不完整文件
_current_process = None
_current_target_file = None

VIDEO_EXTENSIONS = {'.mov', '.mp4', '.mkv', '.avi', '.wmv', '.flv', '.webm', '.ts', '.m4v'}


def run_command(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        return exc


def signal_handler(sig, frame):
    """处理 Ctrl+C：终止当前编码进程并删除不完整的目标文件。"""
    global _current_process, _current_target_file
    print('\n捕获到 Ctrl+C，正在停止编码并清理...')
    if _current_process is not None and _current_process.poll() is None:
        _current_process.terminate()
        try:
            _current_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _current_process.kill()
            _current_process.wait()
    if _current_target_file is not None and _current_target_file.exists():
        _current_target_file.unlink()
        print(f'已删除不完整文件: {_current_target_file}')
    sys.exit(0)


def detect_encoder():
    """检测本机 FFmpeg 可用的硬件编码器。"""
    try:
        result = run_command(['ffmpeg', '-hide_banner', '-encoders'])
        if isinstance(result, subprocess.CalledProcessError):
            print('错误: 无法执行 ffmpeg，请确认已安装 ffmpeg。')
            return 'libx264', 'CPU: libx264'

        encoders = result.stdout.lower()

        if 'h264_nvenc' in encoders:
            return 'h264_nvenc', 'NVIDIA NVENC'
        if 'hevc_nvenc' in encoders:
            return 'hevc_nvenc', 'NVIDIA NVENC'
        if 'h264_amf' in encoders:
            return 'h264_amf', 'AMD AMF'
        if 'hevc_amf' in encoders:
            return 'hevc_amf', 'AMD AMF'
        if 'h264_qsv' in encoders:
            return 'h264_qsv', 'Intel QSV'
        if 'hevc_qsv' in encoders:
            return 'hevc_qsv', 'Intel QSV'
        if 'h264_videotoolbox' in encoders:
            return 'h264_videotoolbox', 'Apple VideoToolbox'
        if 'hevc_videotoolbox' in encoders:
            return 'hevc_videotoolbox', 'Apple VideoToolbox'

        return 'libx264', 'CPU: libx264'
    except FileNotFoundError:
        print('错误: 未找到 ffmpeg，可通过 Homebrew 或其他方式安装。')
        sys.exit(1)


def build_ffmpeg_command(input_path: Path, output_path: Path, encoder: str, force: bool = False):
    """构建 ffmpeg 编码命令。"""
    if output_path.suffix.lower() != '.mp4':
        output_path = output_path.with_suffix('.mp4')

    vf = 'scale=-2:640'
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error']
    if force:
        command += ['-y']

    command += ['-i', str(input_path)]

    if encoder in ('h264_nvenc', 'hevc_nvenc'):
        command += ['-c:v', encoder, '-preset', 'p5', '-rc', 'vbr_hq', '-cq', '23']
    elif encoder in ('h264_amf', 'hevc_amf'):
        command += ['-c:v', encoder, '-quality', 'quality', '-b:v', '2500k']
    elif encoder in ('h264_qsv', 'hevc_qsv'):
        command += ['-c:v', encoder, '-global_quality', '23']
    elif encoder in ('h264_videotoolbox', 'hevc_videotoolbox'):
        command += ['-c:v', encoder, '-b:v', '2500k']
    else:
        command += ['-c:v', 'libx264', '-preset', 'medium', '-crf', '23']

    command += ['-vf', vf, '-c:a', 'aac', '-b:a', '128k', str(output_path)]
    return command


def is_video_file(path: Path):
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def find_video_files(directory: Path):
    return [p for p in directory.rglob('*') if is_video_file(p)]


def build_output_path(source_root: Path, target_root: Path, source_file: Path):
    rel = source_file.relative_to(source_root)
    out_path = target_root / rel
    if out_path.suffix.lower() != '.mp4':
        out_path = out_path.with_suffix('.mp4')
    return out_path


def get_video_duration(file_path: Path) -> float:
    """用 ffprobe 获取视频时长（秒），失败返回 0。"""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        str(file_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0.0


def format_duration(seconds: float) -> str:
    """将秒数格式化为 HH:MM:SS。"""
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f'{h}:{m:02d}:{s:02d}'
    else:
        return f'{m}:{s:02d}'


def transcode_file(source_file: Path, target_file: Path, encoder: str, force: bool = False):
    global _current_process, _current_target_file

    if target_file.exists() and not force:
        print(f'跳过：目标已存在 {target_file}')
        return 'skipped'

    target_file.parent.mkdir(parents=True, exist_ok=True)
    if force and target_file.exists():
        target_file.unlink()
        print(f'强制覆盖目标文件: {target_file}')

    print(f'开始编码: {source_file} -> {target_file}')
    cmd = build_ffmpeg_command(source_file, target_file, encoder, force=force)

    # 记录当前目标文件，以便 Ctrl+C 时清理
    _current_target_file = target_file

    try:
        _current_process = subprocess.Popen(cmd)
        _current_process.wait()
        if _current_process.returncode != 0:
            print(f'错误: 编码失败 {source_file}，ffmpeg 返回码 {_current_process.returncode}')
            return 'failed'
        print(f'完成: {target_file}')
        return 'done'
    except Exception as exc:
        print(f'错误: 编码过程中出现异常 {source_file}，{exc}')
        return 'failed'
    finally:
        _current_process = None
        _current_target_file = None


def main():
    parser = argparse.ArgumentParser(description='批量转码视频到高度 640，按比例缩放，并自动选择可用编码器。')
    parser.add_argument('--force', action='store_true', help='强制覆盖已有目标文件')
    args = parser.parse_args()

    source_input = input('请输入源视频目录路径: ').strip()
    target_input = input('请输入目标输出目录路径: ').strip()

    source_root = Path(source_input).expanduser().resolve()
    target_root = Path(target_input).expanduser().resolve()

    if not source_root.exists() or not source_root.is_dir():
        print(f'错误: 源目录不存在或不是目录: {source_root}')
        sys.exit(1)

    target_root.mkdir(parents=True, exist_ok=True)

    # 注册 Ctrl+C 信号处理器
    signal.signal(signal.SIGINT, signal_handler)

    encoder, device_name = detect_encoder()
    print(f'检测到编码设备: {device_name}，使用编码器: {encoder}')

    video_files = find_video_files(source_root)
    if not video_files:
        print('未在源目录中找到视频文件。')
        return

    print(f'共找到 {len(video_files)} 个视频文件，正在统计总时长...')

    # 预扫描所有视频时长
    file_durations = {}
    total_duration = 0.0
    for source_file in sorted(video_files):
        dur = get_video_duration(source_file)
        file_durations[source_file] = dur
        total_duration += dur

    print(f'总时长: {format_duration(total_duration)}')
    print('开始处理...')

    success = 0
    skipped = 0
    failed = 0
    completed_duration = 0.0

    for source_file in sorted(video_files):
        target_file = build_output_path(source_root, target_root, source_file)
        result = transcode_file(source_file, target_file, encoder, force=args.force)
        if result == 'done':
            success += 1
            completed_duration += file_durations.get(source_file, 0)
        elif result == 'skipped':
            skipped += 1
            completed_duration += file_durations.get(source_file, 0)
        else:
            failed += 1

        # 显示进度
        if total_duration > 0:
            pct = completed_duration / total_duration * 100
            print(f'进度: {pct:.1f}% ({format_duration(completed_duration)} / {format_duration(total_duration)})')
        else:
            done = success + skipped
            total = len(video_files)
            print(f'进度: {done}/{total}')

    print('处理完成。')
    print(f'成功: {success}，跳过: {skipped}，失败: {failed}')


if __name__ == '__main__':
    main()
