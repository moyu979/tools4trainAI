# 02. 匹配视频与日志

## 用途

将录制的游戏视频与对应的操作日志进行时间匹配，筛选出有完整操作记录的视频片段。

## 使用方式

```bash
python main.py <log_dir> <video_dir> <result_dir>
```

- `log_dir`：日志 `.txt` 文件所在目录
- `video_dir`：视频文件所在目录
- `result_dir`：匹配成功的视频输出目录

## 匹配逻辑

1. 从日志 `.txt` 文件中提取最早和最晚的时间戳
2. 从视频文件名（格式：`名称 YYYY.MM.DD - HH.MM.SS.cc.mp4`）解析开始时间
3. 通过 `ffprobe` 获取视频时长
4. 判断视频时段是否与任意日志区间重叠，重叠则复制到结果目录

## 依赖

- FFmpeg / FFprobe（系统安装，需在 PATH 中）
