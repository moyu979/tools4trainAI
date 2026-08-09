#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════════════════
# AIGEN  ⚠️ 警告：本文件由 AI 生成，未经完整人工审查。
#        可能存在逻辑错误、边界问题或安全隐患，请在使用前仔细核对，切勿盲目信任。
# ═══════════════════════════════════════════════════════════════════════════
"""
视频预处理脚本（03 压缩存储 + 04 训练转码 合并版）

两种模式：
  A. 压缩存储（原 03）：按码率分流
       - 码率 <= BITRATE_THRESHOLD_Mbps（默认 50M）直接复制（保持原扩展名）
       - 码率 >  阈值则用所选编码器压缩（保持原始分辨率，存档高质量参数，输出统一 .mp4）
       - 压缩失败时回退为直接复制原文件；可选缓存目录中转
  B. 训练转码（原 04）：全部转码
       - 缩放到高度 640（宽等比），输出 .mp4
       - 已存在目标文件则跳过（--force 可强制覆盖）
       - 按已编码时长百分比显示进度

共用能力：
  - 启动时扫描本机所有可用的 H.264/HEVC 编码器并让用户自选
    （默认优先级: NVENC -> AMF -> QSV -> VideoToolbox -> MF -> libx264/libx265）
  - 递归扫描源目录、保留相对目录结构、日志文件（同时输出到终端）
  - Ctrl+C 中断时终止当前编码并清理不完整文件
"""

import argparse
import datetime
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# 全局状态，用于 Ctrl+C 时终止当前编码并清理不完整文件
_current_process = None
_current_target_file = None

logger = logging.getLogger(__name__)

# ============================================================
# 支持的视频文件扩展名（03 ∪ 04）
# ============================================================
VIDEO_EXTENSIONS = {'.mov', '.mp4', '.mkv', '.avi', '.wmv', '.flv', '.webm', '.ts', '.m4v'}

# ============================================================
# 存档模式配置（原 03，保持现状）
# ============================================================
BITRATE_THRESHOLD_Mbps = 50   # 码率阈值（Mbps），超过此值需要压缩
BYTES_TO_MBIT = 1000000        # 字节 → Mbps（1 Mbps = 1,000,000 bps）
BYTES_TO_MB = 1024 * 1024     # 字节 → MB

# ============================================================
# 训练模式配置（原 04，保持现状）
# ============================================================
TRAIN_HEIGHT = 640            # 缩放目标高度（宽度等比）

# ============================================================
# 默认编码器优先级（从高到低），仅当用户未手动选择时使用
# ============================================================
DEFAULT_ENCODER_PRIORITY = [
    'hevc_nvenc', 'h264_nvenc',
    'hevc_amf', 'h264_amf',
    'hevc_qsv', 'h264_qsv',
    'hevc_videotoolbox', 'h264_videotoolbox',
    'hevc_mf', 'h264_mf',
    'libx265', 'libx264',
]

