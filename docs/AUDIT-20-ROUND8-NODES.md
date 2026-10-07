# ComfyUI Strata-T8 第八组 20 轮检查

日期：2026-10-08。不可变节点基线为 1.0.6，提交 `3032d17eb7aedd2a0728d5073640e309464c46f2`；运行包基线为 Strata-T8 `0.1.40-t8.2`，原生引擎 `0.1.40.2`。先读取 AGENTS、meta、features、pyproject、version、README、源码及既有测试，未找到项目 SKILL.md。主 Agent 与一个子 Agent 分别检查运行包和独立节点；本报告只记录节点的 20 个场景，重复运行既有回归不计轮数。

发现并修复 **2 类打包根因**。第一类是托管入口依赖未随包发行的开发元数据，影响 Release ZIP 和 Registry 安装。第二类是打包只核对 meta 与 pyproject，允许不同版本或无效兼容信息的 version.json 进入发行物。节点版本正常升为 **1.0.7**；最低运行包版本仍为 `0.1.39-t8.14`。10 个 node ID、V3/V1 适配、协议 1、HTTP 请求与 GPU 交接行为保持。

## 实际验证

- 在 Python 3.12.7 / Node.js 22.19.0 / Windows 上，设置 `STRATA_SOURCE_DIR=E:\Strata`。修改前完整 **219 tests OK，30.681s**。新增 `tests/test_audit_round8.py` 恰好 **20 个方法**；新增场景 **20 tests OK，3.530s**，完整 **239 tests OK，33.958s，无 skip**。版本升为 1.0.7 后完整 **239 tests OK，34.835s，无 skip**；补充 stop 的四字节共享前缀后，本组最终 **20 tests OK，3.652s**。日志保存在主仓忽略目录 `.portable-build/audit8-20261008/node-unit-full.log`、`node-unit-v107.log`、`node-round8-final.log`。
- 用 Git 从上述不可变 HEAD 重建整个临时仓库，实际执行其旧 builder、Git 文件白名单、ZIP 写入与解压，再导入解压后的 core。旧包 `meta.json=false`、`version.json=true`，对真实运行包目录的 `Managed.runtime_version()` 失败；修复后的解压包首次读到 `0.1.40-t8.2`，主 Agent 更新工作树后再次读到 `0.1.40-t8.3`。仅调用离线元数据入口，没有启动推理或 GPU。
- 同一旧仓库把 shipping version.json 改为 `0.0.0`，旧 builder 仍生成名为 1.0.6 的 ZIP；新 builder 在创建 dist 前抛出明确错误。前后脚本与 JSON 为 `.portable-build/audit8-20261008/node_before_after.py`、`node-before-after.json`，并保留小型 `node-before.zip` / `node-after.zip`。两个缺陷均有旧 HEAD 失败行为与修复后行为，未把所有正向检查称作基线 BUG。
- 真实随机 loopback HTTP 用 Strata 当前服务、ByteTokenizer 与 ResidentEngine/MockEngine，验证 reasoning-only、Unicode stop 和 64 项顺序批次。SSE 反例使用真实 HTTP socket。未下载模型、调用原生进程、初始化 CUDA、修改真实档案或停止用户服务。
- 面板使用实际 JavaScript handlers 和 Node.js，DOM/API 为受控替身，沿用既有完成标记与 watchdog。GPU 数字、owned process、原生启动边界为替身，不将其称为真实显存释放验收。
- 复用既有官方 ComfyUI 0.38.0 V3/CPU 探针验证 1.0.7：10 IDs、真实 HTTP typed repair、Structured/Batch 列表契约、CPU requires_grad RGBA 编码、Control 透传均通过；Python 3.11.6 / Torch 2.7.0+cu128，`cuda_initialized:false`。日志 `node-official-v3-cpu.log`。官方 io 与 CPU Torch 实际执行，生成器和 server/mm 入口为替身；此额外探针不另计轮数。

## 20 个检查场景

对应测试方法以 `test_r01_` 至 `test_r20_` 开头，每轮检查不同行为或发行入口。

