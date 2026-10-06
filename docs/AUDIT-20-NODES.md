# ComfyUI Strata-T8 节点 20 轮审查记录

日期：2026-10-06。范围：节点源码、面板以及 `tests/test_nodes.py`，后同步至独立仓库 `E:\\Comfyui-Strata-T8`。

这是 20 个不同焦点的审查轮次；同一组回归重跑只用于关联验证，不作为新增轮次。原 13 个测试现扩展为 50 个节点测试；独立仓库另外保留主 Agent 的 2 个发行物测试。未提交、发布或改变主项目服务端/运行包代码。

## 最终验证

- 主仓完整回归：`E:\\Strata\\runtime\\python\\python.exe -B -m unittest tools.test_comfyui_nodes -q`，49 tests OK；随后新增 ROCm 平台边界 1 例并通过。
- 独立仓完整最终回归（包含所有最终修改）：PowerShell 先设 `$env:STRATA_SOURCE_DIR='E:\\Strata'`，再运行 `E:\\Strata\\runtime\\python\\python.exe -B -m unittest discover -s E:\\Comfyui-Strata-T8\\tests -q`：**54 tests OK，无 skip，7.594 秒**。节点 50 例 + 发行物 2 例。
- 独立仓不提供 Strata source 时：迁移验证时 51 tests OK（skip=11），跳过的是需真实 Python Service/ResidentEngine 的 HTTP 集成。后来新增的 1 个 ROCm 单元测试在完整回归中通过；未再次重复无-source整套。
- Node.js 实际执行面板 JavaScript handlers，配 DOM/API 替身；检查 4 个生命周期动作均生成 /prompt 工作流，status 走只读 endpoint，工作流不带 API key。
- 路径改名验证使用真实独立根目录 `E:\\Comfyui-Strata-T8`，源码根目录没有硬编码旧文件夹名称。

## 审查轮次

### R01 — V1/V3 注册、列表标记、执行返回及缓存接口

- 复现/证据：10个稳定节点ID；用V3接口替身验证schema、原生STRING列表/自定义整批列表及执行输出；Control NaN不可缓存。
- 修复：未发现此契约的缺陷；新增2个注册契约回归。
- 针对性验证：Registration + Structured，7 tests OK（R02共用一组执行，不重复计轮）。

### R02 — 采样参数与文本参数类型

- 复现/证据：top_k=True/2.5通过原校验，temperature=NaN、max_tokens=0、seed=-1未拒绝；这些参数可来自连接节点，UI范围不能代替后端验证。
- 修复：请求构造前检查有限数值、整数/布尔边界、合法reasoning_effort及文本类型。；最终关联边界复查：先界限再isfinite，巨整数不泄露OverflowError。
- 针对性验证：新增nonfinite/boolean/fractional测试，结合原Structured测试通过。

### R03 — 本机档案类型与生命周期授权边界

- 复现/证据：allow_lifecycle:"false" 原先因字符串真值开启卸载；负/NaN timeout和布尔context原先可存盘。
- 修复：JSON对象、真实boolean、有限正超时、非负资源阈值、整数context/port及vision枚举检查；配置保存不受全局工作流中断旗标影响。
- 针对性验证：test_profile_rejects_truthy_false_and_invalid_numeric_values + 原private profiles回归。

### R04 — URL端口、空白/控制字符和端点规范化

- 复现/证据：notaport/0/越界端口和换行URL原先通过保存，运行时才失败；新增用例发现默认端口使用or导致0漏检，已一并修正。
- 修复：URL解析时验证port，拒绝空白和控制字符/嵌入凭据，规范scheme/host/default port及/v1尾斜线。
- 针对性验证：test_profile_validates_ports_and_canonicalizes_endpoint含IPv6、默认端口；目标12 tests最终通过。

### R05 — 凭据文件原子保存与失败残留

- 复现/证据：os.replace失败留下含API key的.tmp；POSIX新temp在chmod前可受默认umask影响。
- 修复：O_EXCL以0600创建文件，finally删除temp，旧配置保持；API key拒绝控制字符和无法发送的HTTP头字符。
- 针对性验证：test_failed_replace_removes_private_temporary_file + invalid_profile_does_not_replace_saved_profile。

### R06 — HTTP总超时而非逐次socket读取超时

- 复现/证据：原请求只有socket timeout，持续少量返回可无限延长；用阻塞响应替身可复现超时不生效。
- 修复：请求开始建立wall-clock deadline，主线程轮询超时，并校验显式timeout。
- 针对性验证：test_total_timeout_closes_detached_response_socket，0.2秒deadline下实测<1秒退出。