# ============================================================
# 存档模式压缩参数（高质量，保持 03 现状：目标 45M / 最大 70M / 缓冲 450M）
# NVENC 保留 10bit（main10 + p010le），AMF（RDNA4）同样给 10bit，
# 其余编码器用 8bit。若本机驱动/ffmpeg 不支持 10bit 会压缩失败并回退复制。
# ============================================================
ARCHIVE_ENCODER_PARAMS = {
    'hevc_nvenc': ['-rc', 'vbr_hq', '-cq', '15', '-b:v', '45M', '-maxrate', '70M',
                   '-bufsize', '450M', '-preset', 'p7', '-profile:v', 'main10', '-pix_fmt', 'p010le'],
    'h264_nvenc': ['-rc', 'vbr_hq', '-cq', '15', '-b:v', '45M', '-maxrate', '70M',
                   '-bufsize', '450M', '-preset', 'p7'],
    'hevc_amf': ['-rc', 'vbr_peak', '-b:v', '45M', '-maxrate', '70M', '-bufsize', '450M',
                 '-quality', 'quality', '-profile:v', 'main10', '-pix_fmt', 'p010le'],
    'h264_amf': ['-rc', 'vbr_peak', '-b:v', '45M', '-maxrate', '70M', '-bufsize', '450M',
                 '-quality', 'quality'],
    'hevc_qsv': ['-b:v', '45M', '-maxrate', '70M', '-bufsize', '450M', '-preset', 'veryslow'],
    'h264_qsv': ['-b:v', '45M', '-maxrate', '70M', '-bufsize', '450M', '-preset', 'veryslow'],
    'hevc_videotoolbox': ['-b:v', '45M', '-maxrate', '70M', '-bufsize', '450M'],
    'h264_videotoolbox': ['-b:v', '45M', '-maxrate', '70M', '-bufsize', '450M'],
    'hevc_mf': ['-b:v', '45M'],
    'h264_mf': ['-b:v', '45M'],
    'libx265': ['-preset', 'slow', '-b:v', '45M', '-maxrate', '70M', '-bufsize', '450M'],
    'libx264': ['-preset', 'slow', '-b:v', '45M', '-maxrate', '70M', '-bufsize', '450M'],
}

# ============================================================
# 训练模式压缩参数（原 04，保持现状：crf 23 / 2500k）
# ============================================================
TRAIN_ENCODER_PARAMS = {
    'h264_nvenc': ['-preset', 'p5', '-rc', 'vbr_hq', '-cq', '23'],
    'hevc_nvenc': ['-preset', 'p5', '-rc', 'vbr_hq', '-cq', '23'],
    'h264_amf': ['-quality', 'quality', '-b:v', '2500k'],
    'hevc_amf': ['-quality', 'quality', '-b:v', '2500k'],
    'h264_qsv': ['-global_quality', '23'],
    'hevc_qsv': ['-global_quality', '23'],
    'h264_videotoolbox': ['-b:v', '2500k'],
    'hevc_videotoolbox': ['-b:v', '2500k'],
    'h264_mf': ['-b:v', '2500k'],
    'hevc_mf': ['-b:v', '2500k'],
    'libx264': ['-preset', 'medium', '-crf', '23'],
    'libx265': ['-preset', 'medium', '-crf', '23'],
}


def setup_logging():
    """配置日志：输出到时间戳命名的日志文件 + 终端。

    Returns:
        Path: 日志文件路径。
    """
    now_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = Path(__file__).parent / f'video_processing_{now_str}.log'
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return log_file


