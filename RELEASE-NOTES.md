## Strata-T8 ComfyUI 1.0.5

完成第六组 20 项联合检查。修复视觉关闭时的提前显存释放、服务忙时自动清理、释放后状态复核、托管运行包版本预检、准备进程消失时的诊断、锁异常及 HTTP 重定向处理。保存档案前校验 Unicode 和 JSON 成员名称；面板传送原始 JSON，避免重复字段在浏览器中被抹掉。新增 28 个方法，188 例回归通过。

保留 10 个节点 ID、ComfyUI V3/V1 接口和协议 1，支持文字提示词、图像理解、结构化分镜和批处理。托管模式需要 Strata-T8 0.1.39-t8.13 或更新版本；外部模式继续支持兼容协议的本地服务。模型路径、安装方式及来源见 README。节点包不含 Python 运行库或模型。

Publisher `t8star`、节点 `strata-t8`，通过官方 Comfy Registry 发布流程提交；Registry 审核状态以平台为准，审核期间可安装 GitHub Release。逐项证据见 docs/AUDIT-20-ROUND6-NODES.md。
