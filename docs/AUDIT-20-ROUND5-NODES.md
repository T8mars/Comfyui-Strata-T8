# ComfyUI Strata-T8 第五组 20 轮检查

日期：2026-10-06。不可变节点基线为 1.0.3，提交 `46437e9d6427525cc5b79bf54f08128372cc1d12`；运行包基线为 0.1.39-t8.11。先读取项目上下文、源码和前四组记录。本组是 20 个新检查焦点，旧回归重跑不计轮数。

本组修复 **9 类缺陷**：Schema 草案及引用作用域、Unicode、HTTP 响应 framing/声明大小、真实 HTTP 取消、请求大小、未确认的子进程归属、非同卡 Control 协议/活动预检、刷新已有草稿、异步状态覆盖。同一机制的多个反例合并计数，20 轮不等于 20 个 BUG。保留 10 个 node ID、V3/V1 适配和 Strata 协议 1。

## 实际证据

- 旧代码完整基线：**131 tests OK，14.614s，无 skip**，日志 `E:\Strata\.portable-build\audit5-node-before.log`。
- 新增 `tests/test_audit_round5.py` **29 例**，包含旧草案 anchor 受控拒绝及 JSON Pointer 替代方案的最终补强。最终模块 **29 tests OK，6.631s**。其中 2 例实际调用 Strata Python Service HTTP，再做节点本机 Schema 验证；ResidentEngine/MockEngine 提供推理替身，没有初始化 CUDA。
- 最终完整回归 **160 tests OK，21.968s，无 skip**，日志 `E:\Strata\.portable-build\audit5-node-final.log`。命令：先设置 `$env:STRATA_SOURCE_DIR='E:\Strata'`，执行 `E:\Strata\runtime\python\python.exe -B -X utf8 -m unittest discover -s tests -q`。这是最终受控拒绝补强后的完整执行。
- 不可变反例：从上述基线提交把节点及旧 fixture 复制到临时目录，加载修正后的新增模块，选择 **22 个缺陷测试方法**。**全部方法均失败**，29 failures/14 errors（含 subtests），12.788s。脚本逐个核对 `every_selected_method_failed=true`；这不是把 29 例全套都说成失败。证据为 `audit5_node_baseline.py` / `audit5-node-baseline.log`，均位于主仓忽略目录 `.portable-build/`。
- 实际 Node.js 22.19.0 执行前端 handlers，DOM 与 API promise 为替身。JSON、临时文件、PIL PNG，以及随机 loopback 端口 HTTP socket 实际执行。
- 额外实际官方 V3 CPU 探针通过：中文/emoji Prompt 输出、Structured 原生 STRING 列表及自定义整批列表、Batch 配对输入、官方 `output_is_list` 正确。Python 3.11.6 / Torch 2.7.0+cu128，输出 `cuda_initialized:false`。命令：`G:\comfyUI(1)\comfyUI\.ext\python.exe -B -X utf8 E:\Strata\.portable-build\audit5-node-official-probe.py`；证据 `audit5-node-official-probe.log`。真实 ComfyUI API 源码与辅助依赖来自主 Agent 的验证目录，server 注册与中断 hook 使用替身。
- 旧 Transport fixture 仅补 `response.getheaders.return_value=[]`，使 mock 具有真实 HTTPResponse 的头列表接口；没有删测试、跳过失败或弱化任何旧断言。`git diff --check` 通过。

## 20 个新焦点

