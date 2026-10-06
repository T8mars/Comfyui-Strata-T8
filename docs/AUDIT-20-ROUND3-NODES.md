# ComfyUI Strata-T8 第三组 20 轮检查

日期：2026-10-06。基线为节点 1.0.1，工作树 HEAD `6d2deb6860c5cdebb7715994beb15025b3f655c2`；运行包 0.1.39-t8.8。

这是新增的 R01–R20。前两份报告的焦点及重复回归不计入本组。检查了新的 Schema 组合、配置事务、进程归属反例、卸载兜底、实际 HTTP framing、极端图片几何、面板异步编辑，以及官方 V3 动态档案 schema。本组修复 **13 类缺陷**，另外 **7 个焦点未发现新缺陷**。保留全部 10 个 node ID、V3/V1 适配及 Strata 协议 1；版本、README、工作流、提交与发行由主 Agent 处理。

## 验证证据

- 基线完整回归：**84 tests OK，11.107s，无 skip**。
- 新增 `tests/test_audit_round3.py` **21 例**。新模块最终 **21 tests OK，2.457s**。
- 带实际 Strata source 的最终完整回归：**105 tests OK，13.289s，无 skip**。命令：先设置 `$env:STRATA_SOURCE_DIR='E:\Strata'`，运行 `E:\Strata\runtime\python\python.exe -B -X utf8 -m unittest discover -s tests -q`。包含保存期间的前端编辑保护补强，以及主 Agent 本组新的服务端 Schema 行为。
- 不提供 Strata source：**105 tests OK，5.835s，skip=13**；跳过 12 个原 HTTP Service 集成及第二组 1 个结构化 HTTP 集成。
- 额外执行 `tests/probe_comfy_round3.py`：实际官方 V3 `GET_NODE_INFO_V1` 的配置下拉选项依次为 `default → alpha → alpha,beta → beta`，无需重启 Python；Control 原样传递实际 torch tensor；实际 RGB 截断像素为 `[0,255,128]`；极窄 PNG 分别为 `65536×1` 与 `1×65536`。探针输出 `cuda_initialized:false`。
- 官方探针使用 Python 3.11.6 / Torch 2.7.0+cu128，源码 `E:\Strata\.portable-build\comfyui-validation-source\ComfyUI-master`，辅助依赖 `E:\Strata\.portable-build\comfy-deps`。命令：`G:\comfyUI(1)\comfyUI\.ext\python.exe -B -X utf8 tests\probe_comfy_round3.py <ComfyUI source> <dependencies>`。
- 浏览器测试使用实际 Node.js 执行 JavaScript handlers；DOM/API 是替身。实际 HTTP 测试仅监听本机随机端口，退出时关闭。

## 20 个新焦点

### R01 — 零数据下降的本地 Schema 递归

- 复现：`{"$ref":"#"}`、allOf 的自引用、两个 `$defs` 相互引用均通过 Schema 语法检查，但验证对象时裸抛 `RecursionError`。
- 修复：验证阶段的递归异常转为 `StructuredOutputError`，遵守已有 0–2 次修复限制。
- 验证：`test_unproductive_local_cycles_fail_as_bounded_structured_errors`；repair_attempts=1 时恰好调用两次 generate。
- 边界：这些 Schema 语法合法，本组没有声称在模型加载前完整判定所有零下降递归。合法有数据下降的递归另在 R17 验证。

### R02 — 连接输入绕过 widget 后的 Schema/提取类型

- 复现：Structured 的 None/False/空对象 Schema 被误当作“默认 Schema”；Extract 的列表 expected_type 裸抛 unhashable TypeError。
- 修复：Structured 明确要求 Schema 文本，空白文本采用默认 Schema；Extract 先检查 expected_type 为合法字符串。
- 验证：`test_structured_schema_requires_text_before_any_model_work`、`test_extract_invalid_expected_type_is_a_node_error`。非法类型不调用模型。

### R03 — 扩展配置字段的自损保存

- 复现：已知字段合法，未知扩展字段中带 NaN 时保存成功；下一次严格 JSON 读取立即失败。
- 修复：保留扩展字段，但整个档案必须能序列化为有限数字的 JSON；实际写入同时禁止 NaN。
- 验证：`test_extension_values_cannot_save_a_self_corrupting_profile` 覆盖嵌套 NaN、Infinity、非 JSON 对象，旧文件及扩展字段不变，没有私密临时文件。

### R04 — JSON 合法但不能编码为 UTF-8 的档案字符串

- 证据：未配对 surrogate 字符在实际文本写入时失败。
- 验证：`test_unicode_encoding_failure_preserves_old_profile_without_private_temp`；旧配置字节完全保留，临时文件删除。
- 结论：现有事务清理通过，本焦点无新修复。API 已用固定异常类型诊断，不回显配置内容。

