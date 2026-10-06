# ComfyUI Strata-T8 第四组 20 轮检查

日期：2026-10-06。基线为节点 1.0.2，提交 `f89abb1f4c66fdaf8d729d94965e2e29ee7773a9`；配套运行包 0.1.39-t8.10。首先读取项目上下文和前三组报告。本组为 20 个新增检查焦点；旧回归重跑不计轮数。

本组修复 **9 类缺陷**，保留 10 个节点 ID、V3/V1 适配和 Strata 协议 1。不同草案或同一清理事务的多个反例归并为同一类缺陷，不能把 20 轮说成 20 个 BUG。

## 实际验证

- 基线完整回归：**105 tests OK，13.666s，无 skip**。
- 新增 `tests/test_audit_round4.py` **26 例**：最终模块 **26 tests OK，1.821s**。包含 2 例真实 Strata Python Service HTTP 集成；模型引擎为 ResidentEngine/MockEngine 替身。
- 完整最终回归：**131 tests OK，15.669s，无 skip**。命令：设置 `$env:STRATA_SOURCE_DIR='E:\Strata'` 后，执行 `E:\Strata\runtime\python\python.exe -B -X utf8 -m unittest discover -s tests -q`。
- 隔离复现：把不可变基线提交的节点源码、面板和旧测试复制到临时目录，执行新增的 13 个缺陷测试方法。**13 个方法均失败**，共 16 个失败/5 个错误（包括 subtests），证明反例在修复前存在。复现脚本与日志位于主仓忽略目录 `.portable-build/audit4_node_baseline.py` / `audit4-node-baseline.log`；没有覆盖用户配置。
- Node.js 实际执行浏览器 JavaScript handlers；DOM/API 为替身。JSON、PNG/PIL、临时文件和随机端口 HTTP 实际执行。没有下载模型、启动 CUDA、改变用户服务或终止用户进程。
- 原 repair 和循环 Schema 两个测试 fixture 加上 `type:object`，符合现有服务协议。原失败类型及调用次数断言保留，没有删测试或跳过故障。

## 20 个新焦点