| 轮次 | 源码与场景 | 结论与验证范围 |
|---|---|---|
| R01 | core.Managed.runtime_version、tools/build_release：真实构建并解压 ZIP，再从包内执行 managed.ensure 至原生准备边界。 | 旧入口寻找未发行 meta.json 失败。最低版本移至已发行 version.json；ZIP 不加开发元数据，实际到达替身 Popen。 |
| R02 | .comfyignore、version/meta：Registry 使用自己的 ignore 规则。 | core、version、nodes、入口均保留，meta 仍排除；兼容信息只在 shipping version，修复覆盖此安装入口。此轮验证规则，不冒充远端 Registry 发布或审核。 |
| R03 | tools/build_release：shipping version 为 0.0.0，另外两处是当前发行版本。 | 旧 builder 可写身份不一致的 ZIP；现明确 ValueError，dist 尚未创建。正常版本由项目 bump_version 同步。 |
| R04 | tools/build_release：protocol=true/2，最低版本为 null、四段版本或含尾换行。 | 现全部在写 ZIP 前拒绝；protocol 必须是整数 1，最低版本必须符合运行包契约。五反例归于打包元数据根因。 |
| R05 | Managed.ensure：相同 fingerprint 与 instance，运行包 0.1.40-t8.2 / engine 0.1.40.2。 | 正常复用已托管实例，无 stop 或新原生准备；process/client 为替身。 |
| R06 | Managed.owned/stop：记录 PID 相同但创建时间从 7 变为 8。 | 不枚举子进程，不 terminate/kill，不删除所有权记录；防止误停 PID 复用后的其它服务。 |
| R07 | _generate：同卡档案连到 protocol 1、serving=2 服务。 | 在 ComfyUI offload、chat 和 cleanup 前拒绝，只有 status 调用；保持单并发要求。 |
| R08 | cleanup：第一次 status in_flight=1，随后归零。 | 等忙请求结束才执行 unload，再确认 status；cleanup check=None 不继承用户取消。时间与服务响应为替身。 |
| R09 | _generate.check：baseline 后 ComfyUI 增加 128MiB+1 byte 分配。 | 不提交 chat，抛出背景 GPU 冲突并执行 cleanup；没有实际 GPU 测量。 |
| R10 | Client.request：JSON 编码工作尚未完成时取消。 | 编码线程放行后退出并关闭 connection，不 connect、不晚发请求；等待 worker 关闭事件。 |
| R11 | Client.request：connect 尚未返回时取消，之后连接才建立。 | conn.auto_open=0，worker 关闭连接且不发 POST；与读取 headers/body 期间取消是不同窗口。 |
| R12 | Client.request：非流式入口误收 SSE delta 与 DONE 文本。 | 实际 HTTP JSON parser 拒绝，不输出 partial、不变为 StructuredServiceError；key 不出现在错误中。节点固定 stream:false，不实现 SSE 客户端。 |
| R13 | StrataText→真实 HTTP：高 reasoning，reply 只含 analysis 与结束 think。 | 不把 reasoning 送给下游提示词，报告 no final answer；ResidentEngine 关闭、in_flight=0。 |
| R14 | StrataBatch→真实 HTTP：多字节 🚀END 与前缀 🚀E 两个 stop。 | 输出只留 stop 前前缀、index 保序，两次请求仅一次 load/unload。共享前缀包含四字节 UTF-8 字符；实际服务 parser，生成 token 为替身。 |
| R15 | StrataBatch→真实 HTTP：上限 64 项，各有唯一 question/answer。 | 64 个 outputs/custom/paired 保序且请求计数 64，一次驻留事务、最后释放。边界批次实际执行，不重复跑同一请求冒充多轮。 |
| R16 | StrataImageBatch→encode_images：第九张图片。 | 进入 CPU 转换前拒绝，不调用 numel/detach/generate；图片对象为替身。 |
| R17 | nodes.check_schema/validated：2019-09 recursiveAnchor / recursiveRef 的三级对象。 | 合法本地递归通过，第三层错误类型拒绝；不远端检索 Schema。 |
| R18 | Extract/Number：正负 9007199254740993 与 JSON boolean。 | Extract 字符串保持整数精度；boolean 按 boolean 输出且不能充当 Number。全程无模型调用。 |
| R19 | web/strata.js：状态请求先发，卸载入队回执后到，旧状态最后返回；密码框有草稿。 | 最新队列回执保持，旧状态不覆盖；密码草稿保留，两个请求均不包含草稿 key。实际 JS / API 替身。 |
| R20 | examples 与节点 INPUT_TYPES/RETURN_TYPES：三个 API workflow 的连接、必填输入、输出索引。 | 节点必填项齐全、链接类型/索引匹配，不包含 API key、URL、运行包或个人模型目录。只静态检查图定义，不称为本轮 GPU 绘图。 |

## 修改与限制

生产修复集中在 `core.py`、`version.json`、`meta.json`、`tools/build_release.py`。三处旧 fixture 改读 version.json；打包 fixture 用真实 version 数据，原有私密文件排除断言保留。README、RELEASE-NOTES 更新当前修复范围，过去实机测量保留其原版本背景。

本地生成 ZIP 仅用于审查，没有覆盖已发布的 1.0.6 资产，也没有提交、推送、发布或重发 Registry 审核。1.0.7 发布、CI 固定源码版本和最终运行包版本由主 Agent 在交叉复核后协调。

已有外部 API 在状态检查之间启动任务的竞争，以及旧草案 mixed dependencies + anchor 的第三方兼容边界保持。20 个场景不证明不存在其它 BUG；本报告中的替身结果不替代最终冻结版本的实际 GPU 工作流验收。
