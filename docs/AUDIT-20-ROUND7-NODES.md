# ComfyUI Strata-T8 第七组 20 轮检查

日期：2026-10-06。不可变基线为节点 1.0.5，提交 8c69d8959ed79f1d8e85f05211d485ed65988697；配套运行包基线 0.1.39-t8.13。先读取 AGENTS、meta、features、pyproject、相关源码、测试及前六组报告。本组记录 20 个新焦点，既有回归重跑不计轮数。

修复 **5 类根因**：错误文本冒充结构化修复授权、保留选项覆盖已校验请求、JSON 请求在资源修改后才检查、无效批次仍进入资源事务、显存小数阈值被截断。多个反例归并计数，20 轮不等于 20 个 BUG。10 个 node ID、V3/V1 适配、Strata 协议 1 和额外 API 采样选项保持。

## 实际证据

- 新增 tests/test_audit_round7.py **23 个方法**。首次完整 **211 tests OK，27.628s**；补充非空消息数组及对象成员预检后，最终 **211 tests OK，27.857s，无 skip**，日志 E:\Strata\.portable-build\audit7-node-final.log。设置 STRATA_SOURCE_DIR=E:\Strata，用 E:\Strata\runtime\python\python.exe -B -X utf8 -m unittest discover -s tests -q 执行。
- 将上述 Git 提交归档至独立 TemporaryDirectory，显式把其 tests 目录置于 sys.path 首位，断言 core/nodes 实际源文件也位于该目录。选取 **13 个缺陷方法全部失败**；含 subtests 为 **27 failures、0 errors，1.153s**，脚本核对 every_selected_method_failed=true。证据 .portable-build/audit7_node_baseline.py、audit7-node-baseline.log 位于主仓忽略目录，没有把其它正向方法称作基线失败。
- 真实随机 loopback socket 发送 HTTP 502、legacy 422、400、429、503 及正常 chunked 扩展/trailer 响应。实际 HTTP client、JSON parser 和线程执行；模型或 GPU 信息替身分别在表中说明。
- 实际 Node.js 执行面板 handlers，DOM/API 为受控替身；继承第六组 harness 的完成标记及 watchdog，等待所有异步 promise 后才算通过。
- 实际官方 ComfyUI 0.38.0 V3/CPU 探针通过：10 IDs、保留选项在 generate 前拒绝、真实 HTTP 502 的 typed bounded repair、成功重试的 usage、Structured 原生 STRING 列表及 Batch 自定义整批列表、requires_grad RGBA tensor 编码与 Control 身份透传。Python 3.11.6 / Torch 2.7.0+cu128，cuda_initialized:false。日志 audit7-node-official-probe.log；G:\comfyUI(1)\comfyUI\.ext\python.exe -B -X utf8 E:\Strata\.portable-build\audit7-node-official-probe.py。Comfy server/mm 挂载入口与生成器为替身，官方 io、torch CPU、http.client/socket 实际执行。
- 唯一旧 fixture 调整：test_structured_repairs_local_json_schema_failures_and_server_rejections 的服务端错误替身改为 StructuredServiceError；原错误文案、重试次数与成功结果断言保留。新增真实 wire 用例验证分类，未删测试或跳过故障。
- git diff --check 通过。子 Agent 未改版本、README、CI、发布流程或提交。

## 20 个新焦点

