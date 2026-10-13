# ComfyUI Strata-T8

## 安装与配置

通过 ComfyUI-Manager 安装 Strata-T8（Publisher `t8star`、节点 ID `strata-t8`），或下载 [节点 Release](https://github.com/T8mars/Comfyui-Strata-T8/releases/latest) 解压到 `ComfyUI/custom_nodes/Comfyui-Strata-T8`。使用 ComfyUI 自身 Python 安装该目录的 `requirements.txt`（仅 jsonschema），重启。Strata 使用独立 VisionReady 运行包的 Python、CUDA/HIP 和引擎；导入节点不会启动引擎、下载权重或执行 pip。ComfyUI 与绘图模型由已有环境提供。

打开侧栏 **Strata-T8**，填写配置名称并保存连接 JSON，刷新页面后选择配置。API key 在独立密码框填写，留空保留；勾选“清除已保存 API key”时 external 清空，managed 重新生成。新托管配置自动生成。配置默认在 `%LOCALAPPDATA%/Strata-T8-ComfyUI`，可用 `STRATA_COMFY_HOME` 指定；此目录不应分发，工作流仅存配置名称。

```json
{
  "mode": "managed",
  "runtime": "D:\\Strata-T8",
  "data_dir": "D:\\Strata-data",
  "port": 8082,
  "vision": "gpu",
  "context": 32768,
  "same_gpu": true,
  "min_free_vram_mib": 12288,
  "min_free_ram_gib": 60,
  "timeout_s": 1800,
  "cleanup_timeout_s": 90
}
```

`vision` 可设 gpu、cpu 或 no。视觉需要兼容的 Qwen 主模型；Windows HIP 文字档案设 vision:no、same_gpu:false，未做 AMD 实机验证。

连接已有服务使用 mode:external、url:http://127.0.0.1:8080。默认 allow_lifecycle:false、same_gpu:false，只请求推理；远程服务保留 same_gpu:false。本机同卡共享时明确设两者 true，服务须为 Strata-T8 协议 1、parallel:1。节点不结束外部服务进程。

## 功能

| 节点 | 输入与输出 |
| --- | --- |
| Connection | 按名称读取本机档案，输出连接 |
| Text | 系统提示、用户文字及 JSON 历史；最终文字、思考、用量 |
| Prompt | 扩写、翻译、改写、图像/视频、正负提示词；可编辑模板 |
| Structured | JSON Schema 或默认分镜；校验 JSON、原生 STRING 列表、批量节点用列表 |
| Extract / Number | JSON Pointer 字段提取；文字/列表或 FLOAT，类型错误报错 |
| Vision | 1–8 图描述、反推、OCR、问答、主观评价，可选 JSON Schema |
| Batch / Image Batch | 最多 64 条文字或 8 图，顺序推理、一次载入；逐项索引和用量 |
| Control | 状态、启动、加载验证、卸载、停止自有服务；可透传文字/图片建立依赖 |
| Video Sample | 官方 VIDEO 或 IMAGE + fps/时间戳 → CPU 关键帧与采样 manifest |
| Video Analyze | 分段视觉分析、同租约汇总 → 报告、JSON、证据、覆盖与用量 |

refresh 改变时重新生成，其他相同输入使用 ComfyUI 缓存；Control 每次执行。top_k 范围 1–64，top_p >0；seed 不承诺自适应专家模式下逐字一致。结构化输出是生成后校验，可设 0–2 次修复，格式失败明确报错。

Structured 和 Vision 的非空 JSON Schema 必须显式声明根 `type:object`。支持 Draft 4、7、2019-09、2020-12；不声明草案时按 2020-12 校验，引用仅限当前 Schema 中的本地目标。Vision 留空 Schema 使用普通图片分析，填入 JSON null 或 boolean 不能代替留空。Schema、历史、档案、结构化输出中的重复 JSON 成员均拒绝，不采用后一个值覆盖前一个值。

面板“启动/加载/卸载/停止”都进入 ComfyUI 队列；状态查询直接读取。同卡 Control 完成加载验证后释放资源再返回，HTTP 服务继续待命；非同卡可显式保持载入。原生 STRING 列表使下游逐项执行，STRATA_TEXT_LIST 将整批交给 Batch。

## 工作流示例

把节点包 examples 中的 API JSON 拖入 ComfyUI，替换 Connection 的配置名称和 CheckpointLoader 的 SD1.5 模型名：

- text-to-image.api.json：提示词助手 → CLIP → KSampler → 保存图像。
- image-to-image.api.json：图片反推 → CLIP → KSampler；先把附带 strata-vision-fixture.png 复制到 ComfyUI/input。
- storyboard-to-images.api.json：两镜头分镜 → Batch → 原生 STRING 列表 → 两次采样。
- video-analysis.api.json：官方 Load Video → 有界 CPU 采样 → 按时间轴分析报告。视频文件放入 ComfyUI/input。

视频分析默认 `frames` 模式，每段最多 8 帧，所有片段和总结在同一服务租约中执行，只在整个流程结束后释放。`native` 必须由服务声明视频 contract 1，支持 1–64 个实际采样帧，每段 2–16 帧；服务内部复制奇数段的末帧完成配对，不增加证据帧或虚构时间。单帧不能证明运动方向。反推视频生成提示词选择 `task:prompt`，`report` 输出为英文提示词，完整分析和限制仍在 `analysis_json`。IMAGE + fps 时间为估算，显式 timestamps_json 应严格递增、长度与原帧批次一致。

VIDEO 直接解码原始文件并保留 PTS，按显示旋转后坐标裁切，时间截取按已审阅的官方字段与元数据时长计算。支持已审阅的官方 File（旧 file-only 与当前 file/trim/crop 布局）、Components 和当前 List；无公开裁切 getter 时只读取固定字段。未知实现、布局或缺少时长元数据的文件直接报错，改用 IMAGE 输入、导出有完整元数据的视频或更新适配器。单个输入文件/BytesIO 上限256MiB，CPU 解码120秒、拼接总采样180秒，时间窗最多3600秒。start_s/end_s 相对输入 VIDEO，end_s:0 表示输入末尾；长片可选择后续时间窗。

采样最多64帧，默认589824像素，原始单帧不得超过16Mi像素。分析阶段输出上限4096 tokens，问题最多4KiB、可选转写文字最多8KiB。长报告超出归并预算时明确报错；降低单段输出长度或分时间窗运行。

全程高密度分析使用 **Strata 全程视频分析 / Full Video Analyze**，直接接 `VIDEO` 或原始 `IMAGE` 批次，不要先接旧 Video Sample。`duration_s` 为分析秒数，0 表示整个剩余视频；`start_s` 为输入视频的开始秒；`sampling_fps` 为每秒观察帧数。例如 10 秒、24 帧/秒规划约 240 帧，15 秒约 360 帧；IMAGE 输入的 `fps` 表示原始批次帧率。

新节点逐段在 CPU 上采样和转码，frames 模式每段 2–8 帧，native 每段 2–16 帧；全部观察与汇总复用一次服务租约，结束、失败或取消只清理一次。每次最多 256 段，超出时要求缩短秒数或降低采样率。输出仍为 report、analysis_json、evidence_json、coverage_json、usage_json，证据使用全窗口唯一 ID，并保留源 PTS；coverage 显示实际帧数和最大帧间隔。原始帧率低于目标时不会伪造额外帧。

观察默认使用 temperature:0、reasoning_effort:none，提示词要求只判断当前片段、明确比较首尾帧，不因整片问题而在每段补出颜色变化或反向运动。模型输入只附必要的证据 ID 和时间信息，完整源时间映射仍保留在 evidence_json。提高 temperature 可能增加报告的不稳定性；数值覆盖通过不代表语义识别全部正确。

对足够均匀的画面，另附 CPU 主色 RGB 与前景区域中心统计，用于交叉检查颜色和图像平面位移。该中心是区域统计，不是对象跟踪或身份识别；纹理复杂时不提供这些数值。小于采样像素容差的中心变化不作为方向证据，归一化白边不计入主色统计。分段汇总另附每条报告的实际证据时间范围，避免把片段内颜色不变误说成全片不变。

旧 Video Sample / Video Analyze 的 64 帧限制和默认值保留供既有工作流使用。新流程是高密度分段观察及文字汇总，不能保证识别全部连续运动，音轨仍需另附转写文本。240/288/360 帧的 CPU 与模拟服务验证确认覆盖和流程；另以 SC117 IQ3_S / RTX 4060 Ti 16GB 实测 2 秒、24 FPS、48 帧合成视频，两种模式正确汇总红→蓝与白圆向右。均匀画面的可选 CPU 像素统计用于交叉检查颜色及前景区域位移，不是对象跟踪。真实长片及复杂动作质量尚未验收。

支持后续时间窗：start_s/end_s 的位置上限86400秒，单次窗口仍最多3600秒，fps至少0.001、sampling_fps至少0.01。修正 MKV 非零 PTS 起点的时长、尾部截取与拼接时间轴；透明文件帧和 Components 分离alpha按白背景合成，PQ/HLG HDR文件和非sRGB Components须先转换为SDR/sRGB。不同尺寸采样帧按比例缩小并留白到首帧画布；manifest 的 width/height 对应实际输出，pre_tensor_size与tensor_content_box记录变换。`task:prompt` 最终返回空或纯空白提示词会报错并清理租约，下游不会收到分析报告作为替代提示词。

`analysis_json.analysis` 包含 report、events、shots、prompt、limitations；events 记录 start_s/end_s 和不重复的 evidence_frames。时间坐标相对本次选定窗口；manifest 保留输入时间映射。每次汇总仅能引用该组输入证据帧，事件时间须位于所引用帧的时间范围内，越界时拒绝输出。归并预算包含问题、覆盖信息及帧时间元数据。不同时间窗的报告不能直接视为原片精确镜头边界。可选 transcript 只接文字，不加载ASR权重、也不推断音效或音乐。

Strata 输出文字，绘图由下游模型完成；抽帧图片分析不称为原生视频理解。

## 显存、取消与故障

同步节点先把图片转 CPU 数据，释放 ComfyUI 模型，测量空闲 VRAM/RAM，再载入 Strata。完成、错误或取消后等待请求结束，确认语言/视觉进程退出及显存回收才输出；释放失败中止下游。托管模式仅清理记录的自有 PID/创建时间/路径。

默认空闲门槛 12GiB VRAM、60GiB RAM。本机因其他常驻程序设为 8GiB VRAM 并实测通过；降低门槛须依据实际占用。存活 GPU tensor、第三方后台任务和其他异步分支无法保证释放，发现冲突报错；示例用依赖线保持串行，多 GPU 调度尚未验收。

端口占用时换端口。缺主模型/MTP 时导入完整数据，mmproj 校验失败时重装已校验的 VisionReady 包。Windows 需要 10 1903+ 或 11，内置引擎使用应用 UTF-8 manifest 支持中文安装与模型路径。原生图像协议要求无空格路径，程序选择 Windows 短路径或包内临时目录；无法找到时设置可写 ASCII TEMP/TMP 后重启。

## 版本与更新

节点 1.1.2 新增 Full Video Analyze，修复浮点漏帧、汇总范围与时间；配套运行包 0.1.42-t8.6，359 项 CPU/HTTP 回归通过。协议仍为 1，抽帧模式最低运行包为 0.1.39-t8.14；原生模式需要服务明确声明视频能力。官方 VIDEO 旋转＋裁切＋截取、拼接、Components 与 V3 Schema 已通过 CPU 素材验证，实机内容质量范围见 README。

2026-10-12 在 RTX 4060 Ti 16GB／128GB RAM、SC117 IQ3_S、32K 上下文上验证真实 V3 节点：frames/native 均完成8帧、两个4帧分段和一次汇总，正确识别8秒合成片的颜色变化与圆形移动方向，同流程保持一组进程，最后双进程退出；原有文字、图片及推理中取消清理通过。

真实 ComfyUI 队列完成 native 视频→Extract→下游报告保存及8张采样图输出。同卡模式将官方 ModelPatcher 管理的4MiB CUDA模型卸载回CPU，ComfyUI显存分配归零；Strata最后双进程退出。本轮 external 模式未重测完整扩散绘图、其他后端与真实长片质量。已有主模型、MTP 与视觉权重可继续使用，无需重新下载。

节点使用独立语义版本，运行包仍使用 `上游版本-t8.修订`。两者通过协议 1 通信，节点 ID 保持稳定。运行包退出后用 UPDATE-PORTABLE 自动获取正式版；旧无权重包先更新后用 INSTALL-VISION 切换，模型目录及视觉编码方式/token 设置保留。旧文字配置首次启用视觉可运行 START-HERE.bat --vision gpu，或在托管档案选择 vision:gpu。

节点更新：通过 Manager 或独立仓库 Git 更新（ZIP 用户覆盖节点目录）并重启；本机档案位于独立目录，会保留。已验证 ComfyUI 0.38.0 V3 执行器、前端 1.53.10；旧 API 有适配，其他版本未做完整绘图验收。

[验收](https://github.com/T8mars/Strata-T8/blob/main/docs/VALIDATION-COMFYUI-T8.md) · [权重来源](https://github.com/T8mars/Strata-T8/blob/main/vision/catalog.json)
