#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Detect duplicate frames in a video (frames identical to the previous frame).

Features:
    1. Decode each frame using OpenCV
    2. Compare each frame against the previous one (supports exact/MSE/histogram/SSIM)
    3. Print a list of duplicate timestamps and the overall ratio
    4. Generate a chart: x-axis = timestamp (MM:SS.mmm), y-axis = similarity to previous frame

Usage (CLI mode):
    python main.py <video.mp4>
    python main.py <video.mp4> --method exact
    python main.py <video.mp4> --threshold 0.99 --method ssim --output chart.png

Usage (interactive mode):
    python main.py                          # auto-detect interactive mode
    python main.py --interactive            # force interactive mode
    python main.py -i                       # same as above
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
# Optional dependency: scikit-image (for SSIM)
# ---------------------------------------------------------------------------
try:
    from skimage.metrics import structural_similarity as ssim_ski

    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

# ---------------------------------------------------------------------------
# matplotlib (for plotting), use non-interactive backend to avoid GUI blocking
# ---------------------------------------------------------------------------
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

# ---------------------------------------------------------------------------
# Similarity computation functions
# ---------------------------------------------------------------------------


def similarity_exact(frame1: np.ndarray, frame2: np.ndarray) -> float:
    """Exact pixel-level comparison: check if two frames are identical.

    Args:
        frame1: First frame (numpy array).
        frame2: Second frame (numpy array).

    Returns:
        float: 1.0 if identical, 0.0 otherwise.
    """
    if frame1.shape != frame2.shape:
        return 0.0
    return 1.0 if np.array_equal(frame1, frame2) else 0.0


def similarity_mse(frame1: np.ndarray, frame2: np.ndarray) -> float:
    """Compute similarity based on Mean Squared Error (MSE).

    Maps MSE to [0, 1]: MSE=0 returns 1.0, larger MSE approaches 0.

    Args:
        frame1: First frame (numpy array).
        frame2: Second frame (numpy array).

    Returns:
        float: Similarity value in [0, 1], higher means more similar.
    """
    if frame1.shape != frame2.shape:
        return 0.0
    diff = frame1.astype(np.float32) - frame2.astype(np.float32)
    mse = np.mean(diff ** 2)
    # Map MSE to [0, 1]: MSE=0 → 1.0, larger MSE → approaches 0
    return float(1.0 / (1.0 + mse / 1000.0))


