## Strata-T8 ComfyUI 1.0.3

第四组 20 轮联合检查，修复 9 类问题、新增 26 例；完整节点回归 131 例通过。按 JSON Schema 声明选择草案，提前检查服务要求的 object 根；拒绝重复 JSON 成员。修复同卡清理停止服务后未重启、Control 成功释放后查询死亡端口、owner 巨整数、目录误列为档案、Origin 格式及面板保存与队列动作并发。

Structured 和 Vision 非空 Schema 根须显式写 type:object；Vision 留空仍为普通图片分析。保留 10 个节点 ID、V3/V1 适配和 Strata-T8 协议 1。模型、运行包及本机连接档案独立保存；建议同时更新 Strata-T8 运行包。

通过 ComfyUI-Manager / Comfy Registry（Publisher `t8star`，节点 `strata-t8`）管理版本；也提供不含模型和运行环境的节点 ZIP 与 SHA256。安装、模型路径、下载来源与致谢见 README。