| 轮次 | 检查目标 | 反例、处理和实际验证 | 边界 |
|---|---|---|---|
| R01 | 已允许的小数 MiB 显存阈值 | .75 原被 int 截断为 0MiB，512KiB 空闲也放行。保持 float 比较，512KiB 拒绝，恰好 768KiB 通过。test_fractional_vram_threshold_cannot_be_rounded_down。 | GPU/RAM 信息为替身；不是显存测量。与第一组负小数输入不同。 |
| R02 | 小数 GiB RAM 门槛的单位和等号 | .5GiB 门槛前 1 byte 拒绝，恰好 512MiB 通过，allocation baseline 保持。test_fractional_ram_threshold_is_inclusive_and_measured_in_gib。无需修复。 | 资源数字替身，不改默认 60GiB。 |
| R03 | 普通错误 message 的 marker 字样 | HTTP 500、bad_request 或连接诊断带 structured_output_failed 原重试 3 次。仅专门异常可重试；真实 502 其它 code 的 marker 文案也不重试。两方法覆盖普通替身与真实 wire。 | 错误文字不能授权重试；普通网络失败不被吞掉。 |
| R04 | 实际 502 与 legacy 422 的确切错误 code | 当前运行包为 502+error.code=structured_output_failed；保留已测试 legacy 422。Client 标记专门异常，redaction 保留类型，两者仅修复 1 次。另测成功重试只使用其 usage。 | 真实 socket，生成器替身；官方 V3/CPU 同样验证实际 wire 分类。 |
| R05 | code 与不相符 HTTP 状态的组合 | 400/429/503 即便 code 为 structured_output_failed 原也重试；现为普通错误且 generate 仅 1 次。test_other_http_statuses_do_not_acquire_repair_authority_from_the_code。 | 三状态属同一授权根因，不声称新增 rate-limit 重试策略。 |
| R06 | options 覆盖消息及服务身份 | messages 原在 dict 合并时覆盖 prompt/history 的校验结果；model/stream 与实际服务身份/非流式契约冲突。generate 前拒绝三个保留键。test_reserved_options_cannot_replace_validated_messages_or_service_identity。 | 公开采样及额外 API 选项继续支持，没有限制所有未知参数。 |
| R07 | 额外 API 选项兼容透传 | max_completion_tokens、presence_penalty、chat_template_kwargs 保持，refresh 不发送，最后 user prompt 不变。test_additional_supported_api_options_are_preserved。无需修复。 | 请求构造验证，不承诺所有第三方参数均由原生引擎实现。 |
| R08 | history 中超 128 层扩展值 | history 原可解析，晚于资源修改才被运行包拒绝。整批 JSON 在 profile/锁/Client/GPU 前预检。test_deep_history_extensions_fail_before_any_profile_or_gpu_transaction。 | 配套第五组服务端深度规则，本组关注节点资源修改前的入口。 |
| R09 | Schema const 字面值的深度 | const 中 130 层数据是合法 Schema 字面值，原绕过 schema 子资源检查、晚于资源修改失败。请求预检提前拒绝。test_deep_schema_literal_data_fails_before_any_profile_or_gpu_transaction。 | 不把 const 内键误当 Schema keyword；与 R08 共用同一根因。 |
| R10 | 出站对象的非字符串成员名 | 1 和 "1" 原可变成重复 JSON 成员，再被服务拒绝。递归要求字段名为字符串。test_non_string_request_member_names_cannot_serialize_as_duplicate_json_keys。 | 第六组关注档案保存，本组关注请求/GPU；普通浏览器 JSON 本来只有字符串键。 |
| R11 | API 扩展值的编码及有限数值 | Infinity/NaN/object/surrogate/超出 Python JSON 编码上限的整数原在交接后失败。资源前给固定节点错误，无 profile、locks、Client、handoff。test_nonfinite_and_non_json_api_extensions_fail_before_resource_mutation。 | 既有规范采样检查保持，本轮关注附加字段。 |
| R12 | core.generate 批次及消息形状 | 空批次原仍交接/清理，非法项可裸抛异常，65 项可绕过 widget 范围。要求 1–64 请求对象、非空消息数组、对象成员。test_empty_and_invalid_request_batches_cannot_open_a_resource_transaction。 | Batch/ImageBatch 既有 widget 输入回归不另计轮。 |
| R13 | 后一项失效时的批次前置原子性 | 第一项正常，第二项 extension 过深；整批预检完才取锁，不让第一项先占用/提交。test_invalid_later_batch_item_is_rejected_before_the_first_request。 | 与旧“生成第二项失败后释放”属不同阶段；没有网络请求。 |
| R14 | 共享 JSON 子树和真正循环 | 两字段共享合法子对象仍成功；self-cycle 提前受控失败且无锁/资源工作。test_shared_json_subtrees_are_not_mistaken_for_a_cycle、test_cyclic_extension_is_a_controlled_preflight_error。 | 生成/HTTP 替身，JSON 实际执行；不把重复引用误判为循环。 |
| R15 | 恰好 128 个容器层 | 请求根加 127 层 extension 通过，返回结果保持。test_exact_128_container_levels_remain_accepted。无需额外修复。 | 对齐运行包 depth>=128 规则，不扩大可接收范围。 |
| R16 | chunked extension 和 trailer | 真实响应含 chunk size 扩展及 X-Audit trailer，只消费 JSON body，得到单个对象。test_chunked_extensions_and_trailers_do_not_become_json_body_bytes。无需修复。 | 新的合法 framing 组合，不重复计第五组歧义 framing 修复。 |
| R17 | 直接 Client 的深层请求 | 绕过节点时原仍 connect，再由服务拒绝；共用预检后 connect 不调用。test_direct_client_deep_json_is_rejected_before_connect。 | HTTP connection 替身；与 R08 的 GPU 事务是不同入口，共享同一修复。 |
| R18 | 非二值 alpha CPU 合成 | 1×2 RGBA：50% 红色与不透明蓝色，实际 PNG 解码得到 (255,127,127)、(0,0,255)，次序保持。test_partial_alpha_preserves_pixel_order_and_blends_against_white。无需修复。 | NumPy tensor 替身/实际 PIL；官方探针另用 torch requires_grad CPU tensor。与第四组透明端点不同。 |
| R19 | 排队后编辑档案名 | load 保留当时 test，之后 later-draft 不改变入队 Connection，也不被完成结果擦除。test_queued_profile_is_fixed_while_a_later_local_draft_changes。无需修复。 | 实际 JS、队列 promise 替身；receipt 只是入队，不代表加载完成。 |
| R20 | 嵌套队列诊断及 key 草稿 | node_errors message/details 显示，失败不擦除私有 key 草稿，入队 body 不发送该 key。test_nested_queue_diagnostics_are_visible_without_erasing_the_key_draft。无需修复。 | 实际 JS、DOM/API 替身；新验证草稿与嵌套错误的组合。 |

