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