### R05 — 重复 --config 的实际 argv 含义

- 复现：cmdline 同时带 `--config <owned>` 和后面的 `--config <foreign>` 时，Python argparse 采用后者；原“任意匹配一个 --config”仍认定为自有。
- 修复：仅接受一个 config 选项，且必须严格配对已记录配置；重复普通/等号形式均拒绝。
- 验证：`test_duplicate_config_options_cannot_identify_a_foreign_effective_config`；正常单一配对仍识别。
- 边界：PID、argv 和进程对象为替身，未终止真实用户进程。

### R06 — owner 注册后损坏 instance 的回滚

- 复现：创建 owner 后 instance 字段不是有效字符串，readiness 无法安全确认实例；原读取/字段使用可能处于回滚 try 外。
- 修复：状态读取及 instance 校验放在 readiness 回滚保护内，失败尝试停止已验证自有进程。
- 验证：`test_damaged_instance_record_rolls_back_the_owned_process`，损坏 instance 为 False 后调用 stop 一次，不声称 ready。

### R07 — 相同 instance_id 但不兼容协议的 ready 响应

- 复现：协议 2 响应带正确 instance_id，Managed.ensure 原直接返回；后续 generate 才检查兼容性，新自有服务没有走启动失败回滚。
- 修复：Managed readiness 在返回前验证完整协议 1 状态，失败走已有自有停止逻辑。
- 验证：`test_incompatible_ready_response_stops_new_owned_server`；一次 status 请求后立即拒绝及回滚，不轮询 90 秒。

### R08 — stop 无法枚举子进程时的释放承诺

- 验证：`test_stop_access_denied_during_enumeration_never_claims_release`，children(recursive=True) 拒绝访问时明确失败，没有 terminate 并误报释放。
- 结论：现有安全阻断通过，无新修复。不能确定整个资源树退出时，继续阻断下游；本项使用 psutil 替身。

### R09 — unload 兜底停止自有 HTTP 服务后的成功结果

- 复现：HTTP 无法确认释放，cleanup 已成功 stop 自有服务；Control(unload) 原继续查询已停止端口，反而报告连接错误。
- 修复：cleanup 返回是否实际停止自有服务；Control 对已确认的兜底 stop 返回 `{stopped:true,released:true}`。
- 验证：`test_unload_fallback_can_report_success_when_own_server_had_to_stop`，仅尝试必要的释放 status，stop 后不再查不存在的 HTTP 服务。

### R10 — process.loaded 单独为真的预先释放

- 复现：聚合 loaded=false、process.running/starting=false，但 vision.loaded=true。原预清理判断遗漏此资源标志，先做同卡 baseline。
- 修复：同卡预清理同时检查 running、loaded、starting。
- 验证：`test_process_loaded_flag_is_released_before_same_gpu_baseline`；顺序为 cleanup → handoff → 请求 → cleanup。
- 边界：反例状态与 handoff 是替身；没有把布尔标志当作实际 VRAM 测量。

### R11 — 取消回调与安全清理相互独立

- 验证：`test_cancelled_request_keeps_cleanup_independent_of_cancel_hook`，请求抛 InterruptedError 后仍调用 cleanup，保留取消异常；清理不依赖已中断的工作流回调。
- 结论：组合行为通过，无新修复。若真正清理失败，仍以阻断资源交接为先。

### R12 — Content-Length 截断但恰好构成 JSON 的响应

- 复现：真实 HTTP 声明 1000 字节，只发送 `{"ok":true}` 后关闭。http.client 的有上限 read 不自动拒绝这种截断；原客户端误报成功。
- 修复：保存解析后的 Content-Length，读取后按实际字节长度检查完整性。
- 验证：`test_valid_json_in_a_truncated_content_length_is_not_a_complete_response`；截断明确失败，正确长度成功。没有把已声明长度当作可信分配大小。

### R13 — 无 Content-Length 的 close-delimited 响应

- 验证：`test_close_delimited_json_remains_supported`，真实 HTTP/1.0 以关闭连接划定 JSON body，客户端仍完整解析。
- 结论：R12 的修复保留合法 framing；本焦点无额外修复。chunked 已由前组回归覆盖，不重复计本组轮次。

### R14 — 显式请求 timeout 的巨整数

- 复现：Client.request(timeout=10**1000) 在 math.isfinite 前没有边界，裸抛 OverflowError。
- 修复：先检查 `0 < timeout <= 86400`，与档案允许的范围一致，然后验证有限性。
- 验证：`test_explicit_huge_timeouts_fail_before_socket_or_overflow`；巨整数、86401、Infinity、bool 均为明确节点错误，不创建 HTTP connection。

