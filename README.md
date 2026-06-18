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

```powershell
conda env create -f environment.yml
conda activate piano-hand
python -m piano_hand.cli doctor --mute
```

依赖更新后，可以同步现有环境：

```powershell
conda env update -f environment.yml --prune
conda activate piano-hand
```

Conda 环境已经安装 FluidSynth。若需要生成钢琴音频，还需自行准备合法授权的
SoundFont，并通过环境变量指定：

```powershell
$env:PIANO_HAND_SOUNDFONT="C:\path\to\piano.sf2"
python -m piano_hand.cli doctor --soundfont $env:PIANO_HAND_SOUNDFONT
```

SoundFont 文件不会提交到仓库。

## 最快验证

当前机器没有 FluidSynth 时，可以生成静音教学视频：

```powershell
python -m piano_hand.cli build examples\two_hand_scale.musicxml --output work\demo --mute
```

分步处理：

```powershell
python -m piano_hand.cli analyze song.musicxml --output work\song --mute
python -m piano_hand.cli validate work\song
python -m piano_hand.cli render work\song
```

修改 `work\song\fingering.csv` 后再次运行 `validate` 和 `render`，人工指法会覆盖
自动结果。

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
