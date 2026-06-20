# Piano Hand

将 MIDI 或 MusicXML 乐谱转换为可编辑的虚拟手钢琴教学视频。

项目完全在本地运行，不需要 GPU，也不依赖网页、账号或云端服务。

## 功能

- 解析 MIDI、MusicXML 和 MXL 乐谱。
- 自动分配左右手并生成指法。
- 通过 `fingering.csv` 人工调整手和指法。
- 渲染二维虚拟手、按键高亮、手指编号和小节信息。
- 支持局部键盘和标准 88 键完整键盘。
- 导出 MP4 视频。
- 可选使用 FluidSynth 和 SoundFont 生成钢琴音频。

## 支持的输入格式

- `.mid`
- `.midi`
- `.musicxml`
- `.xml`
- `.mxl`

## 环境要求

- Windows 10/11 或 Ubuntu 22.04/24.04
- Conda
- 普通 CPU；不需要 GPU
- 静音视频：FFmpeg
- 有声视频：FFmpeg、FluidSynth 和合法授权的 `.sf2` SoundFont

项目的 [environment.yml](./environment.yml) 会安装 Python、FFmpeg、FluidSynth
以及开发依赖。

## Linux 快速开始

以下命令适用于 Ubuntu 22.04/24.04。

### 1. 安装 Miniconda

如果系统已经安装 Conda，可以跳过此步骤。

```bash
sudo apt update
sudo apt install -y git curl

curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
  -o /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda init bash
```

### 2. 克隆项目并创建环境

```bash
git clone https://github.com/Iron-Wph/Piano.git
cd Piano

conda env create -f environment.yml
conda run -n piano-hand python -m piano_hand.cli doctor --mute
```

如果环境已经存在，使用下面的命令同步依赖：

```bash
conda env update -f environment.yml --prune
```

### 3. 生成示例视频

默认演示曲是初级双手版《Twinkle, Twinkle, Little Star》。`demo`
命令默认读取 MusicXML，并固定生成完整 88 键静音视频：

```bash
conda run -n piano-hand python -m piano_hand.cli demo
```

视频输出位置：

```text
work/demo/output.mp4
```

再次生成时增加 `--force`：

```bash
conda run -n piano-hand python -m piano_hand.cli demo --force
```

### 验证 MIDI 输入

仓库同时提供内容等价的 MIDI 文件。通过 `--format midi` 运行同一首曲目：

```bash
conda run -n piano-hand python -m piano_hand.cli demo \
  --format midi \
  --output work/demo-midi
```

视频输出位置：

```text
work/demo-midi/output.mp4
```

两个示例文件都包含 12 小节、54 个音符和相同的双手编配：

- [MusicXML 示例](./examples/twinkle_twinkle_beginner.musicxml)
- [MIDI 示例](./examples/twinkle_twinkle_beginner.mid)
- [来源和编配说明](./examples/README.md)

