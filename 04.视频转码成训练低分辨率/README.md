# 04. 视频转码成训练低分辨率

## 用途

将视频批量转码为 AI 训练可用的低码率格式（高度 640 等比例缩放），自动检测并选用最优硬件编码器。

## 使用方式

```bash
python main.py
```

交互式输入源/目标目录，支持 Ctrl+C 中断清理，输出转码后的 `.mp4` 文件。

## 编码器检测优先级

```
NVIDIA NVENC → AMD AMF → Intel QSV → Apple VideoToolbox → CPU libx264
```

## 依赖

- FFmpeg（系统安装）
