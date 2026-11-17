# 反推＋短动效

这是一个可在 Codex 中复用的带货视频工作流，入口是 `$tiktok-remake-motion`。先走原有的带货出单复刻流程，回拼底片通过后自动接 Lemo-Opuscar，根据画面、口播和已核实的商品卖点加文字、描线和短动效，最后交一条完整 MP4。

## 在同事的 Codex 中开始

取得仓库访问权限后，把仓库链接和下方这句发给 Codex。也可以先克隆仓库，再在 Codex 中打开仓库根目录。

> 请克隆并打开这个仓库，读 README 和 AGENTS.md。先运行环境检查，告诉我缺什么。配置完成后，用 $tiktok-remake-motion 处理我给的带货参考视频。

只发视频、没说改动时，机器人先问人物、发型、服装、背景想怎么改以及哪些沿用原片，收到答复后处理。带货出单复刻继续按原流程确认整张宫格、审看 H3 代表段、验收回拼。验收后的底片自动进入短动效步骤。已有验收底片也可以从短动效开始。

## 首次配置

准备 Git、Python 3.11+、Node.js 20+、具备 `libx264` 的 FFmpeg 7+ 和同套 FFprobe。让这些工具在当前终端可用，再从仓库根目录执行 `python -B -X utf8 tools/workflow.py check`。已有组件直接复用，下载或安装前由使用者确认缺项和用途。

以下是 Windows PowerShell 命令。每行成功后继续下一行。已有满足要求的 Python 环境时可继续使用。

```powershell
python -m venv .venv
$env:Path = (Resolve-Path .venv/Scripts).Path + [IO.Path]::PathSeparator + $env:Path
python -m pip install -r requirements.txt
```

渲染使用固定版本的 Lemo 库和它的 Chromium。已有可用 Lemo 库时，可以在 `local-paths.json` 里填 `lemo_library`，使用已有的兼容浏览器也可填 `chromium`。配置示例见 [local-paths.example.json](local-paths.example.json)。未指定 Chromium 时使用 Playwright 安装的默认版本。

```powershell
New-Item -ItemType Directory -Force .deps | Out-Null
git clone --filter=blob:none --no-checkout https://github.com/lemomo-ai/lemo-opuscar.git .deps/lemo-opuscar
git -C .deps/lemo-opuscar sparse-checkout set --no-cone '/*' '!/styles/*/demo/' '!/styleboard/'
git -C .deps/lemo-opuscar checkout --detach 00a7f9ec99c8686265a8ca4ed9a3fdaaf27ea6f4
npm.cmd --prefix .deps/lemo-opuscar ci --ignore-scripts --no-audit --no-fund
node .deps/lemo-opuscar/node_modules/playwright-core/cli.js install chromium-headless-shell
python -B -X utf8 tools/workflow.py check
```

以上安装的是画面渲染组件，成片音轨沿用已验收底片。需要在 Codex 中验证可点播预览时，另用 Python `playwright` 和它匹配的 Chromium（`python -m playwright install chromium-headless-shell`）。能否在对话内嵌播放器以当前客户端为准。

`check` 返回 `local_tools_ready` 表示本地入口与 Chromium 可用。完整视频复刻还需要当前 Codex 可用的内置图片生成能力和使用者自己的秘塔 H3 权限。按 [分段和回拼说明](tools/video-remake/README.md) 在本机提供 `METASO_MINIMAX_API_KEY`，或在支持隐藏输入的本地终端提供密钥。上传素材及付费提交沿用该单授权。本机 Qwen 只在 [对应补图分支](engine/AGENTS.md#本项目视频制作流程) 需要，先查本机 ComfyUI 与模型。

## 工作流与文件

- [入口技能](.agents/skills/tiktok-remake-motion/SKILL.md) 与 [项目规则](AGENTS.md) 控制开工、原复刻验收和最终成片交付。
- [带货出单复刻](engine/.agents/skills/tiktok-sales-video-remake/SKILL.md) 与 [视频反推](engine/.agents/skills/video-reverse/SKILL.md) 随仓库提供，`.deps/` 中的 Lemo 由本机独立安装。
- [Lemo 交接格式](.agents/skills/tiktok-remake-motion/references/handoff.md) 给出 `prepare`、`render`、素材版本、帧时钟和音轨校验的用法。
- 每条视频使用自己的 `runs/` 目录，原片、关键帧、工程、成片和本机配置保留在本地。第三方组件按随附许可或上游许可使用。 [Capsule Cinema 来源](tools/video-remake/vendor/capsule-cinema/ORIGIN.md) · [Lemo-Opuscar 上游许可](https://github.com/lemomo-ai/lemo-opuscar/blob/00a7f9ec99c8686265a8ca4ed9a3fdaaf27ea6f4/LICENSE)

本地已验证原复刻的离线分段与回拼，以及用一条约 40 秒的已验收底片接入 Lemo、导出全片和检查原音轨。新机器环境与真实 H3 首单需要由实际使用方验收。
