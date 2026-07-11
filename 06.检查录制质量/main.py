#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查视频中的重复帧（与前一帧内容一致的帧）。

功能：
    1. 使用 OpenCV 解码视频的每一帧
    2. 比较每一帧与上一帧（支持 exact 精确匹配 / MSE / 直方图 / SSIM 四种方法）
    3. 输出重复帧的时间点列表和总体比例
    4. 生成统计图：横轴为帧序号，纵轴为与上一帧的相似度

用法：
    python main.py <video.mp4>
    python main.py <video.mp4> --method exact       # 默认：像素级完全一致
    python main.py <video.mp4> --threshold 0.99 --method ssim --output chart.png
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# 可选依赖：scikit-image（用于 SSIM）
# ---------------------------------------------------------------------------
try:
    from skimage.metrics import structural_similarity as ssim_ski

    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

# ---------------------------------------------------------------------------
# matplotlib（用于绘图），使用非交互式后端避免 GUI 阻塞
# ---------------------------------------------------------------------------
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------------------------------------------------------------------
# 相似度计算函数
# ---------------------------------------------------------------------------


def similarity_exact(frame1: np.ndarray, frame2: np.ndarray) -> float:
    """像素级精确比较：判断两帧图像是否完全一致。

    Args:
        frame1: 第一帧图像（numpy 数组）。
        frame2: 第二帧图像（numpy 数组）。

    Returns:
        float: 两帧完全一致返回 1.0，否则返回 0.0。
    """
    if frame1.shape != frame2.shape:
        return 0.0
    return 1.0 if np.array_equal(frame1, frame2) else 0.0


def similarity_mse(frame1: np.ndarray, frame2: np.ndarray) -> float:
    """基于均方误差 (MSE) 计算两帧图像的相似度。

    将 MSE 映射到 [0, 1] 区间：MSE=0 时返回 1.0，MSE 越大返回值趋近于 0。

    Args:
        frame1: 第一帧图像（numpy 数组）。
        frame2: 第二帧图像（numpy 数组）。

    Returns:
        float: 相似度值，值域 [0, 1]，越大表示越相似。
    """
    if frame1.shape != frame2.shape:
        return 0.0
    diff = frame1.astype(np.float32) - frame2.astype(np.float32)
    mse = np.mean(diff ** 2)
    # 将 MSE 映射到 [0, 1]：MSE=0 → 1.0，MSE 越大 → 趋近 0
    return float(1.0 / (1.0 + mse / 1000.0))


def similarity_hist(frame1: np.ndarray, frame2: np.ndarray) -> float:
    """基于 HSV 颜色直方图相关性计算两帧图像的相似度。

    将图像转换到 HSV 色彩空间，计算 2D 直方图（色相 50 级，饱和度 60 级），
    使用相关性比较方法得到 [0, 1] 区间的相似度值。

    Args:
        frame1: 第一帧图像（BGR 格式的 numpy 数组）。
        frame2: 第二帧图像（BGR 格式的 numpy 数组）。

    Returns:
        float: 相似度值，值域 [0, 1]，越大表示越相似。
    """
    hsv1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2HSV)
    hsv2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2HSV)
    hist1 = cv2.calcHist([hsv1], [0, 1], None, [50, 60], [0, 180, 0, 256])
    hist2 = cv2.calcHist([hsv2], [0, 1], None, [50, 60], [0, 180, 0, 256])
    cv2.normalize(hist1, hist1, 0, 1, cv2.NORM_MINMAX)
    cv2.normalize(hist2, hist2, 0, 1, cv2.NORM_MINMAX)
    corr = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
    return float(max(0.0, corr))


def similarity_ssim(frame1: np.ndarray, frame2: np.ndarray) -> float:
    """基于结构相似性 (SSIM) 计算两帧图像的相似度。

    优先使用 scikit-image 的 SSIM 实现，若未安装则使用简化版 SSIM 算法。
    将图像转为灰度图后计算亮度、对比度和结构三个分量的乘积。

    Args:
        frame1: 第一帧图像（BGR 格式的 numpy 数组）。
        frame2: 第二帧图像（BGR 格式的 numpy 数组）。

    Returns:
        float: SSIM 相似度值，值域 [0, 1]，越大表示越相似。
    """
    gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)

    if HAS_SKIMAGE:
        return float(max(0.0, ssim_ski(gray1, gray2)))

    # ----- 简化版 SSIM（不依赖 skimage）-----
    # 常数
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2

    mu1 = gray1.mean()
    mu2 = gray2.mean()
    sigma1_sq = gray1.var()
    sigma2_sq = gray2.var()
    sigma12 = np.mean((gray1 - mu1) * (gray2 - mu2))

    luminance = (2 * mu1 * mu2 + C1) / (mu1 ** 2 + mu2 ** 2 + C1)
    contrast = (2 * np.sqrt(sigma1_sq) * np.sqrt(sigma2_sq) + C2) / (sigma1_sq + sigma2_sq + C2)
    structure = (sigma12 + C2 / 2) / (np.sqrt(sigma1_sq) * np.sqrt(sigma2_sq) + C2 / 2)

    return float(max(0.0, luminance * contrast * structure))


