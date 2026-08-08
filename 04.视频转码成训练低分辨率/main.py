#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════════════════
# AIGEN  ⚠️ 警告：本文件由 AI 生成，未经完整人工审查。
#        可能存在逻辑错误、边界问题或安全隐患，请在使用前仔细核对，切勿盲目信任。
# ═══════════════════════════════════════════════════════════════════════════
"""
视频转码脚本
输入：源路径和目标路径
功能：启动时扫描本机所有可用的 H.264/HEVC 编码器并让用户自选
（用户不选择时按默认优先级自动挑选），遍历源路径所有视频文件，
按高度640等比例缩放后编码。已存在目标文件则跳过。
"""

import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

# 全局状态，用于 Ctrl+C 时终止当前编码并清理不完整文件
_current_process = None
_current_target_file = None

VIDEO_EXTENSIONS = {'.mov', '.mp4', '.mkv', '.avi', '.wmv', '.flv', '.webm', '.ts', '.m4v'}

# 默认编码器优先级（从高到低），仅当用户未手动选择时使用
DEFAULT_ENCODER_PRIORITY = [
    'h264_nvenc', 'hevc_nvenc',
    'h264_amf', 'hevc_amf',
    'h264_qsv', 'hevc_qsv',
    'h264_videotoolbox', 'hevc_videotoolbox',
    'h264_mf', 'hevc_mf',
    'libx264',
]


