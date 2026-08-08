# 05. 拆分为隔帧截图

## 用途

将视频按每秒 1 帧的频率提取 JPEG 截图，用于生成图像训练数据集。

## 使用方式

- Windows 批处理脚本：`split.bat`
- 修改脚本中的 `input_folder` / `output_folder` 路径后运行
- 遍历目录下所有 MP4，每个视频截图至独立文件夹

输出：`frame_0001.jpg`, `frame_0002.jpg`, ...

## 依赖

- FFmpeg（系统安装，需在 PATH 中）