### R15 — 请求 JSON 序列化失败不能发送晚请求

- 验证：`test_invalid_request_json_is_never_sent_after_connect`，NaN body 在已有 connect 后序列化失败；conn.request 从未执行，connection 确认关闭。
- 结论：原 allow_nan=False 与 worker finally 组合通过，无新修复。不把已建立 TCP 连接说成未联网。

### R16 — 极窄图片的实际编码像素上限

- 复现：1×1,048,576 或反向图片在 max_pixels=65536 下，比例缩放短边后 clamp 为 1，导致最终 width×height 仍超限。
- 修复：在整数尺寸及最短边取 1 后再检查像素积，必要时缩短长边；保持两维至少为 1。
- 验证：`test_extreme_aspect_ratio_obeys_the_actual_encoded_pixel_limit` 使用实际 PIL PNG；额外真实 CPU torch 探针结果均为 65536 像素，未初始化 CUDA。

### R17 — dynamicRef 的作用域和合法字面数据

- 验证：`test_dynamic_ref_scopes_and_ref_like_data_keep_their_meaning`；本地 dynamicAnchor/dynamicRef 在 next 子对象中下降并成功验证。enum 内 `$ref` 字符串及 HTTPS 字符串仍是普通数据。
- 结论：合法递归及字面数据通过，无新修复。与 R01 的零下降引用区分，不把重新执行前组普通 $ref 用例计作新焦点。

### R18 — 面板异步刷新、过期返回与保存期间编辑

- 复现：初次 profiles 请求未返回时，用户开始编辑新名字/配置/key；响应到达调用 onchange，会丢草稿。两个 refresh 返回顺序颠倒也可将旧列表盖在新列表上。
- 修复：保存草稿快照，仅在未编辑时刷新已有配置；reload generation 忽略过期响应。保存期间冻结表单和刷新，结束或失败后恢复，开始保存同时作废旧 refresh。
- 验证：Node.js `test_refresh_preserves_drafts_and_late_responses_and_blank_key_can_be_cleared` 实际触发 initial reload、反序返回、成功保存和 400 失败恢复；草稿、最新列表与可编辑状态符合预期。

### R19 — 清除旧 API key 及无认证损坏档案恢复

- 复现：面板空 key 一律转换为 __KEEP__，无法清除已有 external key；无认证服务的损坏档案也无法通过空替换 key 恢复。
- 修复：增加明确的“清除已保存 API key”选项。external 保存空 key，managed 按已有规则重新生成；每次切换/成功保存重置选项。
- 验证：同一 Node.js 测试检查实际 POST 是空字符串；`test_blank_external_key_repairs_a_corrupt_unauthenticated_profile`、`test_managed_blank_key_rotates_identity_without_leaking_into_connection` 验证两种语义。所有 key 都是临时假 key。

### R20 — 官方 V3 动态档案 schema 与可选图片透传

- 证据：直接读取并调用实际官方 `GET_SCHEMA`/`GET_NODE_INFO_V1`。官方 Combo 转换可为 `('COMBO', {options:[...]})`，探针按实际格式检查。
- 验证：`probe_comfy_round3.py` 在同一个 Python 进程中保存/移除临时档案，下拉 options 每次正确变化；实际 NodeOutput、Control 的可选 text 和 requires_grad CPU tensor 身份透传均通过。浮点值转 PNG 时负值/超 1 值饱和为 `[0,255,128]`。
- 结论：此新组合契约未发现缺陷。没有启动 ComfyUI HTTP server、native 引擎或 CUDA context。

## 适用边界

- 修复类别位于 R01/R02/R03/R05/R06/R07/R09/R10/R12/R14/R16/R18/R19；R04/R08/R11/R13/R15/R17/R20 检查通过且无新修复。
- 文件、JSON、PNG、真实 HTTP framing 和 Node.js handlers 实际执行；进程 PID/argv/children、生命周期状态及 handoff 是替身。官方 io 类、torch tensor、PIL 为真实实现，探针 server 注册及 interruption hook 为替身以保持 CPU-only。
- 没有下载模型，没有停止或修改用户 8082、8080、8188、8189 服务，没有改版本、README、发布工作流或执行提交/发布。
- 本组没有 Linux 托管原生进程、AMD 同卡交接、多 GPU 或 GPU 显存测量。Windows/Linux 单元 CI 及正式绘图由主 Agent 最后统一验收。
- 最后集成复跑曾命中主仓正在修改的 referencing 参数名拼写错误（default_spec）；即时反馈后主 Agent 修为实际签名 default_specification，随后完整 105 例通过。没有以替身或跳过掩盖该集成问题。
- 20 轮是 20 个不同检查焦点，不等于 20 个 BUG，也不证明项目不存在其他缺陷。
