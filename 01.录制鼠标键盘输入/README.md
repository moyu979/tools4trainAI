# 01. 录制鼠标键盘输入 — 详细说明

## 概述

本模块用于在游戏或应用运行的同时，记录键盘、鼠标和手柄（游戏控制器）的输入事件，生成带精确时间戳的日志文件，用于后续的 AI 训练数据采集。

---

## 脚本说明

| 脚本 | 说明 |
|------|------|
| **`record.py`** | **统一输入记录器**，同时录制键盘 (K)、鼠标 (M) 和手柄 (G)，输出单个日志文件 |
| `waterfall_plot.py` | 读取日志，绘制键盘/鼠标/手柄三幅瀑布图并保存为 PNG |

---

## record.py 统一记录器

### 使用方式

```bash
python record.py
```

按 **Ctrl+C** 停止录制，日志文件生成在当前目录的 `data/` 子目录下：`input_YYYYMMDD_HHMMSS.txt`

### 支持的设备

| 前缀 | 设备 | 底层库 | 记录内容 |
|------|------|--------|---------|
| `K` | 键盘 | pynput | 按键按下/释放（含 vk 物理键码） |
| `M` | 鼠标 | pynput | 左/右/中键、侧键 x1/x2 点击、移动（时间+位移双重降噪）、滚轮滚动 |
| `G` | 手柄 | pygame / SDL | 按钮（含 L3/R3）、方向键、摇杆轴（死区+限频+回正归零） |

> 设备可用性仅根据库是否存在判断，不扫描物理设备。如果未安装 pygame，手柄录制自动禁用；如果未安装 pynput，则自动降级为**仅录制手柄**（键盘/鼠标禁用）。

### 日志格式

#### 键盘事件

```
[ISO时间] K PRESS   key=<键名> vk=<物理键码>
[ISO时间] K RELEASE key=<键名> vk=<物理键码>
```

示例：
```
[2026-07-11T12:00:00.123456] K PRESS   key=w vk=87
[2026-07-11T12:00:00.234567] K RELEASE key=w vk=87
[2026-07-11T12:00:00.345678] K PRESS   key=shift vk=160
```

键名规则：
- 可打印字符 → 字符本身（`a`、`1`、`[` 等）
- 特殊键 → 小写英文名（`shift`、`enter`、`space`、`ctrl`、`tab`、`esc` 等）

`vk` 为各平台物理键码（Windows VK / macOS 虚拟键码 / Linux keysym），同一平台内唯一，
不受大小写、Shift 修饰或键盘布局影响；平台间数值不通用。

降噪：
- 自动连发忽略：长按按键时，OS 自动重复的 PRESS 事件被丢弃，只保留第一次 PRESS 和最终的 RELEASE

#### 组合键（裸格式）

Windows 下按住 Ctrl 再按字母键时，系统上报的是 ASCII 控制字符（如 Ctrl+A → `\x01`）。
记录器会将其还原为字母，与 Ctrl 键本身分开记录，因此 **Ctrl+A 记录为 4 条事件**：

```
[ISO时间] K PRESS   key=ctrl        ← Ctrl 按下（由 pynput 单独上报）
[ISO时间] K PRESS   key=a           ← 字母还原
[ISO时间] K RELEASE key=a           ← 字母释放
[ISO时间] K RELEASE key=ctrl        ← Ctrl 释放
```

> Ctrl 键按 pynput 实际上报名记录（`ctrl` / `ctrl_l` / `ctrl_r`，保留左右手信息）。
> 该裸格式对 AI 训练更友好：模型直接看到每个物理键的按下/释放，无需理解合成后的组合键。

#### 鼠标事件

```
[ISO时间] M PRESS   button=<left|right|middle|x1|x2>
[ISO时间] M RELEASE button=<left|right|middle|x1|x2>
[ISO时间] M MOVE    x=<X坐标> y=<Y坐标>
[ISO时间] M SCROLL  x=<X> y=<Y> dx=<水平> dy=<垂直>
```

支持左键 `left`、右键 `right`、中键 `middle` 及侧键 `x1`（后退）/ `x2`（前进）。

示例：
```
[2026-07-11T12:01:00.123456] M PRESS   button=left
[2026-07-11T12:01:00.234567] M RELEASE button=left
[2026-07-11T12:01:00.345678] M MOVE    x=800 y=600
[2026-07-11T12:01:00.456789] M SCROLL  x=800 y=600 dx=0 dy=1
```

降噪参数（鼠标移动）：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `move_min_interval_ms` | 50ms | 两次移动记录之间至少间隔 50ms |
| `move_min_distance` | 5px | 曼哈顿距离累计 ≥ 5px 才记录 |

#### 手柄事件

```
[ISO时间] G[品牌] BUTTON_DOWN id=<编号> key=<按键名>
[ISO时间] G[品牌] BUTTON_UP   id=<编号> key=<按键名>
[ISO时间] G[品牌] AXIS_MOVE   id=<编号> stick=L x=<X> y=<Y>        # 摇杆 X/Y 合并一行
[ISO时间] G[品牌] AXIS_MOVE   id=<编号> axis=<轴名> val=<数值>      # 扳机等单轴
```

示例：
```
[2026-07-11T12:02:00.123456] G[Xbox] BUTTON_DOWN id=0 key=A
[2026-07-11T12:02:00.234567] G[Xbox] BUTTON_UP   id=0 key=A
[2026-07-11T12:02:00.345678] G[Xbox] AXIS_MOVE   id=0 stick=L x=0.4523 y=-0.2011
[2026-07-11T12:02:00.456789] G[Xbox] AXIS_MOVE   id=0 axis=R_Trigger val=1.0000
[2026-07-11T12:02:00.456789] G[DualSense] BUTTON_DOWN id=1 key=Cross
```

