# ComfyUI Strata-T8 第二组 20 轮检查

日期：2026-10-06。开始状态为节点 v1.0.0、运行包 0.1.39-t8.7 已发行。

这是新增的 R01–R20；没有把 `AUDIT-20-NODES.md` 的第一组检查或同一测试的重复执行计入本组。检查范围为独立节点仓库的 core、nodes、panel、web、测试，以及实际 ComfyUI 0.38.0 官方 API 源码。运行包和更新器由主 Agent 独立检查。

本组修复 **14 类缺陷**，另有 **6 个焦点未发现需要修改的缺陷**。一个焦点中相关的多个反例按一类计数，不把 20 轮说成 20 个 BUG。保持 10 个稳定 nodeID 和 Strata 协议 1；版本、README、CI、提交和发行由主 Agent 处理。

## 验证结果

- 基线：`STRATA_SOURCE_DIR=E:\Strata`，独立仓库完整 **54 tests OK，7.694 秒**。实际基线为节点 52 例、发行物 2 例。
- 新增 `tests/test_audit_round2.py` **29 例**；最终完整 **83 tests OK，无 skip，10.227 秒**：节点 81 例、发行物 2 例。
- 命令：PowerShell 设置 `$env:STRATA_SOURCE_DIR='E:\Strata'` 后，运行 `E:\Strata\runtime\python\python.exe -B -m unittest discover -s tests -q`。
- 不提供 Strata source 的独立运行检查：**83 tests OK，skip=13，3.198 秒**。跳过 12 个原 HTTP Service 集成和本组 1 个结构化 HTTP 集成；其余测试和独立 ZIP 改名导入通过。
- Node.js 实际执行面板 handlers：对象形式的 ComfyUI 队列错误、非 JSON HTTP 503、缺失 prompt receipt、按钮 busy 复位均验证。无 Node.js 的环境明确 skip 这些浏览器测试。
- 额外执行 `tests/probe_comfy_round2.py`，使用实际官方 V3 io 类和实际 CPU PyTorch；10 个 V3 schema 的官方验证、V1 转换、执行输出、UI、列表及缓存契约通过；float16、bfloat16、float32 非连续且 requires_grad 图片张量转换通过。探针输出 `cuda_initialized:false`。
- `git diff --check` 通过。没有下载模型，没有启动、停止或修改用户已有服务；测试 HTTP 服务器只监听本机随机端口并在退出时关闭。

## 20 个检查焦点

### R01 — 标准 JSON 数字在指数溢出时的真实行为

- 复现：`1e999` 不是 NaN/Infinity 常量，但 Python JSON 解析后是 Infinity。原 `parse_constant` 不能拦住，`validated(...,{})`、Extract 和 Schema maximum 可接受并重新输出非标准 JSON。
- 修复：`core.json_loads` 检查浮点转换后的有限性，同时拒绝非标准常量和坏 JSON；节点 Schema、历史、输出提取以及 HTTP 响应共享解析。HTTP 请求序列化也禁止 NaN。
- 验证：`test_exponent_overflow_cannot_escape_structured_validation_or_extract`、`test_schema_exponent_overflow_fails_before_inference`；布尔 Schema true/false 仍可用。

### R02 — 响应完成与总 deadline 的组合边界

- 复现：timeout=.02 秒，body 在 .04 秒后完成。原 `finished.wait(.1)` 返回 true 后不再检查 deadline，误报成功。
- 修复：等待时间受剩余 budget 限制，完成分支也检查总 deadline。
- 验证：`test_completed_response_after_deadline_is_not_success`。仍保留原中断和 detached socket 回归。

### R03 — 成功、坏 JSON 和超大响应的 HTTPResponse 归属

- 复现：HTTP/1.0 或 Connection:close 可把 socket 从 connection 转移给 response。原正常/异常 finally 只关闭 connection，没有显式关闭 response。
- 修复：所有 worker 出口关闭 response，并保证 connection 和 finished event 的 finally 执行。
- 验证：`test_response_is_explicitly_closed_on_size_and_decode_failures` 检查超大 body 和坏 JSON 都调用 response.close。

