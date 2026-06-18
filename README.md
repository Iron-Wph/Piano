# Piano Hand

将 MIDI 或 MusicXML 转换为可编辑的虚拟手钢琴教学视频。本 MVP 完全在本地运行，
不包含网页 UI、账号或云端服务。

## 当前能力

- MIDI、MusicXML、MXL 解析与统一时间轴。
- 自动左右手分配和动态规划指法。
- 通过 `fingering.csv` 人工覆盖手和指法。
- 二维虚拟手、键盘高亮、手指编号与 MP4 渲染。
- FFmpeg 媒体编码和输出校验。
- 可选 FluidSynth + SoundFont 钢琴音频。

## 安装

项目使用 Conda 管理 Python、FFmpeg、FluidSynth 和开发依赖。

运行不需要 GPU，普通 CPU 电脑即可使用。

### Windows

首次创建环境：

```powershell
conda env create -f environment.yml
```

环境已经存在时，同步依赖：

```powershell
conda env update -f environment.yml --prune
```

检查运行环境：

```powershell
conda run -n piano-hand python -m piano_hand.cli doctor --mute
```

### Ubuntu Linux

以下命令适用于 Ubuntu 22.04/24.04。先安装 Git、curl 和 Miniconda：

```bash
sudo apt update
sudo apt install -y git curl
curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
  -o /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
eval "$("$HOME/miniconda3/bin/conda" shell.bash hook)"
conda init bash
```

重新打开终端后，克隆项目并创建环境：

```bash
git clone https://github.com/Iron-Wph/Piano.git
cd Piano
conda env create -f environment.yml
conda run -n piano-hand python -m piano_hand.cli doctor --mute
```

环境已经存在时：

```bash
conda env update -f environment.yml --prune
```

## 一键运行

以下命令会使用仓库内的示例乐谱生成静音教学视频。

### Windows PowerShell

首次运行，创建环境并生成视频：

```powershell
cmd /c "conda env create -f environment.yml && conda run -n piano-hand python -m piano_hand.cli build examples\two_hand_scale.musicxml --output work\demo --mute"
```

环境已经创建后，一键生成视频：

```powershell
conda run -n piano-hand python -m piano_hand.cli build examples\two_hand_scale.musicxml --output work\demo --mute --force
```

输出文件位于：

```text
work\demo\output.mp4
```

### Ubuntu Linux

首次运行，创建环境并生成视频：

```bash
conda env create -f environment.yml && \
conda run -n piano-hand python -m piano_hand.cli build \
  examples/two_hand_scale.musicxml --output work/demo --mute
```

环境已经创建后，一键生成视频：

```bash
conda run -n piano-hand python -m piano_hand.cli build \
  examples/two_hand_scale.musicxml --output work/demo --mute --force
```

输出文件位于：

```text
work/demo/output.mp4
```

### 处理自己的乐谱

Windows：

```powershell
conda run -n piano-hand python -m piano_hand.cli build "C:\path\to\song.musicxml" --output work\song --mute
```

Ubuntu：

```bash
conda run -n piano-hand python -m piano_hand.cli build \
  "/path/to/song.musicxml" --output work/song --mute
```

支持的输入格式为 `.mid`、`.midi`、`.musicxml`、`.xml` 和 `.mxl`。

## 生成有声视频

Conda 环境已经安装 FluidSynth。生成钢琴音频还需要自行准备合法授权的
`.sf2` SoundFont 文件。

Windows PowerShell：

```powershell
$env:PIANO_HAND_SOUNDFONT="C:\path\to\piano.sf2"
conda run -n piano-hand python -m piano_hand.cli doctor --soundfont $env:PIANO_HAND_SOUNDFONT
conda run -n piano-hand python -m piano_hand.cli build examples\two_hand_scale.musicxml --output work\demo-audio
```

Ubuntu：

```bash
export PIANO_HAND_SOUNDFONT="/path/to/piano.sf2"
conda run -n piano-hand python -m piano_hand.cli doctor \
  --soundfont "$PIANO_HAND_SOUNDFONT"
conda run -n piano-hand python -m piano_hand.cli build \
  examples/two_hand_scale.musicxml --output work/demo-audio
```

SoundFont 文件不会提交到仓库。

## 分步运行与修改指法

```powershell
conda run -n piano-hand python -m piano_hand.cli analyze song.musicxml --output work\song --mute
conda run -n piano-hand python -m piano_hand.cli validate work\song
conda run -n piano-hand python -m piano_hand.cli render work\song
```

修改 `work\song\fingering.csv` 后，再次运行 `validate` 和 `render`，人工指法会覆盖
自动结果。Ubuntu 下将路径分隔符 `\` 改为 `/` 即可。

## 测试

```powershell
conda activate piano-hand
python -m pytest
python -m ruff check piano_hand tests
python -m mypy piano_hand
```

## 设计文档

- [MVP方案.md](./MVP方案.md)
- [产品需求文档_PRD.md](./产品需求文档_PRD.md)
- [开发技术需求文档.md](./开发技术需求文档.md)
