## Strata-T8 ComfyUI 1.0.4

第五组 20 轮联合检查，修复 9 类问题，新增 29 例；完整节点回归 160 例通过。修复嵌套 Schema 草案与引用继承、无效 Unicode、响应 framing 与大小预检、Windows HTTP 等待期间取消、未确认子进程归属和非同卡 Control 预检。配置面板刷新保留已有草稿，并保护异步操作的最新状态。旧草案混合 dependencies 的特定 anchor 组合提前报错，可改用 JSON Pointer。

保留 10 个节点 ID、ComfyUI V3/V1 接口及三类工作流：文字提示词、图像理解、结构化分镜和批处理。配套运行包为 Strata-T8 0.1.39-t8.12；模型安装、路径、来源和致谢见 README。节点 ZIP 不包含模型、Python 或运行环境。

Publisher `t8star`、节点 `strata-t8`，通过官方 Comfy Registry 发布流程提交；Registry 审核状态以平台为准，审核期间可安装 GitHub Release。逐轮证据见 docs/AUDIT-20-ROUND5-NODES.md。