### R04 — 非 JSON 的 HTTP 限流诊断

- 复现：429 的纯文本或 HTML body 原只返回 JSONDecodeError，丢失限流状态码。
- 修复：HTTP 错误的坏 JSON/非对象 body 保留状态码，用固定诊断说明；不回显 body，不增加自动重试。
- 验证：`test_non_json_rate_limit_preserves_http_status_without_echoing_body`；本组 R18 还使用真实 chunked HTTP 429 验证。

### R05 — 16 MiB 的精确响应上限

- 证据：读取上限为 16 MiB+1，判断严格大于 16 MiB。边界 JSON 在恰好 16 MiB 时完整解析，多 1 字节拒绝。
- 验证：`test_exact_response_limit_is_valid_and_one_extra_byte_is_rejected`。
- 结论：本焦点未发现新缺陷；R18 再检查真实 socket。

### R06 — 协议 bool 与额外进程状态

- 复现：Python 中 True==1；原 protocol_version=True 可通过。只检查 engine/vision 后，额外 process=False 也可通过，后续遍历才抛 TypeError。
- 修复：协议必须为 int 1；processes 必须为对象，必须包含 engine/vision，并验证所有条目的三个 boolean 资源字段。
- 验证：`test_protocol_boolean_and_malformed_additional_processes_are_rejected`。

### R07 — 同卡 Control(load) 的并发配置

- 复现：文本 generate 检查 parallel=1，但 Control(load) 在 serving=2 时仍可 cleanup/offload/POST load。
- 修复：同卡 load 在任何资源修改前拒绝 serving!=1。
- 验证：`test_same_gpu_control_load_rejects_parallel_service_before_mutation`，确认没有 cleanup、handoff 或 POST。

### R08 — 卸载后的最终活动状态

- 复现：初始 status 空闲，unload 后 status 的 in_flight=1，但资源字段均 false。原只检查 loaded/processes 就声称已释放。
- 修复：最终状态还必须 in_flight=0，无法确认则继续等待或阻断。
- 验证：`test_release_requires_final_activity_idle`。

### R09 — 短清理 budget 与逐请求 timeout

- 复现：cleanup_timeout_s=.05 时，原请求仍使用固定 3/5/3 秒 timeout，固定 .2 秒轮询 sleep 也超出短 budget。
- 修复：每个请求和 sleep 使用剩余清理时间。自有进程 stop 和后续 GPU 内存确认仍是各自的安全收尾步骤，不据此声称整个 stop 有同一时长上限。
- 验证：`test_cleanup_http_timeouts_use_remaining_budget` 使用确定时钟验证递减 budget；`test_cleanup_poll_sleep_does_not_add_fixed_two_hundred_milliseconds` 验证真实等待。
- 原测试对拒绝错误状态的断言改为“所有请求都是 status，没有 POST unload”，避免把固定 .2 秒 sleep 的偶然一次轮询当成契约。

### R10 — 准备正常退出后的取消窗口

- 复现：准备进程 poll() 已正常退出，原立即启动 HTTP server，取消回调要到 server readiness 才执行。
- 修复：准备 log 关闭后、server Popen 前再次 check。
- 验证：`test_cancel_after_preparation_exit_never_spawns_server`，Popen 仅执行准备进程一次，不生成 owner。

### R11 — 官方 V3 schema 与 V1 转换的真实契约

- 证据：读取官方 `_io.py` 的 Input/Output/Schema/NodeOutput、GET_SCHEMA/GET_NODE_INFO_V1 和 execution.py 的 list/fingerprint 逻辑。实际导入官方 API，验证全部 10 个节点，而非只使用接口替身。
- 验证：`probe_comfy_round2.py` 调用官方 schema 验证/转换，执行 Extract 与 Control，检查 UI、列表、返回类型以及 Control NaN fingerprint。
- 结论：未发现需要改动注册适配的缺陷。正式三类绘图由主 Agent 最后统一实机检查。