def similarity_hist(frame1: np.ndarray, frame2: np.ndarray) -> float:
    """Compute similarity based on HSV histogram correlation.

    Convert frames to HSV color space, compute 2D histograms
    (hue: 50 bins, saturation: 60 bins), and compare using correlation.

    Args:
        frame1: First frame (BGR numpy array).
        frame2: Second frame (BGR numpy array).

    Returns:
        float: Similarity value in [0, 1], higher means more similar.
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
    """Compute Structural Similarity Index (SSIM) between two frames.

    Uses scikit-image's SSIM if available, otherwise falls back to a
    simplified SSIM implementation. Converts frames to grayscale and
    computes the product of luminance, contrast, and structure components.

    Args:
        frame1: First frame (BGR numpy array).
        frame2: Second frame (BGR numpy array).

    Returns:
        float: SSIM value in [0, 1], higher means more similar.
    """
    gray1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY)

    if HAS_SKIMAGE:
        return float(max(0.0, ssim_ski(gray1, gray2)))

    # ----- Simplified SSIM (no skimage dependency) -----
    # Constants
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
# Method registry
# ---------------------------------------------------------------------------

METHODS: dict[str, tuple] = {
    "exact": (similarity_exact, "Exact match (pixel-level identical)"),
    "mse": (similarity_mse, "MSE (Mean Squared Error)"),
    "hist": (similarity_hist, "Histogram correlation"),
    "ssim": (similarity_ssim, "SSIM (Structural Similarity)"),
}


# ---------------------------------------------------------------------------
# Solid-color detection
# ---------------------------------------------------------------------------


def _is_solid_color(frame: np.ndarray, std_threshold: float = 5.0) -> bool:
    """Check if a frame is essentially a solid (pure) color.

    Computes the standard deviation of pixel intensities across all channels.
    A very low std deviation indicates the frame is mostly a single color
    (e.g. black / white transition frames between scenes).

    Args:
        frame: BGR frame (numpy array).
        std_threshold: Maximum allowed standard deviation to be considered
                       solid color. Default 5.0 (on 0-255 scale).

    Returns:
        bool: True if the frame is a solid color.
    """
    return float(np.std(frame)) < std_threshold


# ---------------------------------------------------------------------------
# Command-line arguments
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Supported arguments:
    - input: Input video file path (optional, omit for interactive mode)
    - -i, --interactive: Force interactive input mode
    - --threshold: Duplicate detection threshold (default 1.0)
    - --method: Similarity method (exact/mse/hist/ssim)
    - --output: Chart save path
    - --json: Detailed JSON results save path
    - --every-n: Sample interval in frames
    - --no-display: Do not show chart window
    - --ignore-solid / --no-ignore-solid: Ignore solid-color frames

    Returns:
        argparse.Namespace: Parsed command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Detect duplicate frames in a video"
    )
    parser.add_argument(
        "input", type=str, nargs="?", default=None,
        help="Input video file path (omit to enter interactive mode)",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Force interactive input mode",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="Similarity threshold for duplicate detection, default 1.0 (exact mode only counts pixel-identical frames)",
    )
    parser.add_argument(
        "--method",
        type=str,
        default="exact",
        choices=list(METHODS),
        help="Similarity method, default exact (pixel-level exact match)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Chart save path (default: auto-generate beside input file)",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="Detailed JSON results save path (omit to skip)",
    )
    parser.add_argument(
        "--every-n",
        type=int,
        default=1,
        help="Sample every N frames (>1 speeds up large files), default 1",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="Do not show the chart window (save only)",
    )
    parser.add_argument(
        "--ignore-solid",
        action="store_true",
        default=True,
        help="Ignore solid-color frames (e.g. black transitions), default true",
    )
    parser.add_argument(
        "--no-ignore-solid",
        action="store_false",
        dest="ignore_solid",
        help="Do not ignore solid-color frames",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Interactive input
# ---------------------------------------------------------------------------


def _interactive_prompt() -> argparse.Namespace:
    """Prompt the user to manually enter parameters interactively.

    Walks through each parameter step by step, with input validation.
    JSON is auto-saved beside the video file.

    Returns:
        argparse.Namespace: Namespace with all fields populated.
    """
    print("=== Interactive Mode ===")

    # 1. Video file path
    while True:
        raw = input("Video file path: ").strip()
        p = Path(raw)
        if p.is_file():
            input_path = str(p)
            break
        print(f"File not found: {raw}")

    # 2. Similarity method
    print("\nAvailable methods:")
    for k, (_, desc) in METHODS.items():
        print(f"  {k}: {desc}")
    while True:
        raw = input("Method [exact]: ").strip() or "exact"
        if raw in METHODS:
            method = raw
            break
        print(f"Invalid method: {raw}")

    # 3. Threshold
    while True:
        raw = input("Threshold (0~1) [1.0]: ").strip() or "1.0"
        try:
            threshold = float(raw)
            if 0.0 <= threshold <= 1.0:
                break
        except ValueError:
            pass
        print("Invalid threshold, must be a float between 0 and 1")

    # 4. Every-N
    while True:
        raw = input("Sample every N frames [1]: ").strip() or "1"
        try:
            every_n = int(raw)
            if every_n >= 1:
                break
        except ValueError:
            pass
        print("Invalid value, must be an integer >= 1")

    # 5. Output chart path
    raw = input("Chart save path (empty = auto): ").strip()
    output = raw if raw else None

    # 6. JSON path (auto)
    json_path = str(Path(input_path).with_suffix(".json"))

    # 7. Ignore solid color
    raw = input("Ignore solid-color frames (Y/n): ").strip().lower()
    ignore_solid = raw != "n"

    # 8. Display chart
    raw = input("Show chart window (y/N): ").strip().lower()
    no_display = raw != "y"

    return argparse.Namespace(
        input=input_path,
        interactive=False,
        threshold=threshold,
        method=method,
        output=output,
        json=json_path,
        every_n=every_n,
        no_display=no_display,
        ignore_solid=ignore_solid,
    )


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    if args.interactive or (args.input is None and sys.stdin.isatty()):
        args = _interactive_prompt()
        args.interactive = False

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Error: file not found -> {input_path}")
        sys.exit(1)

    threshold = args.threshold
    every_n = max(1, args.every_n)
    sim_fn, sim_name = METHODS[args.method]

    # ---- Open video ----
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        print("Error: cannot open video file (make sure ffmpeg / OpenCV codecs are installed)")
        sys.exit(1)

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = total_frames / fps if fps > 0 else 0.0

    print(f"File       : {input_path.name}")
    print(f"Method     : {sim_name}")
    print(f"Threshold  : {threshold}")
    print(f"Every-N    : {every_n}")
    print(f"Total frames: {total_frames}")
    print(f"FPS        : {fps:.2f} fps")
    print(f"Duration   : {duration:.2f} s")
    print("-" * 50)

    # ---- Frame-by-frame processing ----
    similarities: list[float] = []       # similarity to previous frame
    duplicate_frames: list[dict] = []     # duplicate frame details
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
            is_solid = args.ignore_solid and _is_solid_color(frame)
            if prev_frame is not None and not is_solid:
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
                prev_frame = frame
            elif is_solid:
                # Solid-color frame: skip duplicate detection, keep prev_frame unchanged
                pass
            else:
            # First frame: no previous frame, mark as 0.0 (not a duplicate)
            similarities.append(0.0)

        frame_idx += 1

        # Progress indicator
        pct = int(frame_idx / total_frames * 100) if total_frames else 100
        if pct >= last_progress + 10:
            last_progress = pct
            print(f"Processing … {pct}% ({frame_idx}/{total_frames})")

    cap.release()
    print(f"Done … 100% ({frame_idx}/{total_frames})")
    print("-" * 50)

    # ---- Results ----
    analyzed = len(similarities)
    dup_ratio = duplicate_count / analyzed if analyzed > 0 else 0.0

    print(f"\n=== Results ===")
    print(f"  Frames analyzed  : {analyzed}")
    print(f"  Duplicate frames : {duplicate_count}")
    print(f"  Duplicate ratio  : {dup_ratio:.4%}")

    if duplicate_frames:
        print(f"\nDuplicate frames (first 30):")
        print(f"  {'Frame':>8}  {'Timestamp':>12}  {'Similarity':>8}")
        print(f"  {'-'*8}  {'-'*12}  {'-'*8}")
        for df in duplicate_frames[:30]:
            print(f"  {df['frame_index']:>8}  {df['timestamp_str']:>12}  {df['similarity']:>8.4f}")
        if len(duplicate_frames) > 30:
            print(f"  … {len(duplicate_frames)} duplicate frames total (showing first 30)")

        # Merge duplicate frame ranges
        merged = _merge_duplicate_ranges(duplicate_frames, fps)
        print(f"\nMerged duplicate ranges:")
        for seg in merged:
            print(
                f"  {seg['start_frame']:>8} – {seg['end_frame']:<8}  "
                f"[{seg['start_time']} – {seg['end_time']}]  "
                f"{seg['count']} frames"
            )

    # ---- JSON output ----
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
            "duplicate_frames": duplicate_frames[:500],  # prevent oversized JSON
        }
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON saved → {json_path}")

    # ---- Plot chart ----
    _plot_chart(
        similarities=similarities,
        every_n=every_n,
        threshold=threshold,
        input_name=input_path.name,
        dup_ratio=dup_ratio,
        output_path=args.output or (input_path.with_suffix(".png")),
        no_display=args.no_display,
        fps=fps,
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _fmt_time(seconds: float) -> str:
    """Format seconds into a readable time string.

    Format is MM:SS.mmm, e.g. "05:23.456".

    Args:
        seconds: Time in seconds.

    Returns:
        str: Formatted time string.
    """
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:06.3f}"


def _merge_duplicate_ranges(
    dup_frames: list[dict], fps: float
) -> list[dict]:
    """Merge consecutive duplicate frames into contiguous ranges.

    Iterates through the duplicate frames list and merges frames
    with consecutive indices into segments, outputting start/end
    frame indices, time ranges, and frame counts.

    Args:
        dup_frames: List of duplicate frame dicts, each containing
                    frame_index and timestamp.
        fps: Video frame rate (currently unused, kept for API compatibility).

    Returns:
        list[dict]: Merged contiguous duplicate segments.
    """
    if not dup_frames:
        return []

    segments: list[dict] = []
    start = dup_frames[0]

    for i in range(1, len(dup_frames)):
        cur = dup_frames[i]
        prev = dup_frames[i - 1]
        # If current frame index is not consecutive, the segment ends
        if cur["frame_index"] != prev["frame_index"] + 1:
            segments.append({
                "start_frame": start["frame_index"],
                "end_frame": prev["frame_index"],
                "start_time": _fmt_time(start["timestamp"]),
                "end_time": _fmt_time(prev["timestamp"]),
                "count": prev["frame_index"] - start["frame_index"] + 1,
            })
            start = cur

    # Last segment
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
    fps: float,
) -> None:
    """Plot frame similarity analysis chart (similarity curve + histogram).

    Args:
        similarities: List of similarity values between consecutive frames.
        every_n: Sample interval in frames.
        threshold: Duplicate detection threshold.
        input_name: Input video filename (for chart title).
        dup_ratio: Duplicate frame ratio.
        output_path: Chart save path.
        no_display: If True, do not show the chart window.
        fps: Video frame rate (for x-axis timestamp conversion).
    """
    x_seconds = np.arange(len(similarities)) * every_n / fps if fps > 0 else np.arange(len(similarities)) * every_n
    sim_arr = np.array(similarities)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=False)

    # ---- Top: similarity curve ----
    ax1.plot(x_seconds, sim_arr, "b-", linewidth=0.6, alpha=0.7, label="Similarity to previous frame")
    ax1.axhline(y=threshold, color="r", linestyle="--", linewidth=0.8, label=f"Threshold ({threshold})")
    ax1.fill_between(
        x_seconds, sim_arr, threshold,
        where=(sim_arr >= threshold),
        color="red", alpha=0.08, label="Duplicate area",
    )
    ax1.set_xlabel("Timestamp (MM:SS.mmm)")
    ax1.set_ylabel("Similarity to previous frame")
    ax1.set_title(f"Frame Similarity Analysis — {input_name}  (Duplicate ratio: {dup_ratio:.2%})")
    ax1.set_ylim(-0.02, 1.05)
    ax1.xaxis.set_major_formatter(FuncFormatter(lambda s, _: _fmt_time(s)))
    ax1.legend(loc="upper right", fontsize=9)
    ax1.grid(True, alpha=0.25)

    # ---- Bottom: similarity distribution histogram ----
    ax2.hist(sim_arr, bins=80, range=(0, 1), color="steelblue", edgecolor="white", alpha=0.7)
    ax2.axvline(x=threshold, color="r", linestyle="--", linewidth=0.8, label=f"Threshold ({threshold})")
    ax2.set_xlabel("Similarity to previous frame")
    ax2.set_ylabel("Frame count")
    ax2.set_title("Similarity Distribution Histogram")
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.25)

    plt.tight_layout()

    # Save
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(out), dpi=150, bbox_inches="tight")
    print(f"Chart saved → {out}")

    if not no_display:
        try:
            plt.show()
        except Exception:
            pass
    else:
        plt.close(fig)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
