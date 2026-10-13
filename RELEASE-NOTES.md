## Strata-T8 ComfyUI 1.1.2

新增 **Strata 全程视频分析 / Full Video Analyze**，直接接官方 VIDEO 或原始 IMAGE 批次。填写分析秒数与每秒观察帧数即可规划全程分段，例如 10 秒、24 FPS 规划约 240 帧；逐段采样与分析，再有界汇总，总帧数可超过 64。输出报告、JSON、证据、实际覆盖与用量，全部分段共享一次服务租约。

修复浮点时间边界漏帧、汇总证据范围及片段时间错位；中间观察默认 temperature:0。均匀背景可提供 CPU 主色与前景区域坐标，辅助核对颜色和位移；复杂画面不提供此统计，不作为对象跟踪。保留原有 12 个节点 ID、V3/V1 和协议 1，共 13 个节点。配套运行包 **0.1.42-t8.6**，模型与视觉权重可沿用。

**359 项 CPU/HTTP 回归通过**；真实官方 VIDEO / torch CPU 验证 240、288、360 帧覆盖，生成服务为模拟。SC117 IQ3_S / RTX 4060 Ti 16GB / 128GB RAM / 32K 上下文完成 2 秒、24 FPS、48 帧合成视频的 frames 与 native 实际推理，两者正确汇总红→蓝与白圆向右。复杂动作和真实长片质量尚未验收，两种模式都使用采样帧，不含音轨识别。

节点 ZIP 不含 Python、引擎或权重。安装、模型路径、来源与示例见 [README](https://github.com/T8mars/Comfyui-Strata-T8/blob/v1.1.2/README.md)。本次发布 GitHub Release；Registry 保留手动发布流程。

## Strata-T8 ComfyUI 1.1.1

修复视频分段汇总的证据范围与时间、空反推提示词、混合尺寸和透明帧；修正 MKV 时间偏移、尾部截取及列表拼接。服务备用重启后重新校验协议、并发、忙碌、视觉和原生能力，拒绝时不沿用旧清理权限。

官方 VIDEO 的文件、BytesIO、拼接与 Components 输入一致处理。PQ/HLG HDR 文件及非 sRGB Components 需先转换 SDR/sRGB；不提供自动 HDR tone mapping。支持 1–64 个真实采样帧与原生末帧配对，保留原始证据 ID；字幕或转写可作为文字输入。

**347 项 CPU/HTTP 回归通过**，实际官方 VIDEO / torch CPU 验证透明度、时间与 SDR 基线一致；本版未重跑模型 GPU 或真实长片质量验收。12 个节点 ID、V3/V1 和协议 1 保持兼容，配套 Strata-T8 **0.1.42-t8.3**；现有模型及视觉权重可继续使用。

节点 ZIP 不含 Python、引擎或权重，安装及模型路径见 README。本次发布 GitHub Release；Registry 保留官方工作流，手动选择后才发布。

## Strata-T8 ComfyUI 1.1.0

新增 Video Sample 和 Video Analyze，支持官方 VIDEO、IMAGE 批次及显式时间戳。提供概述、时间轴、分镜观察、问答和提示词反推，输出报告、JSON、证据图片和采样覆盖；支持分段与有界汇总，在同一模型加载会话完成。原生双帧模式须由 Strata 服务明确协商能力，使用 Strata-T8 **0.1.42-t8.2** 或更新版本。

真实 ComfyUI 0.38 队列完成视频分段、汇总和下游报告/图片保存；官方 ModelPatcher 同卡交接与 Strata 双进程清理通过。另通过 260 项回归、官方 VIDEO 变换/拼接 CPU 探针，以及 SC117 IQ3_S / RTX 4060 Ti 16GB 的短视频真值、旧文字/图片与取消验收。分发器拒绝任意层级或大小写的私人 roadmap.md。抽帧可能遗漏短暂动作，音频可附字幕或转写文本。

保留原有 10 个节点 ID、V3/V1 和协议 1，独立包不含 Python、主模型或视觉权重；运行包、模型路径和来源见 README。Publisher `t8star`，节点 `strata-t8`；Registry 审核状态以官方平台为准。

## Strata-T8 ComfyUI 1.0.7

修复 Release ZIP 和 Registry 安装后托管服务因缺少开发用 `meta.json` 而无法启动的问题。最低运行包版本集中在随包提供的 `version.json`，打包前核对节点版本、协议与最低运行包版本，防止错误兼容信息进入发行包。

新增 20 个场景，239 项节点与真实 HTTP 回归通过，无跳过；覆盖安装产物、取消期间的迟到连接、GPU 交接边界、64 项顺序批次、递归 Schema、面板及示例工作流。10 个节点 ID、ComfyUI V3/V1、协议 1 和最低运行包版本 `0.1.39-t8.14` 保持。模型、MTP 和视觉权重无需重新下载。证据见 docs/AUDIT-20-ROUND8-NODES.md。

Publisher `t8star`，节点 `strata-t8`。按官方流程发布新版本，Registry 审核状态以平台为准。

## Strata-T8 ComfyUI 1.0.6

完成第七组 20 个新焦点。修复结构化错误误重试、选项覆盖消息、JSON及无效批次在资源事务后才失败，以及显存小数阈值截断。通过 HTTP 状态和精确错误码识别可修复错误；全部请求在释放显存前校验。新增 23 方法，211 例回归通过。

保留 10 个节点 ID、ComfyUI V3/V1 和协议 1。支持文字、图像理解、结构化分镜、批处理和服务控制；托管模式要求 Strata-T8 0.1.39-t8.14 或更新版本。节点包不含 Python或权重，模型路径、安装与来源见 README。

Publisher t8star，节点 strata-t8，使用官方 Comfy Registry 发布流程。审核状态以平台为准，审核期间可使用 GitHub Release。逐轮证据见 docs/AUDIT-20-ROUND7-NODES.md。