品牌标识由 `_get_controller_mapping()` 根据设备名模糊匹配得出：

| 品牌 | 匹配关键词 | 来源 |
|------|-----------|------|
| `Xbox` | 设备名含 "xinput" 或 "xbox"（Windows） | 微软 XInput 协议手柄（含北通等第三方） |
| `DualSense` | 设备名含 "dualsense" | Sony PS5 手柄 |
| `PS4` | 设备名含 "ps4" | Sony PS4 手柄 |
| `Unknown` | 未匹配 | 未知/未适配手柄 |

降噪参数（摇杆轴）：
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DEADZONE` | 0.05 | 摇杆绝对值 < 5% 视为归零 |
| `MIN_DELTA` | 0.01 | 轴值变化 < 1% 不记录 |
| `AXIS_MIN_INTERVAL_MS` | 20ms | 摇杆最短记录间隔（类似鼠标最短间隔） |
| `AXIS_BIG_DELTA` | 0.3 | 大幅变化立即记录，快速推杆不丢帧 |

### 手柄映射表

不同系统、不同手柄的按键编号不同，映射表负责将原始编号转为可读名称。

**macOS Xbox 手柄映射（部分）：**
```
按钮: 0→A, 1→B, 2→X, 3→Y, 9→LB, 10→RB, 11→D_Up …
轴:   0→L_Stick_X, 1→L_Stick_Y, 4→L_Trigger …
```

**Windows Xbox 手柄映射（XInput 标准）：**
```
按钮: 0→A, 1→B, 2→X, 3→Y, 4→LB, 5→RB, 6→View, 7→Menu,
      8→L3, 9→R3, 10→Xbox, 11→D_Up, 12→D_Down, 13→D_Left, 14→D_Right
轴:   0→L_Stick_X, 1→L_Stick_Y, 2→R_Stick_X, 3→R_Stick_Y, 4→L_Trigger, 5→R_Trigger
```

> 差异原因：Windows 使用 XInput 协议，macOS 使用 IOKit 读取 HID 报告，两者的按键排列顺序不同。

### 热插拔支持

手柄支持热插拔与"后连"：
1. SDL 发出 `JOYDEVICEREMOVED` / `JOYDEVICEADDED` 事件
2. 记录器重新扫描并重建映射表（含摇杆分组、限频状态）
3. 启动时无手柄也不退出：靠 `JOYDEVICEADDED` 事件 + 每 1 秒兜底扫描发现后插入的手柄

---

## waterfall_plot.py 瀑布图

### 使用方式

```bash
python waterfall_plot.py input_20260711_120000.txt
```

生成三张 PNG 图片：
```
keyboard_waterfall_input_20260711_120000.png
mouse_waterfall_input_20260711_120000.png
gamepad_waterfall_input_20260711_120000.png
```

### 图表说明

| 图 | 纵轴 | 内容 |
|----|------|------|
| Keyboard | 按键名（a, shift, enter …） | 键盘 PRESS/RELEASE 时间区间 |
| Mouse | 鼠标按钮（mouse:left, mouse:right, mouse:middle） | 鼠标点击 PRESS/RELEASE 时间区间 |
| Gamepad | 手柄按钮（A, LB, D_Up, Cross …） | 手柄 BUTTON_DOWN/UP 时间区间 |

横轴为从第一个事件开始的相对时间（秒），忽略移动、滚轮、摇杆轴等非按键事件。

---

## 注意事项

### 权限要求

- **macOS**：需要在「系统设置 → 隐私与安全性 → 输入监听」中勾选使用的终端应用
- **Linux**：可能需要 `sudo` 运行，或将用户加入 `input` 组
- **Windows**：部分游戏需要以管理员身份运行终端

### 反作弊检测

此工具通过全局输入钩子（pynput / SDL）录製输入事件，行为与按键精灵、宏工具相似。部分反作弊系统可能：
- 阻止游戏启动
- 运行时弹出警告
- 封禁账号

**建议仅用于离线单机游戏，使用者自行承担风险。**

### 依赖安装

```bash
pip install pynput>=1.7.6 pygame>=2.5.0 matplotlib>=3.7.0
```

---

## 兼容性测试表

> 图例：✓ 已实测通过 ｜ ○ 待测试（TODO） ｜ × 不可用 / 未实现

| 设备 | Windows | macOS | Linux |
|------|:-------:|:-----:|:-----:|
| 键盘（pynput） | ✓ | ○ | ○ |
| 鼠标（pynput） | ✓ | ○ | ○ |
| 北通 BTP-KP20D（XInput → Xbox 布局） | ✓ | ○ | × |
| PS4 手柄（DualShock 4） | ○ | ○ | × |
| PS5 手柄（DualSense） | ○ | ○ | × |

**说明**：

- **✓**：Windows 下键盘、鼠标、北通 BTP-KP20D 手柄已实测通过（2026-08-09）。
- **○**：代码已实现 / 映射表已预置，但尚未在对应平台真机验证（含 PS4 / PS5 手柄，映射表已内置但未测试）。
- **×**：Linux 手柄捕捉逻辑暂未实现（`_get_controller_mapping` 抛 `NotImplementedError`）；键盘/鼠标在 macOS/Linux 上代码可用但未验证。
