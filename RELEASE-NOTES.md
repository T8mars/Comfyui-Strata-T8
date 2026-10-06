## Strata-T8 ComfyUI 1.0.1

第二次 20 轮联合检查，修复 14 类问题并新增 29 例回归。修复 JSON 指数溢出、请求完成时的超时漏检、HTTPResponse 关闭、非 JSON 错误诊断、服务状态校验、同卡并发控制、卸载确认和取消窗口。

Schema 校验支持合法本地引用及字面数据，并在推理前拒绝缺失或外部引用；改善面板队列错误、损坏连接档案恢复和托管相对路径处理。84 例完整回归通过，并验证官方 ComfyUI V3 API、CPU 图片张量、独立 ZIP 加载及三类实际绘图。发行检查同步排除二进制和 ONNX 模型。

保留 10 个节点 ID 和 Strata-T8 协议 1。模型、运行包及本机连接档案独立保存。

通过 ComfyUI-Manager / Comfy Registry（Publisher `t8star`，节点 `strata-t8`）管理版本；也提供不含模型和运行环境的节点 ZIP 与 SHA256。安装、模型路径、下载来源与致谢见 README。