def run_command(cmd):
    """运行 shell 命令并返回结果。

    Args:
        cmd (list[str]): 命令及其参数组成的列表。

    Returns:
        subprocess.CompletedProcess 或 subprocess.CalledProcessError: 命令成功时返回
        CompletedProcess 对象，失败时返回 CalledProcessError 对象。
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True,
                              encoding='utf-8', errors='replace')
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
    logger.info('捕获到 Ctrl+C，正在停止编码并清理...')
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
        logger.info(f'已删除不完整文件: {_current_target_file}')
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
            logger.error('无法执行 ffmpeg，请确认已安装 ffmpeg。')
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
        logger.error('未找到 ffmpeg，请确认已安装 ffmpeg。')
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                encoding='utf-8', errors='replace')
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
    logger.info('正在检测各编码器是否真正可用（实际编码一帧测试）...')
    available = []
    for i, enc in enumerate(candidates, 1):
        ok, reason = test_encoder(enc)
        status = '可用' if ok else '不可用'
        detail = f'（{reason}）' if reason else ''
        logger.info(f'  [{i}/{len(candidates)}] {enc} ({describe_encoder(enc)}) -> {status}{detail}')
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


def _strip_10bit_params(params):
    """从编码器参数列表中剥离 10bit 相关参数（-profile:v main10 / -pix_fmt p010le）。

    用于 10bit 编码失败时降级为 8bit 重试。参数表中 10bit 参数是成对的
    option+value（如 ['-profile:v', 'main10']），必须成对移除，
    不能只删值，否则 ffmpeg 会因缺少参数值而报错。

    Args:
        params (list[str]): 编码器参数列表。

    Returns:
        list[str]: 移除 10bit 参数后的新列表（不修改原列表）。
    """
    result = []
    i = 0
    while i < len(params):
        opt = params[i]
        if opt in ('-profile:v', '-pix_fmt'):
            i += 2  # 跳过 option 及其 value
            continue
        result.append(opt)
        i += 1
    return result


def build_ffmpeg_command(input_path, output_path, encoder, mode, force=False, retry_8bit=False):
    """构建 ffmpeg 编码命令。

    根据模式（archive/train）与编码器类型设置对应的编码参数：
      - archive：保持原始分辨率，使用存档高质量参数，音频复制；
        当 retry_8bit 为 True 时剥离 10bit 参数（用于失败降级重试）
      - train  ：缩放到高度 640（宽等比），使用训练参数，音频 aac 128k

    Args:
        input_path (Path): 源视频文件路径。
        output_path (Path): 目标输出文件路径。
        encoder (str): 编码器名称（如 hevc_nvenc, libx264 等）。
        mode (str): 'archive'（压缩存储）或 'train'（训练转码）。
        force (bool): 是否强制覆盖已存在的输出文件。默认为 False。
        retry_8bit (bool): 仅存档模式有效；为 True 时剥离 10bit 参数重试。默认为 False。

    Returns:
        list[str]: 构建完成的 ffmpeg 命令行参数列表。
    """
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error']
    if force:
        command += ['-y']

    command += ['-i', str(input_path)]

    if mode == 'archive':
        params = ARCHIVE_ENCODER_PARAMS.get(encoder, [])
        if retry_8bit:
            params = _strip_10bit_params(params)
    else:
        params = TRAIN_ENCODER_PARAMS.get(encoder, [])

    command += ['-c:v', encoder] + params

    if mode == 'archive':
        # 存档：保持原始分辨率，音频直接复制
        command += ['-c:a', 'copy', str(output_path)]
    else:
        # 训练：缩放到高度 640，宽度自动等比缩放
        vf = "scale='if(gte(iw,ih),640,-2)':'if(gte(iw,ih),-2,640)'"
        command += ['-vf', vf, '-c:a', 'aac', '-b:a', '128k', str(output_path)]
    return command


def build_output_path(source_root, target_root, source_file):
    """根据源文件路径生成目标输出路径（训练模式使用）。

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


