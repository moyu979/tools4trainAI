# 03. 视频压缩存储

## 用途

对大码率原始视频进行压缩，节省存储空间。

- 码率 ≤ 50Mbps：直接复制
- 码率 > 50Mbps：HEVC 硬件编码压缩

## 使用方式

```bash
python video_processor.py
```

交互式输入源/缓存/目标目录，输出压缩后的视频文件 + 日志文件。

## 压缩参数

| 参数 | 值 |
|------|-----|
| 编码器 | `hevc_nvenc`（NVIDIA NVENC H.265） |
| 目标码率 | 45Mbps |
| 最大码率 | 70Mbps |
| 色深 | 10bit（p010le） |
| 音频 | 直接复制 |

## 依赖

- FFmpeg / FFprobe（系统安装，需支持 NVENC）