def run_command(cmd):
    """运行 shell 命令并返回结果。

    Args:
        cmd (list[str]): 命令及其参数组成的列表。

    Returns:
        subprocess.CompletedProcess 或 subprocess.CalledProcessError: 命令成功时返回
        CompletedProcess 对象，失败时返回 CalledProcessError 对象。
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        return exc


def signal_handler(sig, frame):
    """处理 Ctrl+C 信号。

    终止当前正在运行的编码进程，并删除对应的不完整目标文件。

    Args:
        sig (int): 信号编号。
        frame (types.FrameType | None): 当前执行栈帧。
    """
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


def list_available_encoders():
    """扫描 ffmpeg 编译支持的 H.264/HEVC 编码器候选。

    运行 ``ffmpeg -encoders`` 并解析输出，收集所有硬件编码器
    （如 h264_nvenc、hevc_amf 等）以及软件编码器 libx264/libx265。
    注意：此处列出的只是编译进 ffmpeg 的候选编码器，不代表本机硬件
    一定可用（如无 NVIDIA 显卡时 h264_nvenc 仍会被列出），真正的
    可用性需要由 filter_available_encoders 实际编码测试确认。

    Returns:
        list[str]: 候选编码器名称列表；无法执行 ffmpeg 时返回空列表。

    Raises:
        SystemExit: 当未找到 ffmpeg 可执行文件时退出程序。
    """
    try:
        result = run_command(['ffmpeg', '-hide_banner', '-encoders'])
        if isinstance(result, subprocess.CalledProcessError):
            print('错误: 无法执行 ffmpeg，请确认已安装 ffmpeg。')
            return []

        available = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                name = parts[1]
                # 收集所有 h264_*/hevc_* 硬件编码器及 libx264/libx265
                if name.startswith(('h264_', 'hevc_')) or name in ('libx264', 'libx265'):
                    available.add(name)
        return sorted(available)
    except FileNotFoundError:
        print('错误: 未找到 ffmpeg，可通过 Homebrew 或其他方式安装。')
        sys.exit(1)


def describe_encoder(encoder: str) -> str:
    """返回编码器的人类可读描述文本。

    Args:
        encoder (str): 编码器名称。

    Returns:
        str: 编码器的描述文本，未知编码器时返回编码器名称本身。
    """
    descriptions = {
        'h264_nvenc': 'NVIDIA NVENC (H.264)',
        'hevc_nvenc': 'NVIDIA NVENC (HEVC)',
        'h264_amf': 'AMD AMF (H.264)',
        'hevc_amf': 'AMD AMF (HEVC)',
        'h264_qsv': 'Intel QSV (H.264)',
        'hevc_qsv': 'Intel QSV (HEVC)',
        'h264_videotoolbox': 'Apple VideoToolbox (H.264)',
        'hevc_videotoolbox': 'Apple VideoToolbox (HEVC)',
        'h264_mf': 'Windows Media Foundation (H.264)',
        'hevc_mf': 'Windows Media Foundation (HEVC)',
        'libx264': 'CPU: libx264',
        'libx265': 'CPU: libx265',
    }
    return descriptions.get(encoder, encoder)


def test_encoder(encoder: str):
    """实际测试编码器能否真正工作。

    ``ffmpeg -encoders`` 列出的只是编译进 ffmpeg 的编码器，并不代表
    本机硬件可用（例如没有 NVIDIA 显卡时 h264_nvenc 会在加载
    nvcuda.dll 时失败）。该函数通过实际编码一帧测试图来判断编码器
    能否成功打开并输出数据，失败时提取 ffmpeg 报错原因。

    Args:
        encoder (str): 编码器名称。

    Returns:
        tuple[bool, str]: (编码器是否可用, 不可用时的失败原因或空字符串)。
    """
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
        # 用 640x360 而非过小的尺寸：部分硬件编码器（如 AMF 的 HEVC）
        # 对低于最小分辨率要求的输入会拒绝初始化，导致误判为不可用；
        # 640 高度也与真实转码输出一致，最接近实际场景
        '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=1',
        '-frames:v', '1',
        '-c:v', encoder,
        '-f', 'null', '-',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, ''
        lines = [ln.strip() for ln in result.stderr.splitlines() if ln.strip()]
        # 优先提取包含关键错误信息的一行（如 Cannot load / failed / Could not）
        keywords = ('cannot load', 'failed', 'could not', 'error while', 'not supported', 'not found')
        reason = next((ln for ln in lines if any(k in ln.lower() for k in keywords)), None)
        if reason is None and lines:
            reason = lines[-1]
        if reason is None:
            reason = f'ffmpeg 返回码 {result.returncode}'
        return False, reason
    except subprocess.TimeoutExpired:
        return False, '测试超时'
    except OSError as exc:
        return False, str(exc)


def filter_available_encoders(candidates):
    """从候选编码器中过滤出真正可用的编码器。

    逐个对候选编码器进行实际编码测试，剔除本机无法使用的编码器
    （如无对应硬件、驱动不可用或加载失败），并输出检测进度与
    不可用原因。

    Args:
        candidates (list[str]): 候选编码器名称列表。

    Returns:
        list[str]: 测试通过的可用编码器列表。
    """
    if not candidates:
        return []
    print('正在检测各编码器是否真正可用（实际编码一帧测试）...')
    available = []
    for i, enc in enumerate(candidates, 1):
        ok, reason = test_encoder(enc)
        status = '可用' if ok else '不可用'
        detail = f'（{reason}）' if reason else ''
        print(f'  [{i}/{len(candidates)}] {enc} ({describe_encoder(enc)}) -> {status}{detail}')
        if ok:
            available.append(enc)
    return available


def pick_default_encoder(available_encoders):
    """根据默认优先级挑选一个默认编码器。

    Args:
        available_encoders (list[str]): 可用编码器列表。

    Returns:
        tuple[str, str]: (编码器名称, 设备描述)。列表为空时回退到 libx264。
    """
    if not available_encoders:
        return 'libx264', 'CPU: libx264'
    for enc in DEFAULT_ENCODER_PRIORITY:
        if enc in available_encoders:
            return enc, describe_encoder(enc)
    # 可用列表中没有默认优先级中的项，取列表第一个
    enc = available_encoders[0]
    return enc, describe_encoder(enc)


def select_encoder(available_encoders):
    """让用户从可用编码器列表中自选，用户不选择时使用默认编码器。

    列出所有可用编码器并提示用户输入编号；直接回车时按默认优先级
    自动挑选（优先硬件编码器，回退 libx264）。

    Args:
        available_encoders (list[str]): 扫描到的可用编码器列表。

    Returns:
        tuple[str, str]: (编码器名称, 设备描述)。
    """
    if not available_encoders:
        print('未检测到可用编码器，将使用 CPU 软件编码 libx264。')
        return 'libx264', 'CPU: libx264'

    default_encoder, default_desc = pick_default_encoder(available_encoders)

    print('检测到以下可用编码器:')
    for i, enc in enumerate(available_encoders, 1):
        print(f'  [{i}] {enc} ({describe_encoder(enc)})')
    print(f'直接回车将使用默认编码器: {default_encoder} ({default_desc})')

    while True:
        choice = input(f'请选择编码器编号 (1-{len(available_encoders)})，直接回车使用默认: ').strip()
        if not choice:
            print(f'未手动选择，使用默认编码器: {default_encoder}')
            return default_encoder, default_desc
        try:
            idx = int(choice)
            if 1 <= idx <= len(available_encoders):
                enc = available_encoders[idx - 1]
                return enc, describe_encoder(enc)
            print(f'无效编号，请输入 1-{len(available_encoders)} 之间的数字。')
        except ValueError:
            print('请输入有效的数字编号，或直接回车使用默认。')


def build_ffmpeg_command(input_path: Path, output_path: Path, encoder: str, force: bool = False):
    """构建 ffmpeg 编码命令。

    根据编码器类型设置对应的编码参数（码率、质量等），
    并应用缩放滤镜（高度 640，宽度自动等比缩放）。

    Args:
        input_path (Path): 源视频文件路径。
        output_path (Path): 目标输出文件路径，非 .mp4 后缀会自动转换。
        encoder (str): 编码器名称（如 h264_nvenc, libx264 等）。
        force (bool): 是否强制覆盖已存在的输出文件。默认为 False。

    Returns:
        list[str]: 构建完成的 ffmpeg 命令行参数列表。
    """
    if output_path.suffix.lower() != '.mp4':
        output_path = output_path.with_suffix('.mp4')

    vf = "scale='if(gte(iw,ih),640,-2)':'if(gte(iw,ih),-2,640)'"
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
    elif encoder in ('h264_mf', 'hevc_mf'):
        command += ['-c:v', encoder, '-b:v', '2500k']
    elif encoder == 'libx265':
        command += ['-c:v', 'libx265', '-preset', 'medium', '-crf', '23']
    elif encoder == 'libx264':
        command += ['-c:v', 'libx264', '-preset', 'medium', '-crf', '23']
    else:
        # 其他硬件编码器（如 h264_mf、h264_vaapi 等），仅指定编码器
        command += ['-c:v', encoder]

    command += ['-vf', vf, '-c:a', 'aac', '-b:a', '128k', str(output_path)]
    return command


def is_video_file(path: Path):
    """判断给定路径是否为受支持的视频文件。

    Args:
        path (Path): 待检查的文件路径。

    Returns:
        bool: 如果是文件且后缀属于 VIDEO_EXTENSIONS 则返回 True，否则返回 False。
    """
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def find_video_files(directory: Path):
    """递归查找目录下所有受支持的视频文件。

    Args:
        directory (Path): 要搜索的根目录。

    Returns:
        list[Path]: 所有匹配的视频文件路径列表。
    """
    return [p for p in directory.rglob('*') if is_video_file(p)]


def build_output_path(source_root: Path, target_root: Path, source_file: Path):
    """根据源文件路径生成目标输出路径。

    保持源目录的相对目录结构，并将输出文件后缀统一为 .mp4。

    Args:
        source_root (Path): 源目录的根路径。
        target_root (Path): 目标输出目录的根路径。
        source_file (Path): 源视频文件的完整路径。

    Returns:
        Path: 目标输出文件的完整路径。
    """
    rel = source_file.relative_to(source_root)
    out_path = target_root / rel
    if out_path.suffix.lower() != '.mp4':
        out_path = out_path.with_suffix('.mp4')
    return out_path


def get_video_duration(file_path: Path) -> float:
    """获取视频文件的时长。

    通过 ffprobe 读取视频文件时长信息，失败时返回 0。

    Args:
        file_path (Path): 视频文件路径。

    Returns:
        float: 视频时长（秒），获取失败时返回 0.0。
    """
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
    """将秒数格式化为可读的时间字符串。

    时长不足一小时时省略小时部分，格式为 M:SS。

    Args:
        seconds (float): 以秒为单位的时间长度。

    Returns:
        str: 格式化后的时间字符串，格式为 H:MM:SS 或 M:SS。
    """
    h, remainder = divmod(int(seconds), 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f'{h}:{m:02d}:{s:02d}'
    else:
        return f'{m}:{s:02d}'


def strip_quotes(raw: str) -> str:
    """去除路径输入两侧可能携带的引号。

    从终端或资源管理器复制路径时经常带有单引号或双引号
    （如 'd:\\foo\\bar' 或 "d:\\foo\\bar"），这些引号会让路径
    无法被正确解析为绝对路径。该函数会移除两侧成对的引号。

    Args:
        raw (str): 用户输入的原始路径字符串。

    Returns:
        str: 去除两侧引号后的路径字符串。
    """
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        return raw[1:-1]
    return raw


def transcode_file(source_file: Path, target_file: Path, encoder: str, source_duration: float = 0.0, force: bool = False):
    """对单个视频文件执行转码操作。

    检查目标文件是否存在（skipped 逻辑），创建父目录，调用 ffmpeg 进行实际编码。
    编码过程中会更新全局状态以便 Ctrl+C 时清理不完整文件。
    编码完成后输出编码速度比（编码用时 / 原视频时长）。

    Args:
        source_file (Path): 源视频文件路径。
        target_file (Path): 目标输出文件路径。
        encoder (str): 编码器名称。
        source_duration (float): 源视频时长（秒），用于计算编码速度比。
        force (bool): 是否强制覆盖已存在的目标文件。默认为 False。

    Returns:
        str: 转码结果，'done' 表示成功，'skipped' 表示已跳过，'failed' 表示失败。
    """
    global _current_process, _current_target_file

    if target_file.exists() and not force:
        src_dur_str = format_duration(source_duration) if source_duration > 0 else '?'
        print(f'跳过：目标已存在 {target_file}（原视频时长 {src_dur_str}）')
        return 'skipped'

    target_file.parent.mkdir(parents=True, exist_ok=True)
    if force and target_file.exists():
        target_file.unlink()
        print(f'强制覆盖目标文件: {target_file}')

    print(f'开始编码: {source_file} -> {target_file}')
    cmd = build_ffmpeg_command(source_file, target_file, encoder, force=force)

    # 记录当前目标文件，以便 Ctrl+C 时清理
    _current_target_file = target_file

    start_time = time.time()
    try:
        _current_process = subprocess.Popen(cmd)
        _current_process.wait()
        elapsed = time.time() - start_time
        if _current_process.returncode != 0:
            print(f'错误: 编码失败 {source_file}，ffmpeg 返回码 {_current_process.returncode}')
            return 'failed'

        # 输出编码速度比
        if source_duration > 0:
            ratio = elapsed / source_duration
            print(f'完成: {target_file}（编码用时 {format_duration(elapsed)}，速度比 {ratio:.2f}x）')
        else:
            print(f'完成: {target_file}（编码用时 {format_duration(elapsed)}）')
        return 'done'
    except Exception as exc:
        elapsed = time.time() - start_time
        print(f'错误: 编码过程中出现异常 {source_file}，{exc}')
        return 'failed'
    finally:
        _current_process = None
        _current_target_file = None


def main():
    """主函数：批量转码视频文件。

    流程：
    1. 通过命令行交互获取源目录和目标目录。
    2. 检测可用的硬件编码器。
    3. 递归扫描源目录下所有视频文件，统计总时长。
    4. 逐个文件执行转码，输出进度信息。
    5. 汇总并打印成功、跳过和失败的文件数量。
    """
    parser = argparse.ArgumentParser(description='批量转码视频到高度 640，按比例缩放，并自动选择可用编码器。')
    parser.add_argument('--force', action='store_true', help='强制覆盖已有目标文件')
    args = parser.parse_args()

    source_input = strip_quotes(input('请输入源视频目录路径: '))
    target_input = strip_quotes(input('请输入目标输出目录路径: '))

    source_root = Path(source_input).expanduser().resolve()
    target_root = Path(target_input).expanduser().resolve()

    if not source_root.exists() or not source_root.is_dir():
        print(f'错误: 源目录不存在或不是目录: {source_root}')
        sys.exit(1)

    target_root.mkdir(parents=True, exist_ok=True)

    # 注册 Ctrl+C 信号处理器
    signal.signal(signal.SIGINT, signal_handler)

    # 扫描 ffmpeg 编译支持的候选编码器，并实际测试过滤出真正可用的
    candidates = list_available_encoders()
    available_encoders = filter_available_encoders(candidates)
    encoder, device_name = select_encoder(available_encoders)
    print(f'选择的编码设备: {device_name}，使用编码器: {encoder}')

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
        result = transcode_file(source_file, target_file, encoder, source_duration=file_durations.get(source_file, 0.0), force=args.force)
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