### R07 — HTTP/1.0响应头后取消及连接归属

- 复现/证据：getresponse()会把conn.sock移走，响应.fp仍持有socket；原取消只shutdown conn.sock，不能中断已开始读body的worker。
- 修复：记录connect后原transport，取消时shutdown原socket；完成后再次检查中断，保留原中断异常。
- 针对性验证：test_cancel_closes_detached_socket_and_preserves_interrupt + R06 detached-socket回归。

### R08 — 响应格式、错误凭据及STRING返回类型

- 复现/证据：非对象JSON或字符串error产生AttributeError；numeric content可突破STRING输出契约；畸形usage未拒绝。
- 修复：HTTP必须JSON对象，错误字符串规范化并redact key；chat choices/message/content/reasoning/usage在返回前验证，异常仍finally释放。
- 针对性验证：test_bad_response_shape_and_error_text_hide_credentials；test_malformed_chat_completion_releases_resident_engine三种畸形响应。

### R09 — 跨档案端点互斥、取消前入锁及锁文件增长

- 复现/证据：原same_gpu=true只锁GPU、false只锁endpoint，两个档案同一服务可并发控制；127.0.0.1/localhost另分锁；a+b每次追加一字节。
- 修复：固定顺序同时锁GPU（需要时）、endpoint和managed runtime，回环别名一致；r+b仅初始化一字节并在入锁前后检查取消。
- 针对性验证：test_endpoint_lock_covers_aliases_and_different_gpu_flags真实并发等锁；test_lock_cancel_before_entry_and_file_does_not_grow。

### R10 — 释放状态不可把缺失字段视为空闲

- 复现/证据：loaded=false/processes={}原来通过完全释放判断；owned()为空时stop返回None，fallback仍可声称成功。
- 修复：统一验证protocol/model/活动/并发/engine+vision boolean状态；stop显式bool；无法确认自有进程停止则阻断。
- 针对性验证：test_incomplete_release_status_never_counts_as_idle；test_cleanup_cannot_claim_success_without_owned_stop；target分组回归通过。

### R11 — PID/启动时间/脚本/config配对与损坏所有权记录

- 复现/证据：原判断只需cmdline任意位置含service.json，未确保运行真实server或--config配对；NaN created可绕过abs比较。
- 修复：记录并匹配server路径（保留旧owner推导兼容）、严格pid/finite created、真正--config邻接值；损坏记录明确拒绝，不当无owner重启。
- 针对性验证：test_owner_requires_real_server_config_pair_and_finite_creation_time；原运行目录迁移owner测试更新为真实argv仍通过。

### R12 — 准备进程启动失败和owner注册异常回滚

- 复现/证据：config Popen在try外可泄露log；新server psutil.Process在rollback try外，AccessDenied时已启动子进程未清理；准备children枚举失败跳过父kill。
- 修复：Popen置于log finally内；owner获取/记录均在新子进程kill/wait保护内，删除temp；准备取消独立确保父kill/wait。
- 针对性验证：configuration_spawn_failure_closes_log、owner_registration_failure_kills_new_child、preparation_cancel_still_kills_parent_when_child_access_is_denied；OwnedProcesses共12 tests OK。

### R13 — 首次托管配置的 GPU 交接和资源阈值边界

- 复现/证据：Managed.ensure 原在 gpu_handoff 前执行 managed_config；portable.configure 的 setup --yes 路径会启动原生探测/校准。资源阈值的 > -1 检查还接受 -0.5。
- 修复：只在需启动新服务时，在 native preparation 前执行可取消 prepare_check；generate 和 Control 的 managed start/load 接入同卡 handoff；阈值改为真正 >=0。；关联平台边界：ROCm也使用torch cuda设备名，新增hip标记检查，未验证设备在Comfy offload前拒绝。
- 针对性验证：test_first_configuration_handoff_precedes_native_subprocess 验证顺序及取消不启动子进程；OwnedProcesses 13 tests OK，无 GPU 进程。 最终关联边界补10**1000 timeout/max_tokens，2 tests OK。 ROCm模拟设备被拒且不offload，CUDA模拟接受，新增1 test OK。

### R14 — 结构化输出本机校验、修复次数及网络故障重试边界

- 复现/证据：jsonschema.ValidationError 未进入原 except；非法JSON虽进入ValueError却因缺少服务端marker从不修复；repair_attempts可绕过UI设无限次；Python JSON接受NaN/Infinity。
- 修复：本机JSON/Schema失败统一StructuredOutputError，与服务端structured_output_failed一同有限修复；普通网络错误不重试；严格0..2整数；拒绝非标准数值常量。
- 针对性验证：新增2个Structured测试涵盖local/server失败→成功、network不重试、3次上限、非法次数及非标准JSON；Structured 7 tests OK。