# ---------------------------------------------------------------------------
# 方法注册表
# ---------------------------------------------------------------------------

METHODS: dict[str, tuple] = {
    "exact": (similarity_exact, "精确匹配（像素级完全一致）"),
    "mse": (similarity_mse, "MSE（均方误差）"),
    "hist": (similarity_hist, "直方图相关性"),
    "ssim": (similarity_ssim, "SSIM（结构相似性）"),
}


# ---------------------------------------------------------------------------
# 命令行参数
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    支持的参数:
    - input: 输入视频文件路径（必填）
    - --threshold: 重复帧判定阈值（默认 1.0）
    - --method: 相似度计算方法（exact/mse/hist/ssim）
    - --output: 统计图保存路径
    - --json: 详细结果 JSON 保存路径
    - --every-n: 采样间隔帧数
    - --no-display: 不显示统计图窗口

    Returns:
        argparse.Namespace: 解析后的命令行参数对象。
    """
    parser = argparse.ArgumentParser(
        description="检查视频中的重复帧（与前一帧内容一致的帧）"
    )
    parser.add_argument("input", type=str, help="输入的 MP4 视频文件路径")
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="判定为重复帧的相似度阈值，默认 1.0（exact 模式下仅当像素完全一致时判为重复）",
    )
    parser.add_argument(
        "--method",
        type=str,
        default="exact",
        choices=list(METHODS),
        help="相似度计算方法，默认 exact（像素级精确匹配）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="统计图保存路径（不指定则自动生成在输入文件同目录）",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="详细结果 JSON 保存路径（不指定则不输出）",
    )
    parser.add_argument(
        "--every-n",
        type=int,
        default=1,
        help="每 N 帧采样一次（>1 可加速处理大文件），默认 1",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="不显示统计图窗口（仅保存文件）",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"错误：文件不存在 -> {input_path}")
        sys.exit(1)

    threshold = args.threshold
    every_n = max(1, args.every_n)
    sim_fn, sim_name = METHODS[args.method]

    # ---- 打开视频 ----
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        print("错误：无法打开视频文件（请确认已安装 ffmpeg / OpenCV 编解码器）")
        sys.exit(1)

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = total_frames / fps if fps > 0 else 0.0

    print(f"文件      : {input_path.name}")
    print(f"方法      : {sim_name}")
    print(f"阈值      : {threshold}")
    print(f"采样间隔  : 每 {every_n} 帧")
    print(f"总帧数    : {total_frames}")
    print(f"帧率      : {fps:.2f} fps")
    print(f"时长      : {duration:.2f} 秒")
    print("-" * 50)

    # ---- 逐帧处理 ----
    similarities: list[float] = []       # 每帧与上一帧的相似度
    duplicate_frames: list[dict] = []     # 重复帧详情
    prev_frame: np.ndarray | None = None
    frame_idx = 0
    duplicate_count = 0
    last_progress = -1

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        analyze = frame_idx % every_n == 0

        if analyze:
            if prev_frame is not None:
                sim = sim_fn(prev_frame, frame)
                similarities.append(sim)
                if sim >= threshold:
                    duplicate_count += 1
                    ts = frame_idx / fps
                    duplicate_frames.append({
                        "frame_index": frame_idx,
                        "timestamp": round(ts, 4),
                        "timestamp_str": _fmt_time(ts),
                        "similarity": round(sim, 6),
                    })
            else:
                # 第一帧：没有前一帧，相似度记为 1.0
                similarities.append(1.0)

            prev_frame = frame

        frame_idx += 1

        # 进度提示
        pct = int(frame_idx / total_frames * 100) if total_frames else 100
        if pct >= last_progress + 10:
            last_progress = pct
            print(f"处理中 … {pct}% ({frame_idx}/{total_frames})")

    cap.release()
    print(f"处理完成… 100% ({frame_idx}/{total_frames})")
    print("-" * 50)

    # ---- 统计结果 ----
    analyzed = len(similarities)
    dup_ratio = duplicate_count / analyzed if analyzed > 0 else 0.0

    print(f"\n=== 分析结果 ===")
    print(f"  分析的帧数      : {analyzed}")
    print(f"  重复帧数        : {duplicate_count}")
    print(f"  重复帧比例      : {dup_ratio:.4%}")

    if duplicate_frames:
        print(f"\n重复帧列表（前 30 条）：")
        print(f"  {'帧序号':>8}  {'时间戳':>12}  {'相似度':>8}")
        print(f"  {'-'*8}  {'-'*12}  {'-'*8}")
        for df in duplicate_frames[:30]:
            print(f"  {df['frame_index']:>8}  {df['timestamp_str']:>12}  {df['similarity']:>8.4f}")
        if len(duplicate_frames) > 30:
            print(f"  … 共 {len(duplicate_frames)} 条重复帧记录（仅显示前 30 条）")

        # 重复帧时间段合并
        merged = _merge_duplicate_ranges(duplicate_frames, fps)
        print(f"\n重复帧连续段（合并后）：")
        for seg in merged:
            print(
                f"  {seg['start_frame']:>8} – {seg['end_frame']:<8}  "
                f"[{seg['start_time']} – {seg['end_time']}]  "
                f"共 {seg['count']} 帧"
            )

    # ---- 输出 JSON ----
    if args.json:
        result = {
            "file": str(input_path),
            "fps": fps,
            "total_frames": total_frames,
            "duration": round(duration, 4),
            "analyzed_frames": analyzed,
            "duplicate_count": duplicate_count,
            "duplicate_ratio": round(dup_ratio, 6),
            "threshold": threshold,
            "method": args.method,
            "every_n": every_n,
            "duplicate_frames": duplicate_frames[:500],  # 防止 JSON 过大
        }
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON 已保存 → {json_path}")

    # ---- 绘制统计图 ----
    _plot_chart(
        similarities=similarities,
        every_n=every_n,
        threshold=threshold,
        input_name=input_path.name,
        dup_ratio=dup_ratio,
        output_path=args.output or (input_path.with_suffix(".png")),
        no_display=args.no_display,
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _fmt_time(seconds: float) -> str:
    """将秒数格式化为可读的时间字符串。

    格式为 MM:SS.mmm，例如 "05:23.456"。

    Args:
        seconds: 以秒为单位的时间长度。

    Returns:
        str: 格式化的时间字符串。
    """
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:06.3f}"


def _merge_duplicate_ranges(
    dup_frames: list[dict], fps: float
) -> list[dict]:
    """将连续的重复帧合并为连续时间段。

    遍历重复帧列表，将帧序号连续的合并为同一个段，
    输出每个段的起始帧、结束帧、时间范围和包含的帧数。

    Args:
        dup_frames: 重复帧详情列表，每个元素包含 frame_index 和 timestamp。
        fps: 视频帧率，用于时间计算（当前未直接使用，保留参数）。

    Returns:
        list[dict]: 合并后的重复帧连续段列表。
    """
    if not dup_frames:
        return []

    segments: list[dict] = []
    start = dup_frames[0]

    for i in range(1, len(dup_frames)):
        cur = dup_frames[i]
        prev = dup_frames[i - 1]
        # 如果当前帧序号不连续，说明段结束
        if cur["frame_index"] != prev["frame_index"] + 1:
            segments.append({
                "start_frame": start["frame_index"],
                "end_frame": prev["frame_index"],
                "start_time": _fmt_time(start["timestamp"]),
                "end_time": _fmt_time(prev["timestamp"]),
                "count": prev["frame_index"] - start["frame_index"] + 1,
            })
            start = cur

    # 最后一段
    segments.append({
        "start_frame": start["frame_index"],
        "end_frame": dup_frames[-1]["frame_index"],
        "start_time": _fmt_time(start["timestamp"]),
        "end_time": _fmt_time(dup_frames[-1]["timestamp"]),
        "count": dup_frames[-1]["frame_index"] - start["frame_index"] + 1,
    })

    return segments


def _plot_chart(
    similarities: list[float],
    every_n: int,
    threshold: float,
    input_name: str,
    dup_ratio: float,
    output_path: str | Path,
    no_display: bool,
) -> None:
    """绘制帧相似度分析图表（相似度曲线 + 分布直方图）并保存。

    Args:
        similarities: 每帧与上一帧的相似度列表。
        every_n: 采样间隔帧数。
        threshold: 重复帧判定阈值。
        input_name: 输入视频文件名（用于图表标题）。
        dup_ratio: 重复帧比例。
        output_path: 图表保存路径。
        no_display: 是否不显示图表窗口。
    """
    x = np.arange(len(similarities)) * every_n
    sim_arr = np.array(similarities)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=False)

    # ---- 上：相似度曲线 ----
    ax1.plot(x, sim_arr, "b-", linewidth=0.6, alpha=0.7, label="与上一帧的相似度")
    ax1.axhline(y=threshold, color="r", linestyle="--", linewidth=0.8, label=f"阈值 ({threshold})")
    ax1.fill_between(
        x, sim_arr, threshold,
        where=(sim_arr >= threshold),
        color="red", alpha=0.08, label="重复区域",
    )
    ax1.set_ylabel("与上一帧的相似度")
    ax1.set_title(f"帧相似度分析 — {input_name}  （重复帧比例: {dup_ratio:.2%}）")
    ax1.set_ylim(-0.02, 1.05)
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(True, alpha=0.25)

    # ---- 下：相似度分布直方图 ----
    ax2.hist(sim_arr, bins=80, range=(0, 1), color="steelblue", edgecolor="white", alpha=0.7)
    ax2.axvline(x=threshold, color="r", linestyle="--", linewidth=0.8, label=f"阈值 ({threshold})")
    ax2.set_xlabel("与上一帧的相似度")
    ax2.set_ylabel("帧数")
    ax2.set_title("相似度分布直方图")
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.25)

    plt.tight_layout()

    # 保存
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(out), dpi=150, bbox_inches="tight")
    print(f"统计图已保存 → {out}")

    if not no_display:
        try:
            plt.show()
        except Exception:
            pass
    else:
        plt.close(fig)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