| 轮次 | 检查目标 | 复现、处理及证据 | 边界 |
|---|---|---|---|
| R01 | Draft 4 的 exclusiveMinimum 和 tuple items | 合法 boolean exclusiveMinimum/tuple items 被固定 2020-12 预检拒绝。改为按声明草案选择 validator/Resource。`test_draft4_exclusive_minimum_and_tuple_items_use_the_declared_draft`；另有真实 HTTP invalid→repair→Extract/Number 两次 starts/closes。 | 对象根为服务约束；本机 helper 仍保留 boolean schema。 |
| R02 | Draft 7 的未知 dynamicRef 注解 | Draft 7 中未知 `$dynamicRef` 原被当作有效引用并误拒绝 HTTP 字符串。仅检查当前草案实际执行的引用关键字。`test_draft7_unknown_dynamicref_is_an_annotation_not_an_http_reference`。 | 真正 `$ref` 仍限定本地；没有获取注解 URL。 |
| R03 | Draft 2019-09 的 recursiveRef | 合法 recursiveAnchor 被 2020-12 预检拒绝；同时原引用扫描遗漏 recursiveRef。按草案保留本地递归且提前拒绝远程递归引用。`test_draft2019_recursive_reference_keeps_local_recursive_validation`，另有实际 HTTP + 本机验证嵌套对象。 | 有数据下降的递归通过；零下降循环仍是有界结构化错误。 |
| R04 | 工作流连接传入非 object 根 Schema | Structured 原把 true/false、array 或无显式 object 根发送到服务，服务随后拒绝。加入 `check_service_schema`，在推理前明确诊断根需 type object。`test_non_object_service_schemas_fail_before_model_work`。 | 配合既有 Strata 协议 1，不改变 Extract 对数组/标量的支持。 |
| R05 | 图片 Schema 的显式 JSON null | 非空 `null` 文本原被误当作未填 Schema；boolean/array 也先拷贝图片再失败。非空文本必须通过服务 Schema 预检。`test_explicit_null_image_schema_is_not_silently_ignored`，encode/generate 均不调用。 | 真正空白 Schema 仍允许普通图片分析。 |
| R06 | 重复 JSON 成员的含义歧义 | structured/history/profile 的重复成员原保留最后值。统一严格 JSON parser 拒绝重复成员；panel JSON body 使用同一 loads。`test_duplicate_json_members_cannot_change_structured_output_or_history`、`test_profile_duplicate_credentials_never_replace_an_existing_profile`、`test_profile_http_body_uses_the_same_strict_duplicate_member_parser`。 | 字段名大小写不同仍合法；错误不包含凭据。 |
| R07 | 同卡预清理 stop 后的托管模型身份 | cleanup fallback stop 原让 generate 继续请求已停止 HTTP，并沿用旧 model 名。重新 ensure 托管实例、验证新状态并使用新 model 身份。`test_preclear_fallback_stop_restarts_and_uses_the_new_model_identity`。 | 进程及 cleanup 为替身；不据此声称真实 VRAM 已释放。 |
| R08 | Control(load) 预清理 stop 后继续 load | 同一卡 load 的前置 cleanup fallback stop 后原未重启服务。重新 ensure，检查 parallel=1 与 idle 后再 load。`test_control_load_restarts_after_preclear_stopped_its_http_server`。 | 重启失败仍阻断；不复用旧 readiness。 |
| R09 | Control(load) 最终释放通过 stop 的结果 | load 成功后，finally 已停止自有服务却继续查询死亡端口，误报连接失败。确认 stop 后返回 after_release stopped/released。`test_control_load_final_fallback_reports_release_without_querying_dead_server`，没有第 4 次 HTTP 请求。 | 清理失败仍错误；结果保留 load 前后快照语义。 |
| R10 | owner 创建时间和 PID 的超大整数 | creation 10**1000 原裸抛 OverflowError；PID 溢出可到 psutil。先验证现实范围，并保持无法确认归属时阻断。`test_damaged_owner_creation_numbers_and_encoding_are_controlled_ownership_errors`、`test_false_positive_owner_pid_overflow_does_not_reach_process_termination`。 | 损坏 UTF-8 原已有受控错误，本次也验证；不杀真实进程。 |
| R11 | profiles 目录中同名 JSON 子目录 | `directory.json/` 原进入 Connection 下拉却不能作为档案读取。列表只接受文件。`test_profile_dropdown_ignores_directories_named_like_profiles`。 | 没有档案仍给 default；普通合法档案正常出现。 |
| R12 | 嵌套扩展配置的 bool/null/Unicode | 新组合实际保存并读取，字段值保持且 Connection 不带假 API key。`test_profile_extension_booleans_and_nested_unicode_round_trip_without_keys_in_connection`。无需新修复。 | 仅临时 HOME，无远程访问。 |
| R13 | Origin 带 path/query/fragment 的序列化边界 | 原仅比 scheme/netloc，非序列化 Origin 仍通过。明确拒绝三个非 origin 组件。`test_origin_must_be_a_serialized_origin_without_path_query_or_fragment`。 | 保留 loopback 限定；浏览器通常不会发送这种格式。 |
| R14 | IPv6 loopback 的面板只读状态 | `[::1]:8188` 同源 Origin/remote 通过，status 使用独立的非中断回调。`test_ipv6_loopback_origin_and_read_only_status_are_accepted`。无需新修复。 | aiohttp 路由替身；没有访问用户 ComfyUI 服务。 |
| R15 | 保存配置与已发出的队列动作重叠 | 保存时生命周期按钮原仍可操作；先完成的动作 finally 可再次解冻按钮。使用统一 saving/activeActions 状态，保存期间冻结所有动作，结束后按各自进行状态恢复。Node.js `test_save_freezes_actions_and_action_completion_cannot_unfreeze_a_running_save`。 | 实际 JS handler，API 为可控 promise 替身；队列 API 不携带 key。 |
| R16 | 重启后出现第三方活动的竞争 | 新实例 status 若 in_flight=1，应拒绝推理和 handoff，且不卸载他人活动。`test_restarted_preclear_still_rejects_foreign_activity_before_inference`。为 R07 重启补充验证，没有新增独立缺陷类。 | 不把节点锁声称为对外部 API 的隔离。 |
| R17 | 实际 HTTP 状态 body 的重复 loaded 标志 | 随机端口真实 HTTP 发送 loaded=true/loaded=false，旧客户端误保留最后值；严格 JSON 后拒绝整个响应。`test_duplicate_http_status_flags_cannot_authorize_a_last_member_override`。 | 实际 http.client socket，新增网络授权边界证据；属于 R06 的同一 parser 修复类。 |
| R18 | 空 Extract 列表和非字符串 item_field 值 | 空数组给空列表；嵌套 object 与 false 变为正确 JSON 文本，两个列表输出一致。`test_empty_extract_lists_and_nested_non_string_fields_keep_typed_outputs`。无需新修复。 | 不强迫空列表伪造一个空条目。 |
| R19 | 重复批量输入的配对身份 | 同样两条输入分别保留 index、不同 output/reasoning/usage，并只调用一个 generate 事务。`test_batch_duplicate_inputs_preserve_index_identity_and_one_transaction`。无需新修复。 | generate 替身，不代表真实模型确定性。 |
| R20 | 透明与不透明图片同时处于一个批次 | 实际 PIL 解码两个 PNG，透明黑底为白色，不透明红色为红色，张数与顺序保持。`test_transparent_images_keep_one_encoded_image_per_batch_entry`。无需新修复。 | NumPy 驱动 CPU tensor 替身；没有初始化 CUDA。 |

