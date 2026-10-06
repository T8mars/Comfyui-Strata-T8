## Strata-T8 ComfyUI 1.0.2

第三组 20 轮联合检查，修复 13 类问题、新增 21 例；完整节点回归 105 例通过。修复循环 Schema 异常、档案非法扩展值、托管 argv 归属和 readiness 回滚、卸载兜底、同卡预清理、HTTP 截断响应、timeout 边界及极窄图片像素限制。

面板刷新保留草稿，过期响应不覆盖新状态；新增明确的 API key 清除选项，external 清空旧 key，managed 重新生成。官方 ComfyUI V3 API/CPU tensor、动态档案选项及 PNG 探针通过。保留 10 个稳定节点 ID、V3/V1 和协议 1。

保留 10 个节点 ID 和 Strata-T8 协议 1。模型、运行包及本机连接档案独立保存。

通过 ComfyUI-Manager / Comfy Registry（Publisher `t8star`，节点 `strata-t8`）管理版本；也提供不含模型和运行环境的节点 ZIP 与 SHA256。安装、模型路径、下载来源与致谢见 README。
