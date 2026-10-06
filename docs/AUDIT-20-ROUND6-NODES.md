# ComfyUI Strata-T8 第六组 20 轮检查

日期：2026-10-06。不可变节点基线为 1.0.4，提交 `83d0286cafa72f21a8d29f04c1853e2349de05da`；运行包基线为 0.1.39-t8.12。先读取 AGENTS、meta、features、pyproject、源码、测试及前五组报告。本组为 20 个新的检查焦点，旧回归重跑不计轮数。

本组修复 **11 类缺陷**：档案 JSON 预检、缓存文件可用性、托管运行包 metadata/版本预检、HTTP 成功状态、锁错误分类、视觉能力检查顺序、非同卡自动清理的活动预检、Control 最后释放快照、准备取消时的未知子树、Control 文本透传、面板原始 JSON 保存。相关反例合并计数，20 轮不等于 20 个 BUG。保留 10 个 node ID、V3/V1 适配及 Strata 协议 1。

## 实际证据

- 基线完整回归 **160 tests OK，22.343s，无 skip**，日志 `E:\Strata\.portable-build\audit6-node-before.log`。
- 新增 `tests/test_audit_round6.py` **28 个方法**。首个修复全套 **188 tests OK，24.113s，无 skip**，日志 `audit6-node-full-second.log`；补强 normalize 阶段拒绝断言后，模块 **28 tests OK，3.277s**，日志 `audit6-node-new-final.log`。主 Agent 升至节点 1.0.5 / 最低运行包 0.1.39-t8.13 后另执行完整 **188 tests OK，25.736s**；最终强化图片尺寸元数据后再跑完整 **188 tests OK，24.400s，无 skip**，日志 `audit6-node-final.log`。
- 不可变基线：将上述提交的源文件与旧 fixture 提取到独立临时目录，加载最终新增模块，选取 **16 个缺陷方法，全部失败**。含 subtests 为 **25 failures、6 errors，2.399s**；脚本核对 `every_selected_method_failed=true`。这不是把 28 个方法全说成失败。复现脚本及日志为 `.portable-build/audit6_node_baseline.py`、`audit6-node-baseline.log`，均位于主仓忽略目录。
- 命令：设置 `$env:STRATA_SOURCE_DIR='E:\Strata'`，使用 `E:\Strata\runtime\python\python.exe -B -X utf8 -m unittest discover -s tests -q`。新增重定向/interim HTTP 用随机 loopback socket；跨进程锁实际创建独立 Python 子进程、取得 Windows byte lock，取消等待后通过 stdin 正常释放。
- Node.js 实际执行新面板 handlers，DOM/API 为替身。新增 harness 有完成标记与 watchdog，不把没有等待完的 promise 当作通过。
- 额外实际官方 V3/CPU 探针通过：10 IDs、非文件缓存指纹、保存后缓存失效、Control 无效文本在动作前拒绝、中文/emoji 与 requires_grad CPU tensor 身份透传、Structured 原生 STRING 列表及 Batch 自定义整批列表。Python 3.11.6 / Torch 2.7.0+cu128，`cuda_initialized:false`。
- 同一 CPU 探针还用 **真实 aiohttp 路由与随机 loopback HTTP** 验证新 `profile_json` 接口：重复字段返回 400 且原档案逐字节不变；合法原文保存返回 200，`__KEEP__` 保留旧假 key；GET profiles 不返回凭据。ComfyUI 的路由挂载入口为替身，aiohttp、JSON parser、原子档案写入与 HTTP 均实际执行。
- CPU 探针命令：`G:\comfyUI(1)\comfyUI\.ext\python.exe -B -X utf8 E:\Strata\.portable-build\audit6-node-official-probe.py`；证据 `audit6-node-official-probe.log`。使用主仓已有的实际 ComfyUI 0.38.0 API 源码及辅助依赖；没有启动 CUDA。
- `git diff --check` 通过。旧 prepared-runtime fixture 补上真实 metadata（最低版本读取本仓 meta）；旧 JS 断言适配独立 API-key 字段，清除语义保持。第三组的 Unicode 写入失败事务改为注入写入异常，保留原异常类型、原文件及无 temp 三个断言；新模块单独验证无效 Unicode 在 normalize 阶段拒绝。没有删测试、跳过故障或改版本/README/CI。