class VideoProcessor:
    # ============================================================
    # 单位转换系数（保持 03 现状）
    # ============================================================
    BYTES_TO_MBIT = 1000000       # 字节 → Mbps（1 Mbps = 1,000,000 bps）
    BYTES_TO_MB = 1024 * 1024     # 字节 → MB

    def __init__(self, source_dir, cache_dir, target_dir, mode, encoder):
        """初始化视频处理器。

        Args:
            source_dir: 源视频文件所在目录路径。
            cache_dir: 缓存目录路径（仅存档模式使用），为 None 时不使用缓存。
            target_dir: 目标输出目录路径。
            mode: 'archive'（压缩存储）或 'train'（训练转码）。
            encoder: 选定的编码器名称。
        """
        self.mode = mode
        self.encoder = encoder
        self.source_dir = Path(source_dir)
        self.cache_dir = Path(cache_dir) if (cache_dir and mode == 'archive') else None
        self.target_dir = Path(target_dir)
        self.video_extensions = VIDEO_EXTENSIONS

        # 创建必要的目录（不自动创建缓存目录）
        self.target_dir.mkdir(parents=True, exist_ok=True)

        # 记录处理开始
        logger.info("=" * 60)
        logger.info("视频处理开始")
        logger.info(f"模式: {'压缩存储' if mode == 'archive' else '训练转码'}")
        logger.info(f"编码器: {encoder}")
        logger.info(f"源目录: {self.source_dir}")
        logger.info(f"缓存目录: {self.cache_dir if self.cache_dir else '不使用'}")
        logger.info(f"目标目录: {self.target_dir}")
        logger.info("=" * 60)

    def get_relative_path(self, file_path, base_dir):
        """获取文件相对于基础目录的相对路径。

        Args:
            file_path: 文件的完整路径。
            base_dir: 基础目录路径。

        Returns:
            Path: 相对路径对象。
        """
        return file_path.relative_to(base_dir)

    def probe_video(self, video_path):
        """探测视频文件的码率与时长（合并 03 码率探测 + 04 时长探测）。

        一次 ffprobe 调用同时返回视频码率（Mbps）和总时长（秒）。
        码率优先读取视频流 bit_rate 字段，缺失时用文件大小/时长估算。

        Args:
            video_path: 视频文件的路径。

        Returns:
            tuple[float, float]: (码率 Mbps, 时长秒)，探测失败时为 (0, 0)。
        """
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                str(video_path)
            ]

            # Windows 下默认控制台编码可能为 GBK，强制按 UTF-8 解码 ffprobe 的 JSON 输出，避免 UnicodeDecodeError
            result = subprocess.run(
                cmd,
                capture_output=True,
                check=True,
                encoding='utf-8',
                errors='replace'
            )
            data = json.loads(result.stdout)

            # 查找视频流
            video_stream = None
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    video_stream = stream
                    break

            # 时长
            format_info = data.get('format', {})
            duration = 0.0
            try:
                duration = float(format_info.get('duration', 0) or 0)
            except (TypeError, ValueError):
                duration = 0.0

            # 码率
            bitrate = 0.0
            if video_stream:
                bit_rate = video_stream.get('bit_rate')
                if bit_rate:
                    try:
                        bitrate = int(bit_rate) / self.BYTES_TO_MBIT
                    except (TypeError, ValueError):
                        bitrate = 0.0

            # 如果没有直接码率信息，尝试从文件大小和时长估算
            if bitrate <= 0 and duration > 0:
                file_size = 0
                try:
                    file_size = int(format_info.get('size', 0) or 0)
                except (TypeError, ValueError):
                    file_size = 0
                if file_size > 0:
                    bitrate = (file_size * 8) / (duration * self.BYTES_TO_MBIT)

            if not video_stream:
                logger.warning(f"未找到视频流: {video_path}")

            return bitrate, duration

        except subprocess.CalledProcessError as e:
            logger.error(f"FFprobe 执行失败: {e}")
            return 0, 0
        except Exception as e:
            logger.error(f"探测视频信息时出错: {e}")
            return 0, 0

    def run_ffmpeg(self, cmd, output_path, action_name):
        """执行 ffmpeg 编码/压缩命令，支持 Ctrl+C 清理不完整文件。

        Args:
            cmd: ffmpeg 命令行参数列表。
            output_path: 输出文件路径（用于 Ctrl+C 时清理）。
            action_name: 动作名称（用于日志，如 "压缩" / "转码"）。

        Returns:
            tuple[bool, float]: (是否成功, 耗时秒)。
        """
        global _current_process, _current_target_file

        _current_target_file = output_path
        start_time = time.time()
        try:
            _current_process = subprocess.Popen(cmd)
            _current_process.wait()
            elapsed = time.time() - start_time
            if _current_process.returncode != 0:
                logger.error(f"FFmpeg {action_name}失败 (返回码 {_current_process.returncode}) (耗时: {elapsed:.2f}秒)")
                return False, elapsed
            logger.info(f"{action_name}完成: {output_path} (耗时: {elapsed:.2f}秒)")
            return True, elapsed
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"{action_name}时出错: {e} (耗时: {elapsed:.2f}秒)")
            return False, elapsed
        finally:
            _current_process = None
            _current_target_file = None

    def _compress_with_retry(self, input_path, output_path):
        """压缩视频，若 10bit 参数失败自动降级 8bit 重试。

        先按存档参数（含 10bit）压缩；若编码器/驱动不支持 10bit 导致失败，
        清理残留文件后剥离 10bit 参数、以 8bit 重试一次；
        8bit 仍失败才返回失败（由调用方回退为复制原文件）。

        Args:
            input_path: 输入视频路径。
            output_path: 输出压缩视频路径。

        Returns:
            tuple[bool, float, bool]: (是否成功, 压缩总耗时秒, 是否成功使用 10bit)。
        """
        params = ARCHIVE_ENCODER_PARAMS.get(self.encoder, [])
        has_10bit = _strip_10bit_params(params) != params

        # 首次尝试（含 10bit 参数）
        cmd = build_ffmpeg_command(input_path, output_path, self.encoder, 'archive')
        logger.info(f"  开始压缩视频: {input_path}")
        success, comp_time = self.run_ffmpeg(cmd, output_path, '压缩')
        if success:
            return True, comp_time, True

        # 参数含 10bit 且首次失败 → 降级 8bit 重试一次
        if has_10bit:
            logger.warning(f"  10bit 压缩失败，降级为 8bit 重试: {input_path}")
            # 清理可能的失败残留文件，避免重试时 ffmpeg 报文件已存在
            if output_path.exists():
                output_path.unlink()
            cmd_8bit = build_ffmpeg_command(input_path, output_path, self.encoder, 'archive', retry_8bit=True)
            success, comp_time_8bit = self.run_ffmpeg(cmd_8bit, output_path, '压缩')
            comp_time += comp_time_8bit
            if success:
                return True, comp_time, False

        # 全部失败，清理残留文件（由调用方回退复制原文件）
        if output_path.exists():
            output_path.unlink()
        return False, comp_time, False

    def find_video_files(self, directory):
        """递归查找指定目录下所有受支持的视频文件。

        Args:
            directory: 要搜索的目录路径。

        Returns:
            list[Path]: 找到的视频文件路径列表。
        """
        directory = Path(directory)
        return [p for p in directory.rglob('*')
                if p.is_file() and p.suffix.lower() in self.video_extensions]

    def _process_archive(self, video_path):
        """存档模式：处理单个视频文件，根据码率决定直接复制或压缩。

        码率不超过 BITRATE_THRESHOLD_Mbps（默认 50Mbps）直接复制到目标目录；
        超过阈值先压缩（可选择使用缓存目录）再移至目标目录，
        压缩输出统一 .mp4（规避 AVI 等容器装 HEVC 的兼容性问题）。
        压缩失败时回退为直接复制原文件（保持原扩展名）。保持原始分辨率。

        Args:
            video_path: 视频文件的完整路径。

        Returns:
            bool: 处理成功返回 True，失败返回 False。
        """
        start_time = time.time()

        try:
            # 获取相对路径
            rel_path = self.get_relative_path(video_path, self.source_dir)

            # 复制目标（保持原始扩展名，用于直接复制/压缩失败回退复制）
            target_path = self.target_dir / rel_path
            # 压缩输出目标（统一 .mp4，规避 AVI 等容器装 HEVC 的兼容性问题）
            compress_target = target_path.with_suffix('.mp4') if target_path.suffix.lower() != '.mp4' else target_path
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # 检查码率
            original_bitrate, _ = self.probe_video(video_path)
            file_size_mb = video_path.stat().st_size / self.BYTES_TO_MB

            logger.info(f"处理文件: {video_path.name}")
            logger.info(f"  原始码率: {original_bitrate:.2f} Mbps")
            logger.info(f"  文件大小: {file_size_mb:.2f} MB")
            logger.info(f"  相对路径: {rel_path}")

            if original_bitrate <= BITRATE_THRESHOLD_Mbps:
                # 码率不超过阈值，直接复制
                logger.info(f"  操作: 直接复制 (码率未超过{BITRATE_THRESHOLD_Mbps}M)")
                shutil.copy2(str(video_path), str(target_path))
                logger.info(f"  结果: 复制完成")
                logger.info(f"  耗时: {time.time() - start_time:.2f}秒")
                logger.info(f"  最终码率: {original_bitrate:.2f} Mbps (未压缩)")
                logger.info("-" * 50)
                return True

            # 码率超过阈值，需要压缩
            logger.info(f"  操作: 需要压缩 (码率超过{BITRATE_THRESHOLD_Mbps}M)")

            if self.cache_dir is None or not self.cache_dir.exists():
                # 不使用缓存或缓存目录不存在，直接压缩到目标目录
                logger.info(f"  压缩方式: 直接压缩到目标目录")
                success, compression_time, used_10bit = self._compress_with_retry(video_path, compress_target)

                if success:
                    new_bitrate, _ = self.probe_video(compress_target)
                    new_size_mb = compress_target.stat().st_size / self.BYTES_TO_MB
                    bit_info = '10bit' if used_10bit else '8bit(自动降级)'
                    logger.info(f"  结果: 压缩完成 ({bit_info})")
                    logger.info(f"  压缩耗时: {compression_time:.2f}秒")
                    logger.info(f"  总耗时: {time.time() - start_time:.2f}秒")
                    logger.info(f"  最终码率: {new_bitrate:.2f} Mbps")
                    logger.info(f"  压缩后大小: {new_size_mb:.2f} MB")
                    logger.info(f"  压缩率: {(1 - new_size_mb / file_size_mb) * 100:.1f}%")
                else:
                    # 压缩失败，直接复制原文件
                    logger.warning(f"  结果: 压缩失败，复制原文件")
                    shutil.copy2(str(video_path), str(target_path))
                    logger.info(f"  总耗时: {time.time() - start_time:.2f}秒")
                    logger.info(f"  最终码率: {original_bitrate:.2f} Mbps (原文件)")
                    logger.info("-" * 50)
            else:
                # 使用缓存目录，使用缓存-移动流程
                cache_path = self.cache_dir / rel_path
                if cache_path.suffix.lower() != '.mp4':
                    cache_path = cache_path.with_suffix('.mp4')
                cache_path.parent.mkdir(parents=True, exist_ok=True)

                logger.info(f"  压缩方式: 缓存压缩")
                success, compression_time, used_10bit = self._compress_with_retry(video_path, cache_path)

                if success:
                    # 压缩成功，移动到目标文件夹
                    shutil.move(str(cache_path), str(compress_target))

                    new_bitrate, _ = self.probe_video(compress_target)
                    new_size_mb = compress_target.stat().st_size / self.BYTES_TO_MB
                    bit_info = '10bit' if used_10bit else '8bit(自动降级)'
                    logger.info(f"  结果: 压缩并移动完成 ({bit_info})")
                    logger.info(f"  压缩耗时: {compression_time:.2f}秒")
                    logger.info(f"  总耗时: {time.time() - start_time:.2f}秒")
                    logger.info(f"  最终码率: {new_bitrate:.2f} Mbps")
                    logger.info(f"  压缩后大小: {new_size_mb:.2f} MB")
                    logger.info(f"  压缩率: {(1 - new_size_mb / file_size_mb) * 100:.1f}%")
                else:
                    # 压缩失败，直接复制原文件
                    logger.warning(f"  结果: 压缩失败，复制原文件")
                    shutil.copy2(str(video_path), str(target_path))
                    logger.info(f"  总耗时: {time.time() - start_time:.2f}秒")
                    logger.info(f"  最终码率: {original_bitrate:.2f} Mbps (原文件)")

                logger.info("-" * 50)

            return True

        except Exception as e:
            logger.error(f"处理视频文件时出错 {video_path}: {e}")
            logger.error(f"出错耗时: {time.time() - start_time:.2f}秒")
            logger.info("-" * 50)
            return False

    def _process_train(self, source_file, force=False, source_duration=0.0):
        """训练模式：对单个视频文件执行转码。

        目标文件已存在且非 force 时跳过；转码为高度 640 等比例缩放的 .mp4。
        编码完成后输出编码速度比（编码用时 / 原视频时长）。

        Args:
            source_file: 源视频文件路径。
            force: 是否强制覆盖已存在的目标文件。默认为 False。
            source_duration: 源视频时长（秒），用于计算编码速度比。

        Returns:
            str: 转码结果，'done' 表示成功，'skipped' 表示已跳过，'failed' 表示失败。
        """
        target_file = build_output_path(self.source_dir, self.target_dir, source_file)

        if target_file.exists() and not force:
            src_dur_str = format_duration(source_duration) if source_duration > 0 else '?'
            logger.info(f'跳过：目标已存在 {target_file}（原视频时长 {src_dur_str}）')
            return 'skipped'

        target_file.parent.mkdir(parents=True, exist_ok=True)
        if force and target_file.exists():
            target_file.unlink()
            logger.info(f'强制覆盖目标文件: {target_file}')

        logger.info(f'开始编码: {source_file} -> {target_file}')
        cmd = build_ffmpeg_command(source_file, target_file, self.encoder, 'train', force=force)

        start_time = time.time()
        success, elapsed = self.run_ffmpeg(cmd, target_file, '转码')
        if not success:
            return 'failed'

        if source_duration > 0:
            ratio = elapsed / source_duration
            logger.info(f'完成: {target_file}（编码用时 {format_duration(elapsed)}，速度比 {ratio:.2f}x）')
        else:
            logger.info(f'完成: {target_file}（编码用时 {format_duration(elapsed)}）')
        return 'done'

    def process_video(self, video_path, force=False):
        """处理单个视频文件（按模式分发）。

        Args:
            video_path: 视频文件的完整路径。
            force: 是否强制覆盖（仅训练模式使用）。

        Returns:
            bool 或 str: 存档模式返回 bool，训练模式返回 'done'/'skipped'/'failed'。
        """
        if self.mode == 'archive':
            return self._process_archive(video_path)
        return self._process_train(video_path, force=force)

    def process_all_videos(self, force=False):
        """批量处理源目录中的所有视频文件。

        扫描源目录，遍历所有视频文件逐个处理，
        输出处理进度和最终统计信息。

        Args:
            force: 是否强制覆盖已存在的目标文件（仅训练模式使用）。
        """
        logger.info(f"开始扫描源目录: {self.source_dir}")
        video_files = self.find_video_files(self.source_dir)

        if not video_files:
            logger.info("未找到任何视频文件")
            return

        logger.info(f"找到 {len(video_files)} 个视频文件")

        # 训练模式：预扫描所有视频时长，用于按比例显示进度
        file_durations = {}
        total_duration = 0.0
        if self.mode == 'train':
            logger.info('正在统计总时长...')
            for source_file in sorted(video_files):
                _, dur = self.probe_video(source_file)
                file_durations[source_file] = dur
                total_duration += dur
            logger.info(f'总时长: {format_duration(total_duration)}')

        logger.info('开始处理...')

        success_count = 0
        skipped_count = 0
        failed_count = 0
        completed_duration = 0.0
        total = len(video_files)

        for i, video_file in enumerate(sorted(video_files), 1):
            logger.info(f"处理进度: {i}/{total} - {video_file.name}")

            if self.mode == 'archive':
                if self.process_video(video_file):
                    success_count += 1
                else:
                    failed_count += 1
                print(f'进度: {success_count + failed_count}/{total}')
            else:
                result = self.process_video(video_file, force=force)
                if result == 'done':
                    success_count += 1
                    completed_duration += file_durations.get(video_file, 0)
                elif result == 'skipped':
                    skipped_count += 1
                    completed_duration += file_durations.get(video_file, 0)
                else:
                    failed_count += 1

                if total_duration > 0:
                    pct = completed_duration / total_duration * 100
                    print(f'进度: {pct:.1f}% ({format_duration(completed_duration)} / {format_duration(total_duration)})')
                else:
                    print(f'进度: {success_count + skipped_count}/{total}')

        logger.info("=" * 60)
        if self.mode == 'archive':
            logger.info(f"处理完成！成功处理 {success_count}/{total} 个文件")
        else:
            logger.info(f"处理完成。成功: {success_count}，跳过: {skipped_count}，失败: {failed_count}")
        logger.info("=" * 60)