## 适用范围与功能冻结

子 Agent 没有下载模型、初始化 CUDA、修改真实用户配置、停止用户服务、提交或发布。随机 loopback 服务已关闭；profile 与锁均在临时 HOME。GPU 信息和生成器为替身，不能据此宣称实际推理或显存释放。官方 CPU 探针出现已装 Torch 的可选优化提示，使用路径未调用 CUDA。

冻结 SHA256：core.py 为 c1a4a233e8e1ed76f80a84e5c9ec4aa4c7f1731e20362f3dc2e7b94360eb5b41；nodes.py 为 834e50f2aa802e8c4e684e5368a0b937b1e49fd52d1d5e422b73a138dee0e41e。panel.py 与 web/strata.js 无功能改动。主 Agent 后续真实 GPU 工作流、升级版本及发行证据需另行记录，本段不将 CPU/替身结果冒充实机验收。

既有外部 API 在状态检查之间启动任务的竞争、旧草案 mixed dependencies + anchor 的第三方兼容边界仍保留。20 个焦点不证明不存在其它 BUG。


## 冻结源码 GPU 验收

ComfyUI 0.38.0 / Torch 2.7.0+cu128 / RTX 4060 Ti 16GB / 128GB RAM / DreamShaper 8：text-to-image 23.17s，1 图；image-to-image 26.17s，1 图；storyboard-to-images 56.47s，2 图。队列 load、缓存重复及每次文字/视觉引擎释放均通过，最后停止自有托管服务。8 份相关功能源码 SHA256 与冻结文件匹配。