### R15 — JSON Pointer 的真实字段选择与 FLOAT 输出边界

- 复现/证据：/items/-1 会静默选择末项，/items/01 接受非标准数组索引，非法~转义未拒绝；Number 接受1e999→Infinity，巨整数float转换抛裸OverflowError。
- 修复：按RFC6901验证数组索引/转义并把不存在或scalar下降变为明确节点错误；提取列表项验证字段；Number拒绝非有限数/溢出。
- 针对性验证：新增2个Structured测试，涵盖数组负索引/前导0/+/-/越界、object数字key、~01、missing/scalar、NaN/Infinity/超大整数；Structured+Registration 11 tests OK。

### R16 — 图片 CPU 转换、维度/像素限制、透明通道和中断

- 复现/证据：零高度/宽度产生PIL或除零异常；Inf先clamp后被隐藏、NaN变uint8警告且内容错误；bfloat16直接numpy不兼容；RGBA丢alpha把透明像素当黑色。
- 修复：先验证正维度及max_pixels UI边界；detach到CPU转float32并有限检查，再clip；RGBA白底合成；保留逐图中断，无部分提交。
- 针对性验证：Images 3 tests OK：fakeTensor记录CPU转移，实际PIL PNG解码/resize/透明合成；坏shape/batch/limit/NaN/Inf均不推理；第二图取消不提交。

### R17 — 批量索引/思考数据、部分失败事务及图片参数预检

- 复现/证据：Image Batch paired_json遗漏reasoning；非法preset/采样/schema会先执行昂贵图片CPU拷贝，unknown preset裸KeyError；第二条失败需验证不放行部分结果。
- 修复：图片与Prompt预检preset/text类型；图片Schema及采样在CPU拷贝前验证；图片批量保留reasoning，与文字批量索引格式一致。
- 针对性验证：Images+BatchHTTP 15 tests OK；新增稳定索引/一次generate事务、invalid不拷贝/不推理、真实HTTP第二项失败后resident engine退出且无部分返回。初次测试使用ResidentEngine默认reasoning未输出最终答案，已按原fixture设reasoning_effort=none后复测。

### R18 — 面板生命周期进入 ComfyUI 队列、Origin/JSON边界及状态取消回调

- 复现/证据：面板只将load入队，start现也会GPU handoff；直接start/unload/stop的running检查存在检查后新graph启动竞态。Control最后status丢check回调，受无关全局interrupt影响；Origin未检查scheme、坏body在try外500。
- 修复：所有面板生命周期按钮经/prompt稳定节点ID入队；直接endpoint仅status，其他409；验证scheme+host/loopback+JSON body，异常不回显潜在凭据；最后status保留显式check。
- 针对性验证：Panel 3 tests OK：fake aiohttp/PromptServer路由的4动作409/状态200、Origin/body/隐私；真实Node.js vm+DOM替身触发5个按钮，4个实际请求携带正确workflow，无key；BatchHTTP回调测试通过。

### R19 — 所有权反例/拒绝访问、Windows档案别名锁及缓存内容变化

- 复现/证据：foreign.py server.py --config path仍能骗过任意argv匹配；AccessDenied被当作无owner；一个child terminate/kill拒绝访问跳过剩余target；Windows TEST/test同文件不同锁；mtime保留配置更新不失效缓存。
- 修复：匹配Python实际script位置，-c/-m不可冒认；AccessDenied明确阻断；独立尝试每个target并等待确认全部退出，失败保留owner；profile锁使用normcase绝对path；Connection fingerprint改内容hash。
- 针对性验证：OwnedProcesses+Registration 18 tests OK；真实Windows case alias并发等锁；2个误导argv、AccessDenied、后续children继续清理/存owner、mtime保持下fingerprint变化。

### R20 — 独立仓库根目录、目录改名、外部Strata测试依赖及最终回归

- 复现/证据：原测试在ROOT前直接import serve，硬编码comfyui-strata-t8路径和主仓serve模板，独立仓库无法运行。
- 修复：NODE_ROOT同时支持主仓嵌套目录与任意名称独立根目录；STRATA_SOURCE_DIR在import前指定外部source；无source仅明确跳过11个HTTP集成，错误env路径直接报错。复制运行源码/公开examples/test_nodes到新仓，不覆盖README/meta/Actions/test_release。
- 针对性验证：主仓49 tests OK；新仓E:\Comfyui-Strata-T8无source 51 tests OK(skipped=11)，设STRATA_SOURCE_DIR=E:\Strata 51 tests OK，无skip；2个metadata/privacy tests原文件保留。Node.js真实执行panel handlers。 最终后续边界修复同步后独立仓完整54 tests OK，无skip；节点50例+主Agent release2例。