def main():
    """主函数：交互式视频预处理入口（压缩存储 / 训练转码）。"""
    parser = argparse.ArgumentParser(description='视频预处理工具（压缩存储 / 训练转码）')
    parser.add_argument('--force', action='store_true', help='[训练模式] 强制覆盖已存在的目标文件')
    args = parser.parse_args()

    # 配置日志（文件 + 终端）
    log_file = setup_logging()
    logger.info(f"日志文件: {log_file}")

    # 注册 Ctrl+C 信号处理器
    signal.signal(signal.SIGINT, signal_handler)

    print("=== 视频预处理工具 ===")
    print()

    # 选择处理模式
    print("请选择处理模式:")
    print("  [1] 压缩存储：按码率分流，高码率压缩存档（保持原始分辨率，可选缓存）")
    print("  [2] 训练转码：全部转码为高度 640 等比例缩放（用于 AI 训练）")
    while True:
        mode_choice = input("请输入模式编号 (1/2): ").strip()
        if mode_choice == '1':
            mode = 'archive'
            break
        elif mode_choice == '2':
            mode = 'train'
            break
        print("无效输入，请输入 1 或 2。")

    # 交互式输入路径
    source_input = strip_quotes(input("请输入源视频目录路径: "))
    source_dir = Path(source_input).expanduser().resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        print(f"错误: 源目录不存在: {source_dir}")
        sys.exit(1)

    # 询问缓存目录（仅存档模式，留空表示不使用）
    cache_dir = None
    if mode == 'archive':
        cache_input = strip_quotes(input("请输入缓存目录路径（留空表示不使用缓存）: "))
        cache_dir = cache_input if cache_input else None
    else:
        logger.info("训练模式不使用缓存目录")

    target_input = strip_quotes(input("请输入目标目录路径: "))
    if not target_input:
        print("错误: 目标目录不能为空")
        sys.exit(1)
    target_dir = Path(target_input).expanduser().resolve()

    # 扫描并检测可用编码器，让用户选择（直接回车用默认）
    candidates = list_available_encoders()
    available_encoders = filter_available_encoders(candidates)
    encoder, device_name = select_encoder(available_encoders)
    print(f"选择的编码设备: {device_name}，使用编码器: {encoder}")
    logger.info(f"选择的编码设备: {device_name}，使用编码器: {encoder}")

    print()
    print("=== 处理配置 ===")
    print(f"模式: {'压缩存储' if mode == 'archive' else '训练转码'}")
    print(f"源目录: {source_dir}")
    if mode == 'archive':
        print(f"缓存目录: {cache_dir if cache_dir else '不使用缓存'}")
    print(f"目标目录: {target_dir}")
    print(f"编码器: {encoder} ({device_name})")
    print()

    # 确认开始处理
    confirm = input("确认开始处理？(y/n): ").strip().lower()
    if confirm not in ['y', 'yes', '是']:
        print("已取消处理")
        sys.exit(0)

    processor = VideoProcessor(source_dir, cache_dir, target_dir, mode, encoder)
    processor.process_all_videos(force=args.force)


if __name__ == "__main__":
    main()
