#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频预处理脚本
功能：根据码率对视频文件进行分类处理
- 码率 <= BITRATE_THRESHOLD_Mbps（默认 50M）：直接移动到目标文件夹
- 码率 > BITRATE_THRESHOLD_Mbps（默认 50M）：先压缩再移动到目标文件夹

配置说明：
所有可调参数均以类常量形式定义在 VideoProcessor 中，
包括码率阈值、NVENC 压缩参数（目标码率、最大码率、缓冲区大小等），
修改配置时无需深入业务逻辑代码。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
import logging
import datetime

class VideoProcessor:
    # ============================================================
    # 码率阈值（Mbps）- 超过此值需要压缩
    # ============================================================
    BITRATE_THRESHOLD_Mbps = 50
    
    # ============================================================
    # NVENC H.265 压缩参数（C++ 宏风格常量）
    # ============================================================
    NVENC_TARGET_BITRATE  = '45M'    # 目标码率
    NVENC_MAX_BITRATE     = '70M'    # 最大码率
    NVENC_BUFFER_SIZE     = '450M'   # VBV 缓冲区大小
    NVENC_CQ              = '15'     # 恒定质量值（越低质量越好）
    NVENC_PRESET          = 'p7'     # 编码预设（p1 最快 ~ p7 最佳画质）
    NVENC_PROFILE         = 'main10' # H.265 编码配置文件（10bit 色深）
    NVENC_PIX_FMT         = 'p010le' # 像素格式（10bit 4:2:0）
    NVENC_RC              = 'vbr_hq' # 码率控制模式（高质量 VBR）
    
    # ============================================================
    # 单位转换系数
    # ============================================================
    BYTES_TO_MBIT = 1000000       # 字节 → Mbps（1 Mbps = 1,000,000 bps）
    BYTES_TO_MB   = 1024 * 1024  # 字节 → MB
    
    # ============================================================
    # 支持的视频文件扩展名
    # ============================================================
    VIDEO_EXTENSIONS = {'.mov', '.mp4', '.mkv', '.avi', '.wmv', '.flv', '.webm'}
    
    def __init__(self, source_dir, cache_dir, target_dir):
        """初始化视频处理器。

        创建目标目录，配置日志系统（同时输出到文件和终端），
        记录处理配置信息。

        Args:
            source_dir: 源视频文件所在目录路径。
            cache_dir: 缓存目录路径（压缩临时存储），为 None 时不使用缓存。
            target_dir: 目标输出目录路径。
        """
        self.source_dir = Path(source_dir)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.target_dir = Path(target_dir)
        
        # 创建必要的目录（不自动创建缓存目录）
        self.target_dir.mkdir(parents=True, exist_ok=True)
        
        # 设置日志，文件名包含运行时间
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
        self.logger = logging.getLogger(__name__)
        
        # 记录处理开始
        self.logger.info("=" * 60)
        self.logger.info("视频处理开始")
        self.logger.info(f"源目录: {self.source_dir}")
        self.logger.info(f"缓存目录: {self.cache_dir if self.cache_dir else '不使用'}")
        self.logger.info(f"目标目录: {self.target_dir}")
        self.logger.info("=" * 60)
        
        # 支持的视频格式（引用类常量）
        self.video_extensions = self.VIDEO_EXTENSIONS
    
    def get_video_bitrate(self, video_path):
        """获取视频文件的码率（单位：Mbps）。

        优先从 ffprobe 返回的视频流信息中直接读取码率，
        若无直接码率字段，则通过文件大小和时长进行估算。

        Args:
            video_path: 视频文件的路径。

        Returns:
            float: 视频码率（Mbps），获取失败时返回 0。
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
            import json
            data = json.loads(result.stdout)
            
            # 查找视频流
            video_stream = None
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    video_stream = stream
                    break
            
            if not video_stream:
                self.logger.warning(f"未找到视频流: {video_path}")
                return 0
            
            # 获取码率
            bitrate = video_stream.get('bit_rate')
            if bitrate:
                # 转换为 Mbps（使用类常量）
                bitrate_mbps = int(bitrate) / self.BYTES_TO_MBIT
                return bitrate_mbps
            
            # 如果没有直接码率信息，尝试从文件大小和时长估算
            format_info = data.get('format', {})
            duration = float(format_info.get('duration', 0))
            file_size = int(format_info.get('size', 0))
            
            if duration > 0 and file_size > 0:
                # 估算码率 (文件大小 * 8 / 时长 / BYTES_TO_MBIT)
                estimated_bitrate = (file_size * 8) / (duration * self.BYTES_TO_MBIT)
                return estimated_bitrate
            
            return 0
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"FFprobe 执行失败: {e}")
            return 0
        except Exception as e:
            self.logger.error(f"获取码率时出错: {e}")
            return 0
    
    def compress_video(self, input_path, output_path):
        """使用 FFmpeg 进行硬件加速视频压缩。

        使用 hevc_nvenc 编码器（NVENC H.265）进行 VBR 压缩，
        压缩参数由类常量控制（参见 NVENC_* 系列常量）：
        - 目标码率: NVENC_TARGET_BITRATE（默认 45M）
        - 最大码率: NVENC_MAX_BITRATE（默认 70M）
        - 缓冲区大小: NVENC_BUFFER_SIZE（默认 450M）
        - 编码预设: NVENC_PRESET（默认 p7）
        - 配置文件: NVENC_PROFILE（默认 main10, 10bit 色深）

        Args:
            input_path: 输入视频文件路径。
            output_path: 输出压缩视频文件路径。

        Returns:
            tuple[bool, float]: (是否成功, 压缩耗时（秒）)。
        """
        import time
        start_time = time.time()
        
        try:
            # cmd = [
            #     'ffmpeg',
            #     '-y',
            #     '-i', str(input_path),
            #     '-fflags', '+genpts',
            #     '-vsync', '1',
            #     '-avoid_negative_ts', 'make_zero',
            #     '-c:v', 'libx265',
            #     '-preset', 'slow',
            #     '-b:v', '45M',
            #     '-minrate', '40M',
            #     '-maxrate', '70M',
            #     '-bufsize', '420M',
            #     '-x265-params', 'no-opencl=1',
            #     '-c:a', 'copy',
            #     str(output_path)
            # ]
            cmd = [
                'ffmpeg',
                '-y',
                '-i', str(input_path),
                '-c:v', 'hevc_nvenc',
                '-rc', self.NVENC_RC,
                '-cq', self.NVENC_CQ,
                '-b:v', self.NVENC_TARGET_BITRATE,
                '-maxrate', self.NVENC_MAX_BITRATE,
                '-bufsize', self.NVENC_BUFFER_SIZE,
                '-preset', self.NVENC_PRESET,
                '-profile:v', self.NVENC_PROFILE,
                '-pix_fmt', self.NVENC_PIX_FMT,
                '-c:a', 'copy',
                str(output_path)
            ]

            self.logger.info(f"开始压缩视频: {input_path}")
            subprocess.run(cmd, check=True)
            
            end_time = time.time()
            compression_time = end_time - start_time
            self.logger.info(f"压缩完成: {output_path} (耗时: {compression_time:.2f}秒)")
            return True, compression_time
        except subprocess.CalledProcessError as e:
            end_time = time.time()
            compression_time = end_time - start_time
            self.logger.error(f"FFmpeg 压缩失败: {e} (耗时: {compression_time:.2f}秒)")
            return False, compression_time
        except Exception as e:
            end_time = time.time()
            compression_time = end_time - start_time
            self.logger.error(f"压缩视频时出错: {e} (耗时: {compression_time:.2f}秒)")
            return False, compression_time
    
    def get_relative_path(self, file_path, base_dir):
        """获取文件相对于基础目录的相对路径。

        Args:
            file_path: 文件的完整路径。
            base_dir: 基础目录路径。

        Returns:
            Path: 相对路径对象。
        """
        return file_path.relative_to(base_dir)
    
    def process_video(self, video_path):
        """处理单个视频文件：根据码率决定直接复制或压缩。

        如果视频码率不超过 BITRATE_THRESHOLD_Mbps（默认 50Mbps），直接复制到目标目录；
        如果超过 BITRATE_THRESHOLD_Mbps，先压缩（可选择使用缓存目录）再移至目标目录。
        压缩失败时回退为直接复制原文件。

        Args:
            video_path: 视频文件的完整路径。

        Returns:
            bool: 处理成功返回 True，失败返回 False。
        """
        import time
        start_time = time.time()
        
        try:
            # 获取相对路径
            rel_path = self.get_relative_path(video_path, self.source_dir)
            
            # 目标文件路径
            target_path = self.target_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 检查码率
            original_bitrate = self.get_video_bitrate(video_path)
            file_size_mb = video_path.stat().st_size / self.BYTES_TO_MB
            
            self.logger.info(f"处理文件: {video_path.name}")
            self.logger.info(f"  原始码率: {original_bitrate:.2f} Mbps")
            self.logger.info(f"  文件大小: {file_size_mb:.2f} MB")
            self.logger.info(f"  相对路径: {rel_path}")
            
            if original_bitrate <= self.BITRATE_THRESHOLD_Mbps:
                # 码率不超过阈值，直接复制
                self.logger.info(f"  操作: 直接复制 (码率未超过{self.BITRATE_THRESHOLD_Mbps}M)")
                shutil.copy2(str(video_path), str(target_path))
                
                end_time = time.time()
                process_time = end_time - start_time
                
                self.logger.info(f"  结果: 复制完成")
                self.logger.info(f"  耗时: {process_time:.2f}秒")
                self.logger.info(f"  最终码率: {original_bitrate:.2f} Mbps (未压缩)")
                self.logger.info("-" * 50)
                
            else:
                # 码率超过阈值，需要压缩
                self.logger.info(f"  操作: 需要压缩 (码率超过{self.BITRATE_THRESHOLD_Mbps}M)")
                
                # 检查是否使用缓存目录
                if self.cache_dir is None or not self.cache_dir.exists():
                    # 不使用缓存或缓存目录不存在，直接压缩到目标目录
                    self.logger.info(f"  压缩方式: 直接压缩到目标目录")
                    success, compression_time = self.compress_video(video_path, target_path)
                    
                    if success:
                        # 获取压缩后的码率
                        new_bitrate = self.get_video_bitrate(target_path)
                        new_size_mb = target_path.stat().st_size / (1024 * 1024)
                        
                        end_time = time.time()
                        total_time = end_time - start_time
                        
                        self.logger.info(f"  结果: 压缩完成")
                        self.logger.info(f"  压缩耗时: {compression_time:.2f}秒")
                        self.logger.info(f"  总耗时: {total_time:.2f}秒")
                        self.logger.info(f"  最终码率: {new_bitrate:.2f} Mbps")
                        self.logger.info(f"  压缩后大小: {new_size_mb:.2f} MB")
                        self.logger.info(f"  压缩率: {(1 - new_size_mb/file_size_mb)*100:.1f}%")
                    else:
                        # 压缩失败，直接复制原文件
                        self.logger.warning(f"  结果: 压缩失败，复制原文件")
                        shutil.copy2(str(video_path), str(target_path))
                        
                        end_time = time.time()
                        total_time = end_time - start_time
                        
                        self.logger.info(f"  总耗时: {total_time:.2f}秒")
                        self.logger.info(f"  最终码率: {original_bitrate:.2f} Mbps (原文件)")
                        self.logger.info("-" * 50)
                else:
                    # 使用缓存目录，使用缓存-移动流程
                    cache_path = self.cache_dir / rel_path
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    self.logger.info(f"  压缩方式: 缓存压缩")
                    success, compression_time = self.compress_video(video_path, cache_path)
                    
                    if success:
                        # 压缩成功，移动到目标文件夹
                        shutil.move(str(cache_path), str(target_path))
                        
                        # 获取压缩后的码率
                        new_bitrate = self.get_video_bitrate(target_path)
                        new_size_mb = target_path.stat().st_size / (1024 * 1024)
                        
                        end_time = time.time()
                        total_time = end_time - start_time
                        
                        self.logger.info(f"  结果: 压缩并移动完成")
                        self.logger.info(f"  压缩耗时: {compression_time:.2f}秒")
                        self.logger.info(f"  总耗时: {total_time:.2f}秒")
                        self.logger.info(f"  最终码率: {new_bitrate:.2f} Mbps")
                        self.logger.info(f"  压缩后大小: {new_size_mb:.2f} MB")
                        self.logger.info(f"  压缩率: {(1 - new_size_mb/file_size_mb)*100:.1f}%")
                    else:
                        # 压缩失败，直接复制原文件
                        self.logger.warning(f"  结果: 压缩失败，复制原文件")
                        shutil.copy2(str(video_path), str(target_path))
                        
                        end_time = time.time()
                        total_time = end_time - start_time
                        
                        self.logger.info(f"  总耗时: {total_time:.2f}秒")
                        self.logger.info(f"  最终码率: {original_bitrate:.2f} Mbps (原文件)")
                    
                    self.logger.info("-" * 50)
            
            return True
            
        except Exception as e:
            end_time = time.time()
            total_time = end_time - start_time
            self.logger.error(f"处理视频文件时出错 {video_path}: {e}")
            self.logger.error(f"出错耗时: {total_time:.2f}秒")
            self.logger.info("-" * 50)
            return False
    
    def find_video_files(self, directory):
        """递归查找指定目录下所有受支持的视频文件。

        Args:
            directory: 要搜索的目录路径。

        Returns:
            list[Path]: 找到的视频文件路径列表。
        """
        video_files = []
        for root, dirs, files in os.walk(directory):
            for file in files:
                if Path(file).suffix.lower() in self.video_extensions:
                    video_files.append(Path(root) / file)
        return video_files
    
    def process_all_videos(self):
        """批量处理源目录中的所有视频文件。

        扫描源目录，遍历所有视频文件逐个处理，
        输出处理进度和最终统计信息。
        """
        self.logger.info(f"开始扫描源目录: {self.source_dir}")
        video_files = self.find_video_files(self.source_dir)
        
        if not video_files:
            self.logger.info("未找到任何视频文件")
            return
        
        self.logger.info(f"找到 {len(video_files)} 个视频文件")
        
        success_count = 0
        for i, video_file in enumerate(video_files, 1):
            self.logger.info(f"处理进度: {i}/{len(video_files)} - {video_file.name}")
            if self.process_video(video_file):
                success_count += 1
        
        self.logger.info("=" * 60)
        self.logger.info(f"处理完成！成功处理 {success_count}/{len(video_files)} 个文件")
        self.logger.info("=" * 60)
    

def main():
    """主函数：交互式视频预处理入口。

    通过命令行交互获取源目录、缓存目录和目标目录路径，
    创建 VideoProcessor 实例并启动批量处理。
    支持以下功能：
    - 码率不超过 BITRATE_THRESHOLD_Mbps（默认 50Mbps）的视频直接复制
    - 码率超过 BITRATE_THRESHOLD_Mbps 的视频使用 NVENC H.265 压缩
    - 可选择使用缓存目录进行中转
    """
    print("=== 视频预处理工具 ===")
    print()
    
    # 交互式输入路径
    source_dir = input("请输入源视频目录路径: ").strip()
    if not source_dir:
        print("错误: 源目录不能为空")
        sys.exit(1)
    
    # 检查源目录是否存在
    if not os.path.exists(source_dir):
        print(f"错误: 源目录不存在: {source_dir}")
        sys.exit(1)
    
    # 询问缓存目录（留空表示不使用）
    cache_dir = input("请输入缓存目录路径（留空表示不使用缓存）: ").strip()
    if not cache_dir:
        cache_dir = None  # 不使用缓存
    
    target_dir = input("请输入目标目录路径: ").strip()
    if not target_dir:
        print("错误: 目标目录不能为空")
        sys.exit(1)
    
    print()
    print("=== 处理配置 ===")
    print(f"源目录: {source_dir}")
    print(f"缓存目录: {cache_dir if cache_dir else '不使用缓存'}")
    print(f"目标目录: {target_dir}")
    print()
    
    # 确认开始处理
    confirm = input("确认开始处理？(y/n): ").strip().lower()
    if confirm not in ['y', 'yes', '是']:
        print("已取消处理")
        sys.exit(0)
    
    processor = VideoProcessor(source_dir, cache_dir, target_dir)
    processor.process_all_videos()

if __name__ == "__main__":
    main()
