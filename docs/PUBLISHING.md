# 维护与发布

遵循 [ComfyUI 官方发布流程](https://docs.comfy.org/registry/publishing)。Publisher `t8star`，唯一节点 ID `strata-t8`；本仓库 Actions Secret `REGISTRY_ACCESS_TOKEN` 由 Publisher 的 Registry API key 配置。

更新代码后运行 `python tools/bump_version.py 1.0.1`（替换目标版本），同步 pyproject.toml、meta.json 和 version.json，更新 RELEASE-NOTES.md，提交并推送 main。不可修改已发布 Registry 版本。

工作流先执行 Windows/Linux 回归与跨仓 HTTP 集成，再构建不含模型的节点 ZIP、发布 GitHub Release，调用官方 `Comfy-Org/publish-node-action` 上传 Registry。也可在 Actions 手动触发；GitHub Release 已存在时保留资产。发布后检查 Registry 版本状态，安全扫描未完成不应称为已通过验证。

测试：`STRATA_SOURCE_DIR` 指向独立 Strata-T8 源码目录，运行 `python -m unittest discover -s tests -q`。未提供该目录时仅跳过 HTTP 集成并明确显示；CI 固定源码版本，整合包 CI 另对当前源码与已发布节点运行集成。

`comfy node validate` 和 `comfy node pack` 使用官方 CLI 检查与打包。`.comfyignore` 排除开发文件；模型、私密档案与环境变量文件不纳入 Git 或发行物。节点不安装推理运行库，上游代码同步仍由 Strata-T8 仓库承担。
