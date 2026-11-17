# 从验收底片到 Lemo

在仓库根目录操作。每单使用一个新 `runs/<本单目录>/`，保存通过的底片或引用其可访问路径。

1. 执行 `python tools/workflow.py check` 取得 `reverse_collector`、`remake_skill`、`lemo_skill`、`preview_tool` 等实际路径。对已通过的底片运行 `python -B -X utf8 "<reverse_collector>" "<底片.mp4>" "<本单目录>/shot-evidence"`。逐对复核候选切帧，从真实画面写镜头。
2. 在本单目录写 `brief.json`。其中路径可用相对于本单目录的路径。

   - `base` 是实际底片路径。`base_review` 提供 `status`（已通过为 `passed`）、该版 `sha256`、`record`（具体审看依据或记录位置）。沿用旧底片试跑时，在 `record` 据实说明。
   - `shot_evidence` 指向原反推工具产生的 `evidence.json`。`shots` 是目视核完的镜头列表，每条有 `start_frame`、`end_frame` 和 `description`。帧号从 0 开始，区间含首帧、不含末帧，连续覆盖底片末帧。
   - `speech` 有 `language`、`kind`（`speech` 或 `subtitles`）、`file` 和 `record`。文件为 JSON 数组，每条有 `start`、`end`（秒）和 `text`。转写标明实际核听范围。
   - `selling_points` 是 `claim` 和 `evidence` 的列表，证据写实拍镜头、商品资料或来源位置。`requirements` 是本次要求的字符串列表。
3. 运行 `python tools/workflow.py prepare runs/<本单目录>/brief.json`。输出 `handoff.json`，含底片指纹、按真实帧时钟换算的镜头段、口播或字幕、卖点和需求。程序校验文件和时序，画面与商品判断由实际审看来完成。
4. 在 `film/` 制作 Lemo 工程。页面依 Lemo 的 `window.render(t)` 和 `window.DUR` 约定，供阅读测试的 `window.TEXTS(t)` 返回字幕框。透明描线和文字叠在底片的对应帧上。命令按底片生成从 `assets/frames/00000.jpg` 开始的序列。`film/timeline.json` 记录 `base_sha256`、`frames`、数值 `fps` 和 `labels`。每条标签有 `id`、`text`、`from`、`to`，坐标按本片代码保存。可用 `render_size` 指定同比例宽高，例如 `[1080, 1920]`。
5. 运行 `python tools/workflow.py render runs/<本单目录>/handoff.json`。它使用已有 Lemo 做阅读检测与画面渲染，用 FFmpeg 回挂底片声音，检查全片解码、画面帧数、音频包内容及成片时长，并输出 `final.mp4` 和 `render-check.json`。动效修正后可加 `--output final-v2.mp4`，复用同一底片并保留已渲染版本。记录实际审看通过后，运行 `python "<preview_tool>" --video "<通过的MP4>" --preview-dir "<当前对话的可写预览目录>"`。按原项目规则使用该次返回的预览和文件链接。