### R12 — Schema 引用的作用域、字面数据和间接目标

- 复现：原递归遍历所有 dict，误拒绝 const/enum 数据中的 `$ref` 字段；缺失本地 anchor/$defs 引用又可先执行模型才失败。
- 修复：使用 referencing 的 schema 子资源遍历；实际 schema 的引用必须是存在的本地 schema，提前 lookup。合法 anchor 和有数据下降的递归 Schema 保留。
- 关联反例：指向 description 文本不构成 schema；指向 const 对象后，其中 `$ref` 会变成实际 schema 关键字。修复还检查被引用目标，不能借字面数据隐藏远程引用；visited 集合防止引用检查本身重复循环。
- 验证：`test_schema_literal_ref_fields_are_data_not_reference_keywords`、`test_missing_local_schema_reference_is_rejected_before_model_work`、`test_local_anchors_and_recursive_schema_continue_to_validate`、`test_reference_targets_are_checked_even_when_reached_through_literal_keywords`。

### R13 — Windows 保留设备名档案

- 复现：CON/NUL/AUX/PRN、COM1/LPT9 和 Windows 识别的上标设备号可通过名称 regex；实际文件操作无法作为普通档案可靠处理。
- 修复：以明确节点错误拒绝保留名；档案列表忽略不能作为档案名的文件。CON_work 等普通名称仍可用。
- 验证：`test_windows_device_profile_names_fail_as_node_errors`。规则跨平台一致，便于档案迁移。

### R14 — 实际 CPU tensor 的 dtype、layout 和 autograd

- 证据：上一组 NumPy 驱动替身不能覆盖真正 torch.detach/float/numpy 行为。
- 验证：额外探针使用 float16/bfloat16/float32、非连续 transpose、requires_grad、2 张 RGBA tensor；实际 PNG 解码检查 RGB、尺寸和透明白底，CUDA 未初始化。
- 结论：现有 CPU 转换通过，本焦点无新修复。没有把这个 CPU 结果当成 CUDA 图片张量或显存实测。

### R15 — 面板队列错误、receipt 和 busy 状态

- 复现：ComfyUI 400 的 error 是对象时显示 `[object Object]`；HTML 503 只显示 JSON 语法错误；HTTP 200 没有 prompt_id 时仍声称操作入队。
- 修复：显示 message/details 和 node_errors；非 JSON 响应保留 HTTP 状态；生命周期操作必须拿到非空 prompt_id 才显示已入队。
- 验证：两个新 Node.js VM/DOM 测试真实执行 handlers。成功入队仍走 10 个稳定 nodeID；错误后 finally 恢复按钮。额外用修改前 HEAD 的 JavaScript 在临时目录复现缺失 receipt 的误报。

### R16 — 损坏本机档案的可恢复性

- 复现：一个坏 JSON 档案使整个 profiles endpoint 失败；保存显式替换 key 也因无条件读取损坏旧文件而失败。
- 修复：每个档案独立读取，返回不含内容的 profile_errors；显式替换无需读旧文件。只有 __KEEP__ 才读取旧值，损坏时要求替换 key，保留原文件。面板显示受损名称和恢复说明。
- 验证：`test_explicit_replacement_can_repair_a_corrupt_profile`、`test_corrupt_profile_cannot_silently_keep_an_unknown_key`、`test_one_corrupt_profile_does_not_hide_all_valid_profiles`；全部使用临时假 key，没有读取用户凭据。

### R17 — 托管路径与子进程工作目录

- 复现：relative data_dir 原样存储，managed_config 在 runtime cwd 启动后会指向不同目录；NUL 路径也可保存，运行时才抛底层异常。
- 修复：runtime/data_dir 在配置规范化时 expanduser/resolve，拒绝控制字符和无法规范化的路径。
- 验证：`test_managed_paths_are_fixed_before_subprocess_cwd_changes`；只解析临时/不存在测试路径，不运行原生准备程序。

