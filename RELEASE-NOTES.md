## Strata-T8 ComfyUI 1.0.6

完成第七组 20 个新焦点。修复结构化错误误重试、选项覆盖消息、JSON及无效批次在资源事务后才失败，以及显存小数阈值截断。通过 HTTP 状态和精确错误码识别可修复错误；全部请求在释放显存前校验。新增 23 方法，211 例回归通过。

保留 10 个节点 ID、ComfyUI V3/V1 和协议 1。支持文字、图像理解、结构化分镜、批处理和服务控制；托管模式要求 Strata-T8 0.1.39-t8.14 或更新版本。节点包不含 Python或权重，模型路径、安装与来源见 README。

Publisher t8star，节点 strata-t8，使用官方 Comfy Registry 发布流程。审核状态以平台为准，审核期间可使用 GitHub Release。逐轮证据见 docs/AUDIT-20-ROUND7-NODES.md。
