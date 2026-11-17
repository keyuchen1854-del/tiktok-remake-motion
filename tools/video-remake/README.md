# 两条工作流的共用分段工具

用于 Gem 改写后的静音分段、素材映射、Capsule Cinema 分镜校验、H3 任务跟踪和 FFmpeg 回拼。需要 Python 3.11+、Pydantic 2、PATH 中的 FFmpeg 和 FFprobe。参考渠道为秘塔 H3，使用其他渠道时按该渠道实际能力整理和提交。

## 1. Gem 确定时间线后可以提前切段

计划 JSON 中 `source` 为相对计划目录的原片路径，`segments` 的每项包含 `id` 和 `frames`。帧号用固定帧率原片的零基半开区间，所有段连续覆盖全片。分段优先保全关键动作并遵守渠道时长限制。

```powershell
python tools/handoff.py cut --plan <分段JSON> --work <新分段目录>
```

这一步输出静音片段、帧号与哈希，和关键帧生成可独立推进。保留完整源音轨。用户要求换口播时保留原声证据并准备独立目标音轨。

## 2. 参考图就绪后整理和校验

同一个计划补充 `mode`（`viral` 或 `light`）、`references`（编号到相对图片路径的映射）以及每段的 `reference_id`、`duration`（H3 输出整数秒）和 `prompt`（相对 UTF-8 文件路径）。复刻爆款额外填 `representative` 段号。参考图是干净单图。

```powershell
python tools/handoff.py prepare --plan <完整计划JSON> --work <新制作目录> --cuts <提前分段目录>
```

`--cuts` 可省，脚本按计划现场切段。提前分段会核对原片 SHA-256、帧率、段界和分段内容，匹配时直接复用。两条流程都会生成 `capsule-storyboard.json`、`capsule-qa.json` 和 `handoff.json`。后者记录段号、帧区间、秒数、附件路径和版本哈希。结构通过后仍要目视核对画面和商品。

## 3. H3 提交、审片与回拼

- 已获上传及费用授权时，用 `python tools/handoff.py submit --work <制作目录> --segment G01`。复刻爆款在宫格确认后加 `--grid-approved`，先提交代表段。代表段出片且验收通过后，提交其余段时再加 `--representative-approved`。轻量版在同批已授权范围内直接提交。已有授权的自动调用可传 `--authorized`。
- 当前适配器每段一张干净参考图和一段静音原片。客户或本机自己的密钥通过 `METASO_MINIMAX_API_KEY` 或终端隐藏输入提供。任务状态在 `generated/<段号>.state.json`。中断用 `resume` 查询；状态未明确时先核对任务。
- 审片时按段号写实际失败时间点、原因和验收结果，和任务状态保存在同一制作目录。仅在已授权范围内重做问题段一次。可用 `--retry` 加 `--retry-image` 或 `--retry-prompt`，产物另存。
- 回拼 JSON 包含 `source`、`audio` 及 `shots`。每镜填写 `source_frames`、已验收视频 `file` 和 `generated_frames`。保留原声时用默认 `source`，换口播可用 `"audio": "external", "audio_file": "已对齐口播.wav"`，无声用 `none`。按原时码处理可分离的字幕和图形。

```powershell
python tools/remake.py assemble --plan <拼接JSON> --output <成片MP4>
```

从本目录执行上述命令。源片帧数、全片解码和音轨属于技术自检，商品、手部动作、原音同步与成片衔接需要看片确认。


## 本机图片编辑

按[前段项目规则](../../engine/AGENTS.md)处理内置出图的失败和补图条件。符合本机补图条件时，使用已经配置的 ComfyUI 和 **Qwen-Image-Edit-2511 Q4_K_M** 工作流。先确认权重、文本编码器、VAE、节点、运行队列和可用显存。以失败单图对应的原关键帧为底图，用本次授权的商品或人物图作参考。补图通过商品结构、手部遮挡、动作和人物一致性复验后才复用。

有该分支需求而本机缺少工作流、权重或节点时，说明缺项和预计下载量，按用户授权配置。仓库开箱自检覆盖反推、分段校验、回拼和 Lemo。