### R18 — 真实 HTTP framing 与响应边界

- 验证：本机 ThreadingHTTPServer 的实际 chunked JSON、chunked 非 JSON 429、Connection:close 超大 body、非有限 usage JSON。客户端使用真实 http.client socket。
- 结果：3 个 `HttpWireBoundaries` 测试通过，和替身验证一致；本焦点未发现额外缺陷。

### R19 — 服务端 Schema 失败→修复→提取的整条 HTTP 组合

- 验证：实际 Strata Python Service + ResidentEngine/MockEngine。第一次输出非法 JSON，经服务端 structured_output_failed 返回；第二次符合两镜头 schema。检查 prompts 顺序、Extract、Number，以及两次 starts/closes、最终 in_flight=0。
- 结果：`test_server_schema_repair_then_extract_preserves_order_and_releases_each_attempt` 通过；没有放行部分结果，无额外修复。

### R20 — 当前完整发行文件的独立加载

- 验证：使用当前 tracked public shipping 列表和当前工作树源码，在临时目录调用现有 builder；ZIP 解压后改名，再以独立 Python 子进程导入。清空 STRATA_SOURCE_DIR/PYTHONPATH，使用新的空 HOME。
- 结果：10 个 legacy 节点、实际 Extract 执行通过；没有 import serve，导入没有创建 HOME。该测试使用完整当前 ZIP 的 web/examples，补充原发行测试的假 shipping 文件检查。
- 结论：独立包加载无额外修复。测试 ZIP 留在 TemporaryDirectory；没有覆盖正式 dist 或执行提交/上传/发行。

## 测量边界

主 Agent 最终追加实机验收：ComfyUI 0.38.0 / Torch 2.7.0+cu128 / RTX 4060 Ti 16GB / 128GB RAM，三类实际绘图通过。文本绘图 27.27s（1 张）、图片反推绘图 27.16s（1 张）、两镜头分镜批量绘图 51.34s（2 张）；每次返回前语言/视觉进程确认停止，Control(load) 队列与缓存重复请求通过。计时包括本次小型工作流的推理和采样，不能当作模型吞吐。验收档案使用 8GiB 空闲显存门槛，产品默认仍为 12GiB。

发行前另补一项权重边界回归：GitHub 构建器拒绝 `.bin/.onnx`，Registry 和 Git 忽略列表覆盖全部常见权重扩展名。最终本机 **84 tests OK，无 skip，10.303s**；包括本组新增 29 例和额外 1 例发行检查。官方 `comfy node validate` 与 `comfy node pack` 均成功。

- 主测试 Python 为 `E:\Strata\runtime\python\python.exe`。实际 ComfyUI API/CPU tensor 探针额外使用主 Agent 提供的 ComfyUI Python `G:\comfyUI(1)\comfyUI\.ext\python.exe`，源码 `E:\Strata\.portable-build\comfyui-validation-source\ComfyUI-master`，依赖目录 `E:\Strata\.portable-build\comfy-deps`。
- 探针设置 CUDA_VISIBLE_DEVICES=-1，并禁用可选 Triton GPU kernel 发现；官方 io 类、torch tensor 和 PIL 都是真实实现。未启动 ComfyUI server，也未调用 GPU handoff。探针中的 server 注册入口和 interruption hook 使用替身，避免导入实际服务器管理模块。
- 准备取消、status 资源反例、所有权等仍使用替身；HTTP framing、PNG、文件和 ZIP 是实际执行。没有下载权重、没有原生 GPU 性能或显存测量。
- 14 类修复位于 R01/R02/R03/R04/R06/R07/R08/R09/R10/R12/R13/R15/R16/R17。R05/R11/R14/R18/R19/R20 检查通过且无额外修复。