补充 `test_literal_numeric_object_keys_and_long_array_indices_are_not_confused` 验证对象数字键与超长数组索引，不额外计为第 21 轮。

9 类修复为：草案选择及引用关键字、服务 Schema 根预检、重复 JSON 成员、预清理后托管重启、最终 Control stop 结果、owner 巨整数、假档案目录、Origin 格式、面板保存与动作状态。

## 适用范围

完整回归实际读取主仓 Python Service；其推理和资源行为仍由 ResidentEngine/MockEngine 替身提供。托管 PID、ensure/stop、同卡 handoff 是替身；没有 Linux 托管进程、AMD、多 GPU 或显存测量。子 Agent 审计阶段没有下载模型、改版本/README/CI 或执行提交/发布。主 Agent 的后续 GPU 工作流和发行证据见下文。20 轮检查不证明项目不存在其它缺陷。


## 主 Agent 后续验收与发布

本机完整 131 例通过，15.484s；[Windows/Linux CI](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37452842843) 通过，Windows 131 例全部执行，Linux 明确跳过 1 个 Windows 专属用例。节点 [1.0.3](https://github.com/T8mars/Comfyui-Strata-T8/releases/tag/v1.0.3) 源码提交为 `4203df3caef5495d998a8a9d6f9721c12a885cdc`，配套运行包 [0.1.39-t8.11](https://github.com/T8mars/Strata-T8/releases/tag/v0.1.39-t8.11)。

在实际 ComfyUI 0.38.0、Torch 2.7.0+cu128、RTX 4060 Ti 16GB/128GB RAM 和 DreamShaper 8 下执行文本→绘图、图片反推→绘图、两镜头分镜→批量绘图，分别 28.28s/28.45s/54.38s，共 4 张图片。排队 load、缓存重复和每次文字/视觉进程释放检查通过。验证档案空闲显存门槛 8GiB，产品默认 12GiB；功能源码 SHA256 与正式版本匹配。此为主 Agent 后续 GPU 验收，与上文子 Agent 的替身回归区分。

GitHub ZIP SHA256 `ea7a8b4b7550bc70f104c8d54f00fa0088963cc36211eb52c327616bf0e1a155`；Registry ZIP SHA256 `64703daad0c015232710bb96c23a692cf0032f066e015145fed8827f3d296f93`。两份包内源码逐字节匹配发布提交，10 个节点可导入。Publisher `t8star` 的[官方发布工作流](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37452843332) 成功，CDN 包可取得；[Registry 版本接口](https://api.comfy.org/nodes/strata-t8/versions/1.0.3) 核查时仍为 `NodeVersionStatusPending`，未确认 Manager 可检索。等待审核期间可使用 GitHub Release 安装。

完整证据和运行包核查见[主仓第四组报告](https://github.com/T8mars/Strata-T8/blob/main/docs/AUDIT-20-ROUND4-T8.md)。本段为发布后文档补充，没有覆盖不可变版本或发布资产。