| 轮次 | 检查目标 | 反例、处理和实际验证 | 边界 |
|---|---|---|---|
| R01 | 错误类型及未知 `$schema` 声明 | 根 `$schema=[]/{}/7` 原在 validator 选择前裸抛异常，未知 URI 又被默认接受。先要求非空字符串且草案已注册。`test_wrong_dialect_types_are_controlled_before_model_work`。 | 不读取远程草案；不存在的草案不是默认 2020-12。 |
| R02 | 带 `$id` 的跨草案嵌入资源 | 2020-12 父资源的 meta 检查原把合法 Draft 7 tuple `items` 当成非法 2020-12 keyword。父检查仅遮罩直属子 Schema，保留容器及 boolean 约束，再按每个资源自己的草案检查。单元正反例与 `test_compound_tuple_survives_real_http_and_local_validation_then_releases` 均通过。 | 真正带 `$id` 的 compound resource；HTTP 模型为替身，starts/closes=1/1。 |
| R03 | 子草案对孙级 keyword 的继承 | Draft 7 子资源的孙级 `$dynamicRef` 是注解，原扫描又采用根 2020-12 误拒绝。递归携带父资源 validator，保留普通 `type` 校验。`test_grandchild_reference_keyword_inherits_the_child_draft` 及实际 HTTP inherit-annotation 用例通过；`const` 内 `$schema/$ref` 保持数据含义。 | 与主仓 Schema Agent 交叉验证；没有获取注解 URL。 |
| R04 | 旧草案 `$ref` sibling 的执行语义 | Draft 7 `$ref` 根的 `properties` sibling 原仍被当作有效远程引用检查。按 `_APPLICABLE_VALIDATORS` 扫描实际执行 keyword；旧草案只有 `$ref` 时仅跟随目标，不执行 sibling 子断言。`test_draft7_ref_siblings_do_not_activate_unused_remote_assertions`。 | 被 `$ref` 实际选中的目标仍必须前置检查；不是全面放开远程引用。 |
| R05 | URI fragment 的 percent/tilde 组合指针 | `#/$defs/a%20b~1~01` 正确选择带空格、斜线及字面 `~1` 的定义，错误实例被拒绝。`test_uri_fragment_pointer_percent_and_tilde_decoding_keep_the_selected_schema`。无需新修复。 | 此 Schema URI fragment 语义与 R08 普通 JSON Pointer 区分。 |
| R06 | 解码后的未配对 UTF-16 surrogate | Escaped surrogate 原能从 JSON/Extract/HTTP 响应流入 ComfyUI STRING，再在 UTF-8 序列化时失败。严格 parser 对解码结果验证 UTF-8；Structured 转成有界输出错误。两个 parser/wire 用例通过，合法 `\ud83d\ude80` 保留为 🚀。 | 固定错误不回显 body 或假 API key；原档案写入事务测试保留。 |
| R07 | 连接输入中的原始无效 Unicode | Prompt/system/history/template/question 和 Batch 后一条文本原可在生成/图片拷贝后才失败。构造 request 前验证文本 Unicode，历史再使用严格 parser。`test_unpaired_request_text_fails_before_generate_or_image_copy`。 | 六种节点输入组合均无 generate/encode 调用；合法中文不被拒绝。 |
| R08 | 普通 JSON Pointer 的 Unicode、空字段及 percent 字面键 | `//a%20b/🚀~1键~0` 保留 `%20` 字面含义，`//a b` 选择另一字段；普通 pointer 不接受 `#` URI fragment。`test_json_pointer_unicode_empty_and_percent_literal_keys_are_not_uri_fragments`。无需新修复。 | 实际 JSON/Number 输出，未进行不应有的 URL 解码。 |
| R09 | HTTP 响应的歧义 framing | http.client 原接受负 Content-Length、重复长度及 chunked+length 混用，未知 identity transfer 也可算成功。读取实际头列表，拒绝重复/冲突/非法长度及非 chunked transfer。`test_ambiguous_response_framing_is_not_accepted_as_success` 的五种真实 socket 反例通过。 | 正常 chunked、正确单长度和合法 close-delimited 旧回归保留。 |
| R10 | 仅有超大声明头、迟迟不发送 body | 声明 16MiB+1 的响应原仍等待整个 body，最后报总 timeout。在 header 阶段拒绝，测试不用等服务器发送 body。`test_oversized_declared_response_is_rejected_without_waiting_for_a_body`。 | 不按声明大小预分配；仍实际检查读取 body 上限。 |
| R11 | 真实响应头尚未返回时取消 | Windows 的 buffered header read 可在 socket shutdown 后继续等待；原固定 join(5) 使取消等到测试服务器 3 秒后响应。HTTPResponse/connection 由 worker 独占关闭，主线程 shutdown/close 原 transport 后仅短暂 join。真实 socket 用例在 1 秒测试预算内返回原取消异常。 | 这是取消交互预算，不是模型/GPU 已释放的证明。 |
| R12 | 真实 body 阻塞与 shutdown 失败 | 主线程调用 connection.close 可等 worker 的 buffered-reader lock。仅 worker 关闭 response/connection 后，body 阻塞的取消及时返回；另验证 shutdown 抛 OSError 仍保留 InterruptedError、关闭 transport 且 auto_open=false。两个新增用例通过。 | daemon read 可到对端响应或 socket timeout 才最终结束；独立 cleanup/status/owned stop 仍负责确认资源释放。 |
| R13 | 超大 JSON 请求的联网前边界 | 图片/长文本请求原没有 body 上限，超 64MiB 仍建立连接发送，最后被服务端拒绝。worker 在 connect 前序列化并检查 UTF-8 bytes 上限，取消标志也在连接前检查。`test_outbound_json_limit_is_checked_before_connect`。 | 与现有服务 64MiB generation 上限一致；测试实际构造上限字符串，HTTP 为替身。 |
| R14 | 自有父进程在子树枚举前消失 | children 枚举抛 NoSuchProcess 时，原把它当空子树并误报已释放；可能仍有未确认的孤儿资源。明确阻断且保留 owner 记录。`test_parent_disappearing_before_children_enumeration_cannot_claim_release`。 | psutil 替身；没有杀真实进程，也没有凭父进程消失承诺显存释放。 |
| R15 | 非同卡 Control/status/start/load 的协议验证 | 原 status/start 原样返回协议 2，非同卡 load 还会先 POST load。统一验证 protocol 1；load 在任何修改前取状态验证。`test_control_status_and_non_same_gpu_load_reject_wrong_protocol_before_mutation`。 | stop/unload 的已确认兜底结果仍保持 `{stopped,released}` 原契约。 |
| R16 | 非同卡 load 已有活动请求 | in_flight=1 原仍会 POST load。所有 load 都要求 idle，且拒绝后没有 unload/cleanup。`test_non_same_gpu_load_does_not_mutate_an_existing_active_request`。 | 外部服务状态与请求为替身；节点锁不能隔离其它 API 使用者。 |
| R17 | 混合 property/schema dependencies 的顺序 | 旧 referencing 依首值枚举 dependencies，数组先出现时会把属性名当 Schema 或漏后位 Schema。显式排除 property-name 列表并发现每个 dict/bool schema dependency；Draft4/7、两种顺序的正反输出验证和两种顺序的远程引用前置拒绝通过。额外实证 schema-first+anchor 原裸异常，现推理前受控拒绝；改用 `#/definitions/t` 后正反实例通过。 | 本组未改第三方 Registry；旧草案该有效 anchor 组合暂不支持，改用 JSON Pointer。 |
| R18 | 最后一张图片转换完成后的取消窗口 | 真实 PNG CPU 转换期间设置中断，随后 profile 锁入口应在任何档案读取/Client 创建前拒绝。`test_cancel_after_last_image_conversion_never_enters_an_inference_transaction`；没有创建 locks 目录或提交部分图片。无需新修复。 | NumPy 驱动 tensor 替身及真实 PIL，mm interruption hook 为替身。 |
| R19 | 点击刷新前已存在的未保存草稿 | 原仅比较 refresh 开始/结束的草稿，未识别早已修改的字段；刷新会调用 onchange，丢失内容和新 key。记录最后干净草稿，仅在未编辑时自动载入；显式选择及成功保存仍可刷新配置。Node.js `test_refresh_preserves_edits_made_before_the_refresh_started`。 | 与第三组“请求进行期间修改”是不同窗口；新 key 只在临时 DOM。 |
| R20 | 不同面板动作的晚结果和晚错误 | load 先发出、status/refresh 后完成，旧 load 返回或错误原仍覆盖新视图。为状态显示建立 generation，旧 completion 不再写当前状态，按钮 finally 独立复位。两个实际 JS 用例通过，同时核对已入队 profile 固定且无 API key。 | API promise 可控；不把排队 receipt 当作实际推理完成。 |