## 20 个新焦点

| 轮次 | 检查目标 | 反例、处理和实际验证 | 边界 |
|---|---|---|---|
| R01 | 档案 URL、扩展值及成员名的 Unicode 预检 | normalize 原接受未配对 surrogate，直到写私有 temp 才裸抛 UnicodeEncodeError。整份档案验证 UTF-8 后再允许保存；现 normalize/save 都为明确节点错误，旧文件不变。`test_profile_unicode_is_rejected_before_the_existing_profile_is_replaced`。 | 第三组仅测写入失败事务，本组增加更早的可保存性检查；所有档案为临时文件。 |
| R02 | 程序化扩展字段的非字符串成员名 | `{1:"number","1":"text"}` 原可写出重复 JSON 成员，下一次 strict read 反而失败；bool/null 键会默默改名。递归要求成员名为字符串。`test_non_string_extension_members_cannot_write_a_duplicate_or_changed_json_key`。 | 直接 Python save API 的反例；普通浏览器 JSON 已只有字符串键。 |
| R03 | HTTPS/IPv6 `/v1` 基址与真正发送路径 | 使用 `https://[::1]:443/v1///` 构建 Client，选 HTTPSConnection、主机 `::1`、端口 443，仅发送一次 `/v1/status`，没有 `/v1/v1/status`。`test_ipv6_v1_base_and_https_select_one_canonical_api_path`。无需新修复。 | connection/response 为替身；不声称做了 TLS 证书或 IPv6 网络验收。 |
| R04 | 托管运行包的 metadata 类型、版本标识及界限 | 原裸 json.loads 会抛 AttributeError/JSONDecodeError；缺失或旧版 metadata 还能进入准备。按严格 parser 读取版本、整数协议 1，并按本仓 runtime_min_version 检查，在 owned/端口/GPU 工作前拒绝。损坏、重复成员、旧/缺失版本及新上游 base 正例共 3 个方法通过。 | 托管契约本来要求 Windows Strata-T8 整合包；external 不要求 metadata，仍支持上游部署。 |
| R05 | 带合法 JSON 的 API 重定向 | 实际 socket 的 301/302/307/308 JSON 原均当作成功。明确拒绝非 2xx 的重定向且不跟随；保留 >=400 的原诊断。`test_json_redirects_are_not_success_and_are_not_followed`。 | 没有向 Location 发送另一个请求或凭据。 |
| R06 | interim 100 后的正常 201 JSON | 实际 socket 先发 100 Continue，再发 201 与单个完整 body；正确解析一次并得到对象。`test_interim_continue_then_a_created_json_response_is_consumed_once`。无需新修复。 | 保留正常 2xx；不是把所有非 200 都错误拒绝。 |
| R07 | 永久锁故障与正常争用的区分 | EBADF 原被当作忙锁反复重试，直到工作流中断。仅争用 errno 继续可取消等待，其余立即受控失败。`test_non_contention_lock_errors_fail_before_entering_the_transaction`。 | 注入真实 errno；没有忽略解锁错误或绕过互斥。 |
| R08 | 另一个真实进程占有 byte lock 时取消 | 独立 Python 子进程先锁住同一字节；本进程等待中取消，不进入事务，释放后锁文件仍恰好 1 字节。`test_a_real_other_process_lock_is_cancelled_without_entering_or_growing_the_file`。无需新修复。 | 实际 Windows 跨进程锁；子进程通过 stdin 正常退出，仅使用临时目录。 |
| R09 | 关闭视觉时的外部服务及全新托管启动顺序 | 视觉检查原在 cleanup/handoff 后；新托管 ensure 的 preparation 回调还会更早卸载 Comfy。external 在 status 后、资源修改前检查；managed 根据 profile.vision=no 在 Client/Managed 创建前拒绝。两个独立反例通过。 | 外部 status 与 GPU 为替身；新托管使用实际 profile/runtime 临时文件及真实 ensure 路径，Popen 未调用。 |
| R10 | 非同卡 generate 的自动清理活动范围 | allow_lifecycle=true/same_gpu=false 原可在已有请求时开始生成，并在 finally 清理别人仍使用的服务。要求该自动清理事务初始 idle，拒绝后不发 chat、不 cleanup。`test_non_same_gpu_automatic_cleanup_does_not_adopt_an_existing_active_request`。 | 节点锁不隔离其它 API 客户；本组不承诺消除预检后的所有外部竞争。 |
| R11 | 共享外部服务的并行推理契约 | allow_lifecycle=false/same_gpu=false、serving=2/in_flight=1 时仍可发新请求并保留服务，结果完整且不清理。`test_a_shared_external_service_can_generate_without_claiming_automatic_cleanup`。无需新修复。 | HTTP 状态为替身；与 R10 的明确自动资源清理契约区分。 |
| R12 | Control 最后 after_release 快照的再激活 | cleanup 已确认释放后的额外 status 原未校验；protocol=2、loaded=true、in_flight=1 或 engine.starting=true 都能让 Control 成功。最后快照同样验证协议和全部资源空闲，否则阻断下游。`test_control_final_release_snapshot_cannot_reintroduce_an_incompatible_or_active_service`。 | 四个反例合并为同一后置条件缺陷；替身状态，不作为 GPU 实测。 |
| R13 | 准备取消时父进程在子树枚举前消失 | 外层 NoSuchProcess 原视为“没有 children”，仅抛原中断；无法确认是否有孤儿。标记未确认子树，仍尝试 kill/wait 已知准备父进程，然后返回资源未确认错误。`test_disappearing_preparation_parent_does_not_claim_known_children_have_exited`。 | psutil/Popen 为替身；不把及时取消当作模型/GPU 已释放。 |
| R14 | 交接后 Comfy 后台 allocation 的守卫 | 模拟交接基线后新增 129MiB allocation；在 chat 发送前拒绝，finally 仍 cleanup。`test_background_comfy_allocation_blocks_generation_and_still_cleans_up`。无需新修复。 | Torch CUDA 信息为替身；没有把本轮数据称为显存测量。 |
| R15 | CPU 图片转换上限的前置拒绝 | 形状 8×4096×4097×4 的 numel 超过既有转换上限，应在 detach/to/numpy 前拒绝。`test_large_image_cpu_conversion_is_rejected_before_touching_a_tensor`。无需新修复。 | Tensor 元数据为替身且 shape/numel 一致；没有分配这份大 tensor。 |
| R16 | 无 system turn 时修复提示对原输入的保留 | 第一次无效 JSON 后，修复指令追加到原 user turn，原中文 prompt 和 role 保留，第二次 JSON 通过。`test_structured_repair_without_a_system_turn_keeps_the_original_user_prompt`。无需新修复。 | 生成器替身，关注消息身份而非重复计算修复次数。 |
| R17 | Control 文本透传在副作用前的类型/Unicode 检查 | None/int/list/surrogate 原会先运行 stop 等动作，再输出坏 STRING 或晚抛错。动作前检查文本类型及 UTF-8；负例没有 control 调用。`test_control_text_is_validated_before_any_lifecycle_action`，另有实际 V3 CPU 正反例。 | 可选 IMAGE 保持原 tensor 身份，不做额外 CPU 拷贝。 |
| R18 | Connection 内容指纹的非文件及不可读输入 | 手工连接指向 `.json/` 目录时，原指纹 read_bytes 裸抛 IsADirectoryError；PermissionError 也未规范。非文件返回 missing，真正文件不可读给固定节点错误。两个方法及实际 V3 fingerprint_inputs 通过。 | 与第四组 dropdown 排除目录不同，本轮检查直接连接及官方缓存接口。 |
| R19 | 面板 JSON.parse 丢重复字段及原文保存 | fields 原先经 JSON.parse/JSON.stringify，重复字段已在到后端前丢失。面板发送 profile_json 原文与独立 api_key，后端严格解析后保存，旧 object profile API 继续支持；非对象 draft 联网前拒绝。JS、route 与实际 aiohttp/文件正反例均通过。 | 清空、KEEP 和失败草稿语义保持；测试 key 为临时假 key，GET 不回传。 |
| R20 | `__proto__` 作为合法档案的字面身份 | JS own member 的档案可刷新、选择、读正确字段并按原名称请求状态；实际 V3 Connection 也保存/读取同名档案，不携带 key。`test_a_profile_with_a_javascript_prototype_member_name_stays_a_literal_profile`。无需新修复。 | DOM/API 为替身；不是让任意对象原型作为配置。 |