## 修复涉及的具体缺陷

- 输入边界：JSON 字符串真值可能打开生命周期权限；非有限/布尔/小数整数/超大整数采样和配置缺少校验；URL 0/错误端口、控制字符及 API key 字符集；负资源小数阈值；无界结构化修复次数。
- 连接与返回：仅 socket 超时无法保证总时长；HTTP/1.0 detached response socket 中断不完整；非对象响应与非标准 error 结构崩溃；错误 key 回显；chat STRING/reasoning/usage 类型缺口。
- 文件与并发：API key 临时文件在 replace 失败后残留；写入期间权限窗口；锁文件增长；同端点不同 same_gpu 标记或回环别名未共锁；取消前仍进入临界区；Windows 名称大小写共文件却分锁；mtime 保持更新未失效缓存。
- 资源和归属：状态缺字段被视为释放；无法确认自有停止仍误报成功；先 native preparation 再 Comfy handoff；ROCm cuda 设备名误进入已验证 NVIDIA 路径；任意 argv 中 server/config 字串误认自有进程；NaN 创建时间；AccessDenied 当作无 owner；一个子进程拒绝访问阻止后续清理；准备启动失败 log 残留及 owner 注册失败未杀新进程；准备取消时枚举失败跳过父进程清理。
- 结构化/提取：本机 JSON/Schema 失败未按设置修复；JSON NaN/Infinity；JSON Pointer 负数组索引/前导零/非法转义静默错误；FLOAT 溢出/Infinity；缺失 item 字段错误不清晰。Pointer 语义依 [RFC 6901](https://www.rfc-editor.org/rfc/rfc6901.html)。
- 图片/批量：零维度和 max_pixels 后端缺验证；NaN/Inf 被 clip 掩盖或转为坏像素；直接 numpy 不支持部分浮点 tensor 类型；丢弃 RGBA alpha；非法 task/schema/sampling 先做 CPU 大拷贝；图片批量丢思考内容。
- 面板：start/unload/stop 直接调用与新 graph 开始之间竞态；末次 status 丢取消回调；Origin 忽略 scheme；坏 JSON body 导致 500；通用异常可回显不必要内容。
- 独立测试：直接依赖主仓 serve import、固定旧目录、主仓 Jinja 模板导致 standalone 无法执行。

## 测量和适用范围

- 本轮没有启动/停止真实 GPU/native 引擎，没有下载权重。HTTP 集成实际监听本机随机端口并使用项目 Python Service、ResidentEngine 替身；其 load/unload 是可观察的 Python 状态，不是 VRAM 测量。
- HTTP 断开/总超时包含 socket/响应替身；PID/children/拒绝访问用 psutil/Popen 替身。Windows profile/endpoint 文件锁使用真实线程与真实文件锁。不能据此代替原生进程退出或 GPU 实测。
- V3 schema/list/output/cache 适配用官方接口形状替身验证；真正 ComfyUI 三种绘图工作流由主 Agent 随后验收。
- 图片 PNG/PIL 转换、resize 与 alpha 合成实际运行；tensor 为 NumPy 驱动替身，验证 CPU 调用和 float16→float32。真实 CUDA/bfloat16 图片张量仍需 ComfyUI 实测。
- 本机档案仍在独立 HOME；工作流只记录名称。面板必须 loopback；远程 external 服务只在显式生命周期授权时控制。所有权无法确认或释放失败会阻断下游。
- 同卡跨档案锁只管理这些节点的事务，不能隔离外部 API 调用、后台插件或异步第三方 GPU 分支。AMD 同卡交接、Linux 托管运行包、多 GPU 未验收。
- 这份审查没有证明项目不存在其他 BUG。

R20 实机跟进：另增两例 HTTP 503 诊断传递/部分加载资源清理回归。节点 52 例与发行物 2 例，最终 54 例均通过。原生显存不足由 Strata 返回 engine_load_failed/503；节点保留诊断并确认卸载。实机加载验收改为 /prompt 中的 Control(load)，避免验收 helper 的额外 CUDA context。

本轮拆仓后实机复跑通过：text-to-image 37.33s，1张图；image-to-image 28.25s，1张图；storyboard-to-images 52.53s，2张图。共4张图；每次返回后语言/视觉进程均停止。Control(load)通过ComfyUI队列，缓存重复请求通过。
