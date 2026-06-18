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

```bash
conda run -n piano-hand python -m piano_hand.cli build \
  examples/two_hand_scale.musicxml \
  --output work/demo \
  --mute
```

视频输出位置：

```text
work/demo/output.mp4
```

再次生成同一个项目时增加 `--force`：

```bash
conda run -n piano-hand python -m piano_hand.cli build \
  examples/two_hand_scale.musicxml \
  --output work/demo \
  --mute \
  --force
```

## Linux 生成完整 88 键视频

完整键盘模式使用标准钢琴范围 A0–C8，即 MIDI 21–108。

因为 `build` 会立即使用默认配置渲染，所以完整键盘需要分为
`analyze`、修改配置、`validate` 和 `render` 四步。

```bash
# 1. 解析乐谱并创建可编辑项目
conda run -n piano-hand python -m piano_hand.cli analyze \
  examples/two_hand_scale.musicxml \
  --output work/full-keyboard \
  --mute

# 2. 将局部键盘切换为完整 88 键
sed -i 's/keyboard_mode: local/keyboard_mode: full/' \
  work/full-keyboard/project.yaml

# 3. 检查项目配置和指法
conda run -n piano-hand python -m piano_hand.cli validate \
  work/full-keyboard

# 4. 渲染视频
conda run -n piano-hand python -m piano_hand.cli render \
  work/full-keyboard
```

视频输出位置：

```text
work/full-keyboard/output.mp4
```

完整键盘包含 88 个键，建议使用 1920×1080 分辨率。编辑
`work/full-keyboard/project.yaml`：

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

### 完整 88 键

```bash
conda run -n piano-hand python -m piano_hand.cli analyze \
  "/path/to/song.musicxml" \
  --output work/song-full \
  --mute

sed -i 's/keyboard_mode: local/keyboard_mode: full/' \
  work/song-full/project.yaml

conda run -n piano-hand python -m piano_hand.cli validate work/song-full
conda run -n piano-hand python -m piano_hand.cli render work/song-full
```

## Windows 快速开始

在项目目录中创建环境并检查依赖：

```powershell
conda env create -f environment.yml
conda run -n piano-hand python -m piano_hand.cli doctor --mute
```

生成局部键盘示例视频：

```powershell
conda run -n piano-hand python -m piano_hand.cli build `
  examples\two_hand_scale.musicxml `
  --output work\demo `
  --mute
```

生成完整 88 键视频：

```powershell
conda run -n piano-hand python -m piano_hand.cli analyze `
  examples\two_hand_scale.musicxml `
  --output work\full-keyboard `
  --mute

(Get-Content work\full-keyboard\project.yaml) `
  -replace 'keyboard_mode: local', 'keyboard_mode: full' |
  Set-Content -Encoding utf8 work\full-keyboard\project.yaml

conda run -n piano-hand python -m piano_hand.cli validate work\full-keyboard
conda run -n piano-hand python -m piano_hand.cli render work\full-keyboard
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

Linux：

```bash
export PIANO_HAND_SOUNDFONT="/path/to/piano.sf2"

conda run -n piano-hand python -m piano_hand.cli doctor \
  --soundfont "$PIANO_HAND_SOUNDFONT"

conda run -n piano-hand python -m piano_hand.cli build \
  examples/two_hand_scale.musicxml \
  --output work/demo-audio
```

Windows PowerShell：

```powershell
$env:PIANO_HAND_SOUNDFONT="C:\path\to\piano.sf2"

conda run -n piano-hand python -m piano_hand.cli doctor `
  --soundfont $env:PIANO_HAND_SOUNDFONT

conda run -n piano-hand python -m piano_hand.cli build `
  examples\two_hand_scale.musicxml `
  --output work\demo-audio
```

使用 `--mute` 时不会生成音频，也不要求提供 SoundFont。

## 常用命令

```text
analyze   解析乐谱，创建 project.yaml、timeline.json 和 fingering.csv
validate  检查项目配置、指法、文件路径和依赖
render    渲染已有项目
build     依次执行 analyze、validate 和 render
doctor    检查 Python、FFmpeg、FluidSynth、SoundFont 和目录权限
```

查看完整帮助：

```bash
conda run -n piano-hand python -m piano_hand.cli --help
conda run -n piano-hand python -m piano_hand.cli render --help
```

## 常见问题

### 输出目录已经存在

`analyze` 和 `build` 默认不会覆盖已有项目。确认允许覆盖后增加：

```text
--force
```

### 只想生成静音视频

在 `analyze` 或 `build` 命令后增加：

```text
--mute
```

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
