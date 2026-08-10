# 04. 拆分为隔帧截图

## 用途

将视频按每秒 1 帧的频率提取 JPEG 截图，用于生成图像训练数据集。

## 使用方式

- Python 脚本：`main.py`（跨平台，推荐）
- Windows 批处理脚本：`split.bat`（旧版，仅 Windows）

### Python 版本

```bash
# 使用默认路径（输入 videos/，输出 screenshots/）
python main.py

# 指定路径与截帧频率
python main.py --input /path/to/videos --output /path/to/screenshots
python main.py -i videos -o shots --fps 2
```

- 遍历目录下所有 MP4，每个视频截图至独立文件夹
- 目标截图文件夹已存在时自动跳过该视频
- 截图命名：`frame_0001.jpg`, `frame_0002.jpg`, ...

## 输入 / 输出

| 类型 | 说明 |
| ---- | ---- |
| 输入 | 源视频文件夹，内含一个或多个 MP4 文件 |
| 输出 | 截图文件夹，每个视频对应一个子文件夹，内含按序编号的 JPEG 截图 |
| 参数 | `-i/--input` 源视频文件夹（默认 `videos`）；`-o/--output` 输出文件夹（默认 `screenshots`）；`--fps` 每秒截帧数（默认 `1`） |

## 依赖

- FFmpeg（系统安装，需在 PATH 中）