示例旋律依据美国国会图书馆收藏的 1879 年公共领域乐谱记录重新录入，
并简化为适合本项目演示的右手旋律加左手全音符低音。仓库没有复制第三方
MIDI 或 MusicXML 编配。原始资料：
[Library of Congress item 2023832590](https://www.loc.gov/item/2023832590/)。

## Linux 生成自己的完整 88 键视频

完整键盘模式使用标准钢琴范围 A0–C8，即 MIDI 21–108。对自己的乐谱，
先运行 `analyze`，然后修改 `project.yaml`：

```bash
conda run -n piano-hand python -m piano_hand.cli analyze \
  "/path/to/song.musicxml" \
  --output work/full-keyboard \
  --mute

sed -i 's/keyboard_mode: local/keyboard_mode: full/' \
  work/full-keyboard/project.yaml

conda run -n piano-hand python -m piano_hand.cli validate work/full-keyboard
conda run -n piano-hand python -m piano_hand.cli render work/full-keyboard
```

完整键盘包含 88 个键，建议使用 1920×1080 分辨率。也可以直接编辑
`work/full-keyboard/project.yaml` 的 `render` 部分：

```yaml
render:
  width: 1920
  height: 1080
  fps: 30
  theme: dark
  keyboard_mode: full
  show_finger_numbers: true
  show_measure: true
  show_note_names: false
  random_seed: 0
```

修改后重新运行：

```bash
conda run -n piano-hand python -m piano_hand.cli validate work/full-keyboard
conda run -n piano-hand python -m piano_hand.cli render work/full-keyboard
```

## Linux 处理自己的乐谱

### 局部键盘

局部键盘会根据乐谱音域自动选择并放大可见琴键：

```bash
conda run -n piano-hand python -m piano_hand.cli build \
  "/path/to/song.musicxml" \
  --output work/song \
  --mute
```

MIDI 文件的用法相同：

```bash
conda run -n piano-hand python -m piano_hand.cli build \
  "/path/to/song.mid" \
  --output work/song \
  --mute
```

完整键盘流程见上文“Linux 生成自己的完整 88 键视频”。

## Windows 快速开始

在项目目录中创建环境并检查依赖：

```powershell
conda env create -f environment.yml
conda run -n piano-hand python -m piano_hand.cli doctor --mute
```

默认生成完整 88 键静音演示视频：

```powershell
conda run -n piano-hand python -m piano_hand.cli demo
```

使用 MIDI 版本验证同一首曲目：

```powershell
conda run -n piano-hand python -m piano_hand.cli demo `
  --format midi `
  --output work\demo-midi
```

## 键盘模式

在项目的 `project.yaml` 中设置：

```yaml
render:
  keyboard_mode: local
```

可用值：

| 值 | 效果 | 适用场景 |
| --- | --- | --- |
| `local` | 按乐谱音域显示局部键盘并自动缩放 | 看清指法和单键动作 |
| `full` | 显示标准 A0–C8 完整 88 键 | 展示双手整体位置和跨音区移动 |

切换模式后不需要重新执行 `analyze`，直接重新运行 `validate` 和 `render`。

## 修改自动指法

`analyze` 会创建以下主要文件：

```text
work/song/
├── project.yaml
├── timeline.json
├── fingering.csv
└── validation-report.json
```

编辑 `fingering.csv` 中的 `hand` 和 `finger`，然后重新验证并渲染：

```bash
conda run -n piano-hand python -m piano_hand.cli validate work/song
conda run -n piano-hand python -m piano_hand.cli render work/song
```

人工指法会覆盖自动生成结果。手指编号范围是 1–5。

## 生成有声视频

生成音频需要自行准备合法授权的 `.sf2` SoundFont。

> `demo` 命令为了保证开箱即用，固定生成静音视频。需要为同一首内置
> 《Twinkle, Twinkle, Little Star》生成有声视频时，请使用下面的
> `build` 命令，并且不要添加 `--mute`。

Linux：

```bash
export PIANO_HAND_SOUNDFONT="/path/to/piano.sf2"

conda run -n piano-hand python -m piano_hand.cli doctor \
  --soundfont "$PIANO_HAND_SOUNDFONT"

conda run -n piano-hand python -m piano_hand.cli build \
  examples/twinkle_twinkle_beginner.musicxml \
  --output work/demo-audio
```

使用 MIDI 示例生成有声视频：

```bash
conda run -n piano-hand python -m piano_hand.cli build \
  examples/twinkle_twinkle_beginner.mid \
  --output work/demo-midi-audio
```

Windows PowerShell：

```powershell
$env:PIANO_HAND_SOUNDFONT="C:\path\to\piano.sf2"

conda run -n piano-hand python -m piano_hand.cli doctor `
  --soundfont $env:PIANO_HAND_SOUNDFONT

conda run -n piano-hand python -m piano_hand.cli build `
  examples\twinkle_twinkle_beginner.musicxml `
  --output work\demo-audio
```

使用 MIDI 示例：

```powershell
conda run -n piano-hand python -m piano_hand.cli build `
  examples\twinkle_twinkle_beginner.mid `
  --output work\demo-midi-audio
```

输出文件分别位于：

```text
work/demo-audio/output.mp4
work/demo-midi-audio/output.mp4
```

使用 `--mute` 时不会生成音频，也不要求提供 SoundFont。没有 `--mute`
时，系统会调用 FluidSynth 将乐谱合成为 WAV，再由 FFmpeg 与视频合并。

可以使用 `ffprobe` 确认输出同时包含视频流和音频流：

```bash
ffprobe -v error \
  -show_entries stream=codec_type \
  -of default=noprint_wrappers=1 \
  work/demo-audio/output.mp4
```

正常结果应同时包含：

```text
codec_type=video
codec_type=audio
```

## 常用命令

```text
analyze   解析乐谱，创建 project.yaml、timeline.json 和 fingering.csv
validate  检查项目配置、指法、文件路径和依赖
render    渲染已有项目
build     依次执行 analyze、validate 和 render
demo      用内置入门曲生成完整 88 键静音演示视频
doctor    检查 Python、FFmpeg、FluidSynth、SoundFont 和目录权限
```

查看完整帮助：

```bash
conda run -n piano-hand python -m piano_hand.cli --help
conda run -n piano-hand python -m piano_hand.cli demo --help
conda run -n piano-hand python -m piano_hand.cli render --help
```

## 常见问题

### 输出目录已经存在

`analyze`、`build` 和 `demo` 默认不会覆盖已有项目。确认允许覆盖后增加：

```text
--force
```

### 只想生成静音视频

在 `analyze` 或 `build` 命令后增加：

```text
--mute
```

### 有声视频提示缺少 FluidSynth 或 SoundFont

先确认环境和 SoundFont 路径：

```bash
export PIANO_HAND_SOUNDFONT="/absolute/path/to/piano.sf2"
conda run -n piano-hand python -m piano_hand.cli doctor \
  --soundfont "$PIANO_HAND_SOUNDFONT"
```

`doctor` 中的 `ffmpeg`、`ffprobe`、`fluidsynth` 和 `SoundFont` 都应显示
`OK`。SoundFont 必须是可读取的 `.sf2` 文件，建议使用绝对路径。

### 修改了 `project.yaml`，但视频没有变化

确保修改的是目标项目中的 `project.yaml`，然后重新执行：

```bash
conda run -n piano-hand python -m piano_hand.cli validate /path/to/project
conda run -n piano-hand python -m piano_hand.cli render /path/to/project
```

### 完整键盘上的手比较小

这是完整显示 88 键的正常结果。将 `render.width` 和 `render.height`
设置为 `1920` 和 `1080`，可以提高键盘与手部细节的清晰度。

## 开发与测试

```bash
conda activate piano-hand
python -m pytest
python -m ruff check piano_hand tests
python -m mypy piano_hand
```

## 设计文档

- [MVP 方案](./MVP方案.md)
- [产品需求文档](./产品需求文档_PRD.md)
- [开发技术需求文档](./开发技术需求文档.md)