首轮三类流程已经通过，交叉审查补充尾参数修复后重启 ComfyUI并完整重跑全部三个流程；只将上述最终数字作为冻结验收。验收档案显存阈值 8GiB，产品默认 12GiB。可选 GLSL 模块未安装且未参与测试。未停止用户其他服务，未下载主模型。证据为 audit7-graph-results.json 与 audit7-comfy-graphs-final.log。


## 正式发行核对

最终源码 `e89b8e385c93cbd7178d5ce37d0217d463171718` 的[源码 CI](https://github.com/T8mars/Strata-T8/actions/runs/37476103478) 成功：596/211/258 例。源码 CI 中 5 个嵌入式运行库用例因没有运行库明确跳过；[正式发行构建](https://github.com/T8mars/Strata-T8/actions/runs/37476105604) 安装真实运行库后，**596/211/258 全部通过，无跳过**。

[整合包 0.1.39-t8.14](https://github.com/T8mars/Strata-T8/releases/tag/v0.1.39-t8.14) 正式发布并为 latest，清单绑定上述提交。构建端检查完整文件 SHA256 和 ZIP CRC。

| 发行包 | 字节数 | 文件数（不计清单） | ZIP SHA256 |
| --- | ---: | ---: | --- |
| VisionReady-NoMainModel | 1,922,458,381 | 8,024 | `2bd2c8490b63e515b7a62dbf7cd3dfda844e92fc5ddb8050fa28160cde1a32a2` |
| Portable-NoModels | 1,208,771,744 | 8,023 | `6fa0b5c5bbf42a67fd09f5381167a3bccc1e5437598c6c230b38ca9203da5ad2` |

再次核对 GitHub 资产摘要、公开 SHA256 文件、两个远端 ZIP 清单及 20 份关键源码/配置，公开范围验证合计读取 4,230,267 字节。未重新下载整合包或主模型。VisionReady 含 Python、锁定依赖、CUDA/HIP 引擎、固定视觉权重和配置；两版都不含主模型和 MTP，NoModels 不含任何权重。

最终提交本机文字推理返回 STRATA_OK，18.76s；视觉识别红圆、蓝方块和测试文字，4.03s。引擎已卸载，HTTP 在 `127.0.0.1:8082` 在线。最终三个 GPU 流程的 8 份功能源码摘要与当前文件一致。

[节点 1.0.6](https://github.com/T8mars/Comfyui-Strata-T8/releases/tag/v1.0.6) 源码 `5dc9416e9b97dfe8135ff2789d3e2718b96c09ed`，[Windows/Linux CI](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37476047831) 和[官方发布工作流](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37476048071) 成功。Windows 211 例全执行，Linux 211 例中跳过 1 个 Windows 专属用例。GitHub ZIP SHA256 `e93889dfc4d005eda6d9179752fc24bfb42b6d445a6cc6176e420e4491065a4c`，Registry ZIP SHA256 `d50ba230978ab1b1a166238713c0bd42d5e83f2c18fec549c15b4cd7988f148a`；代码逐字节匹配发布提交，10 个节点可导入，没有模型或个人档案。

Publisher `t8star`；[Registry 版本接口](https://api.comfy.org/nodes/strata-t8/versions/1.0.6) 核查为 `NodeVersionStatusPending`，CDN 包可取得。提交成功不表示 Manager 已可检索，审核期间可安装 GitHub Release。

证据位于忽略目录 `.portable-build/`：audit7-cloud-root-ci.log、audit7-cloud-node-ci.log、audit7-cloud-node-publish.log、cloud-v14-release-success.log、release-v14-published.json、node-v106-published-verification.json、registry-v106-final-status.json、deployed-v14-results.json 与前述回归/GPU 日志。本段为发布后文档补充，没有替换已发布的不可变版本或资产。