Windows byte-range 锁采用 [Python 官方 msvcrt 文档](https://docs.python.org/3/library/msvcrt.html) 所述接口；HTTPSConnection/HTTPResponse 行为参考 [Python 官方 HTTP client 文档](https://docs.python.org/3/library/http.client.html)。实际断言和时间来自本次执行。

## 适用范围和剩余边界

子 Agent 没有下载模型、启动 CUDA、停止用户服务或改变真实用户档案。HTTP 监听随机 loopback 端口并关闭；跨进程测试只启动自己的临时 Python 锁持有者。托管 native/PID、GPU allocation、生成器是替身；实际官方 io、CPU torch、JSON、文件、aiohttp、socket、Node.js 则如上分别记录。

不同外部 API 使用者仍可能在状态检查之间启动新任务，节点互斥不能覆盖它们。最终 after_release 只证明所读取快照空闲，不能承诺此后没有外部工作；无法确认准备子树时明确阻断，不能把连接取消与 GPU 释放混为一谈。上一组记录的旧草案 mixed dependencies + anchor 第三方边界仍保留，没有重新计为本组缺陷，也没有宣称全面解决。

最初跨进程 harness 曾在 Windows byte lock 仍持有时尝试读取同一字节，后改为子进程正常释放后核对；无源码改动，不计产品缺陷。最终不可变反例及完成标记后的 JS 结果作为正式证据。20 个焦点不证明不存在其它问题。

## 主 Agent 正式 GPU 和发行验收

ComfyUI 0.38.0 / Torch 2.7.0+cu128 / RTX 4060 Ti 16GB / 128GB RAM / DreamShaper 8，冻结源码完成文字→绘图、图片理解→绘图、两镜头分镜→批量绘图，分别 28.16s、35.22s、58.34s，共 4 张图。队列 load、缓存重复及每次文字/视觉进程释放通过，最后停止自有托管服务。8 份功能源码摘要与发行文件一致；验证档案空闲显存门槛 8GiB，产品默认 12GiB。初次派发在服务 readiness 前连接失败，没有计作通过；上述数字为 readiness 后完整重跑。

[节点 1.0.5](https://github.com/T8mars/Comfyui-Strata-T8/releases/tag/v1.0.5) 为 `0452eb109136fe0c1958d5133612a5af7f31df0e`，配套[运行包 0.1.39-t8.13](https://github.com/T8mars/Strata-T8/releases/tag/v0.1.39-t8.13) 为 `d0a3c5ab0e1775583aba7a7b15c79d6b502f5947`。[Windows/Linux CI](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37469986731) 成功，Windows 188 例全执行，Linux 跳过 1 个 Windows 专属用例；[官方 Registry 发布](https://github.com/T8mars/Comfyui-Strata-T8/actions/runs/37469987350) 成功。GitHub ZIP SHA256 `71717f623abebe949b72164ab8a378ab3d66474cd329cd1c6adc07eb843b47d8`，Registry ZIP SHA256 `ea72c2668725d6fa0de39eb17ff8f5ec35690c7ac41ddcbb1147308aeba743b9`；所有代码逐字节匹配发布提交，10 个节点可导入。

Publisher `t8star`，CDN 包可取得；[Registry 接口](https://api.comfy.org/nodes/strata-t8/versions/1.0.5) 核查为 `NodeVersionStatusPending`。审核期间使用 GitHub Release。详细证据见[主仓第六组报告](https://github.com/T8mars/Strata-T8/blob/main/docs/AUDIT-20-ROUND6-T8.md)。此段为发布后补充，没有覆盖已发布资产。