额外 `test_changed_managed_key_invalidates_an_old_fingerprint_before_preparation` 验证旧实例 stop → handoff → native preparation 的顺序，仅用 subprocess/psutil 替身，未发现新缺陷，不额外计为第 21 轮。官方 V3 CPU 探针也是补充证据，不增加轮数。

跨草案资源选择参考 [JSON Schema 官方资源组织说明](https://json-schema.org/understanding-json-schema/structuring)；按已声明 validator 校验参考 [jsonschema 官方文档](https://python-jsonschema.readthedocs.io/en/stable/validate/)。本报告的正反结果来自实际运行。

## 适用范围和剩余边界

没有下载模型、启动 CUDA、停止用户服务或改变用户档案。子 Agent 没有改版本/README/CI，没有提交或发布。真实 HTTP 集成监听随机 loopback 端口并在退出时关闭；GPU 三种正式绘图、版本和发行物核对由主 Agent 后续处理。

Windows 真实 socket 取消只验证及时返回原中断及关闭原 transport；阻塞读 worker 仍可到对端/timeout 才最终退出。生成 finally 的 cleanup 仍须独立查 protocol 状态、卸载/确认自有停止及显存基线，不能把取消返回或 daemon thread 状态当作 engine/GPU 释放。

Schema 预检的 scope/草案算法与主仓 Agent 对齐，但没有宣称支持所有合法 compound schema。当前 referencing 旧草案在“schema-first 混合 dependencies + 本地 anchor”下可能于 Registry crawl 把后位属性数组当 Resource；现在前置捕获该 TypeError/AttributeError，返回节点错误并建议改用 JSON Pointer，尚不直接支持这一有效 anchor 组合。`#/definitions/t` 替代方案及本组无 anchor 的依赖组合实际通过。这是第三方枚举边界，未在本组替换其 Resource 或 Registry 内部实现。20 轮检查不能证明无其它缺陷。

## 主 Agent 后续验收与发布

此段留给主 Agent 追加实际 GPU、Windows/Linux CI、GitHub Release 及 Registry 核对证据；不与子 Agent 的推理替身/CPU 探针混为同一次验收。

## 最终本机回归

受控 anchor 拒绝补强后，新增模块 **29 例通过，6.631s**；全套 **160 例通过，21.968s，无跳过**。升版至 1.0.4 并更新运行包元数据后，再次执行全套 **160 例通过，22.383s，无跳过**。日志分别为 `audit5-node-new-final.log`、`audit5-node-final.log`、`audit5-node-v104-final.log`。

不可变基线选取最终 **22 个缺陷方法全部失败**，含 subtests 为 29 failures、14 errors，12.788s；脚本核对 `every_selected_method_failed=true`。最初探针的 DOM harness 曾错误解析 GET options，修正后的上述不可变运行作为最终复现证据；没有将初版 harness 错误计为产品缺陷或通过。
