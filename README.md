# Strata-T8 for ComfyUI

**By T8star**

将 [Strata](https://github.com/Niko1221/Strata) 的本地语言与视觉推理接入 ComfyUI：提示词生成、结构化分镜、图片描述、视频采样分析与批量工作流。Strata 使用独立进程；同卡模式在推理结束后确认引擎退出，再交给下游绘图节点。

**节点与运行包独立发行。** 本仓库不含模型、Python 或推理引擎；[Strata-T8 整合包](https://github.com/T8mars/Strata-T8) 提供 Python、依赖、CUDA/HIP 引擎，VisionReady 另含视觉权重与配置。

## 安装

Registry 版本审核通过后，在 ComfyUI-Manager 搜索 **Strata-T8**，节点 ID `strata-t8`，Publisher `t8star`。尚未显示时可使用 Release ZIP，或在 `ComfyUI/custom_nodes` 中执行：

```bash
git clone https://github.com/T8mars/Comfyui-Strata-T8.git
```

使用 **ComfyUI 的 Python** 安装本目录 `requirements.txt`，然后重启。仅增加 `jsonschema`；图像与视频处理使用 ComfyUI 已有的 PyTorch、NumPy、Pillow 与 PyAV。无需把 Strata 的 CUDA/Python 依赖装入 ComfyUI。

Release ZIP 解压到 `custom_nodes/Comfyui-Strata-T8`，确保该目录直接包含 `__init__.py`。升级旧节点时保留一个安装目录，移除旧 `comfyui-strata-t8` 副本，避免重复注册。

## 模型与路径

1. 下载 [Strata-T8 VisionReady](https://github.com/T8mars/Strata-T8/releases/latest)，解压为例如 `D:/Strata-T8`。
2. 收到完整模型数据时，把 `Strata-data` 放在例如 `D:/Strata-data`，运行整合包的 `IMPORT-MODEL.bat` 导入。
3. 自行下载时运行 `PREPARE-MODEL.bat`，按 ModelScope → 国内镜像 → Hugging Face 获取缺失文件并核对 SHA256；同时准备专家包、分词器和 MTP。仅复制 GGUF 不能替代完整数据目录。

```text
D:/Strata-T8/                       # 独立运行包
  runtime/python/python.exe
  vision/weights/mmproj-Qwen3.8-Flash-Next-BF16.gguf
D:/Strata-data/                     # 主模型数据，可单独分发
  portable-model.json
  models/IQ3_S/                    # 两个完整 GGUF 分片
  packs/iq3_s/                     # 已准备的专家包与分词器
  mtp/rt/                         # experts.bin、dense.bin、dense.txt
ComfyUI/custom_nodes/Comfyui-Strata-T8/
```

**模型放在 Strata-data，连接档案指向该目录。** ComfyUI 的 `models/checkpoints` 用于下游绘图模型。

| 来源 | 下载 |
| --- | --- |
| ModelScope，优先 | [IQ3_S 与视觉权重](https://modelscope.cn/models/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF) · [MTP 来源](https://modelscope.cn/models/Qwen/Qwen3.8-Flash-Next) |
| 国内镜像 | [IQ3_S 分片](https://hf-mirror.com/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S) |
| Hugging Face | [IQ3_S 分片](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF/tree/main/IQ3_S) · [MTP 来源](https://huggingface.co/Qwen/Qwen3.8-Flash-Next) |

IQ3_S 主模型约 **83.62GB**，MTP 所需张量约 **5.22GB**，另需预处理空间。VisionReady 内置 BF16 视觉编码器 **907,543,008 字节**，SHA256 `b1a82259702816a5330d7bd7607cd9676b11780e79ff7348c21103ff3ce49bd0`。固定版本、分片哈希和许可证见运行包的 [主模型清单](https://github.com/T8mars/Strata-T8/blob/main/model-sources.json) 与 [视觉清单](https://github.com/T8mars/Strata-T8/blob/main/vision/catalog.json)。节点和 Manager 更新不下载模型。

## 连接与使用

打开 ComfyUI 侧栏 **Strata-T8**，保存托管档案，刷新后在 Connection 节点选择名称：

```json
{
  "mode": "managed",
  "runtime": "D:/Strata-T8",
  "data_dir": "D:/Strata-data",
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

托管模式使用 Windows 整合包；Linux ComfyUI 使用 `external` 连接已部署的 Strata 服务。托管档案自动生成本机 API key。连接已启动服务可使用 `mode:external`、`url:http://127.0.0.1:8080`；默认 `allow_lifecycle:false`、`same_gpu:false`。本机同卡外部服务需要显式启用这两项，并使用 Strata-T8 协议 1、单请求配置。远程连接保持 `same_gpu:false`。

档案保存在 `%LOCALAPPDATA%/Strata-T8-ComfyUI`；Linux 默认 `~/.config/Strata-T8-ComfyUI`，可用 `STRATA_COMFY_HOME` 指定。工作流只保存档案名称，API key 使用面板密码框填写；留空保留，勾选“清除已保存 API key”时 external 清空、managed 重新生成。模型安装和运行包配置独立于节点目录。

| 功能 | 节点 |
| --- | --- |
| 文本与提示词 | Text、Prompt：扩写、翻译、改写、正负提示词、历史对话 |
| 结构化工作流 | Structured、Extract、Number：分镜 JSON、Schema 校验、字段提取、STRING 列表 |
| 图片与批量 | Vision、Batch、Image Batch：描述、反推、OCR、问答、顺序批处理 |
| 视频 | Video Sample、Video Analyze：有界采样、概述、时间轴、分镜观察、问答和视频提示词 |
| 全程视频 | Full Video Analyze：直接接收 VIDEO / IMAGE，按秒数与帧率自动规划，逐段采样分析后汇总，总帧数可超过 64 |
| 语音与对白 | Speech Transcribe：VIDEO / AUDIO 转文字、时标 JSON 与 SRT；Video Analyze / Full Video Analyze 可选结合对白证据 |
| 服务控制 | Connection、Control：状态、加载、卸载、停止自有服务及依赖透传 |

Structured 和 Vision 的 JSON Schema 根须显式写 `"type":"object"`；支持声明 Draft 4、7、2019-09 或 2020-12，仅允许本地引用。Vision 的 Schema 留空表示普通图片分析；重复 JSON 字段会报错。旧草案混合 dependencies 的 anchor 组合请改用 JSON Pointer 引用。

导入 [examples](examples) 中三个 API 工作流，替换档案名与 SD1.5 绘图模型；看图示例先把附带 PNG 放入 `ComfyUI/input`。文本 → 绘图、图片反推 → 绘图、两镜头分镜 → 批量绘图均有示例。详细配置与限制见 [使用说明](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/USAGE.md)。

## 视频分析

高密度分析使用 **Strata 全程视频分析 / Full Video Analyze**，直接接 VIDEO 或原始 IMAGE 批次，不先经过旧采样节点。填 `duration_s:10`、`sampling_fps:24` 自动规划约 240 帧，`duration_s:0` 分析整个剩余视频；IMAGE 的 `fps` 填原始批次帧率。frames 每段 2–8 帧、native 每段 2–16 帧，一次最多 256 段，全部共用一次服务租约。输出报告、JSON、证据、覆盖和用量，coverage 记录实际帧数与最大间隔。旧 64 帧工作流继续可用。

`sampling_fps` 只控制采样密度；IMAGE 的 `fps` 决定输入时长，例如 144 帧在 12 FPS 是 12 秒，在 24 FPS 是 6 秒。官方 VIDEO 读取其原时间轴。低帧率或变帧率素材的空时间窗会计入 `completed_windows` / `empty_segments`，不会复用上一帧；有时标对白时仅分析该段对白，禁止添加该段画面、动作或镜头事实。

示例：[全程分段分析](examples/video-full-analysis.api.json)。节点 1.2.0 共提供 14 个节点，保留原有 13 个 ID；更新后重启 ComfyUI 加载。

连接 `Load Video → Strata Video Sample → Strata Video Analyze`，并把采样节点的 `frames`、`manifest_json` 两个输出都接入分析节点。示例为 [video-analysis.api.json](examples/video-analysis.api.json)，将 `YOUR_VIDEO.mp4` 替换为 ComfyUI/input 内的文件。已有视频帧批次可连接 `IMAGE`，填写原始 fps；变帧率素材可提供每帧时间戳 JSON。

默认使用 `frames` 模式，按时间顺序将图片发给已有 Strata 视觉 API；每段最多 8 帧，所有分段与最终汇总共享一次模型加载及卸载。输出报告、结构化分析、证据帧与采样覆盖信息，可连接 Extract 获取具体字段。默认采样 1fps、最多 64 帧、每帧 589824 像素；超过帧数预算时均匀覆盖选定时间窗，并记录采样间隔。单个时间窗最多 3600 秒，VIDEO 输入文件或内存流上限 256MiB，原始单帧最多 16Mi 像素；较大素材先导出较小文件或接入已有 IMAGE 帧批次。

采样在 CPU 上直接解码原始视频，保留容器 PTS，并按官方 VIDEO 的截取范围、显示旋转和裁切顺序处理。支持已验证的官方 `VideoFromFile` 两种对象布局（旧版仅文件、当前文件＋截取＋裁切）、`VideoFromComponents` 及当前 `VideoFromList`。适配器读取已审阅的固定截取、裁切字段，并从有界元数据获取时长；未知对象、布局或缺少时长元数据的文件会明确报错，可改用 IMAGE 输入。旋转、裁切、截取组合及拼接已通过合成视频 CPU 对照测试。

manifest 分开记录片段时间、输入位置与源 PTS；拼接使用合成时间轴并额外保留各组成片段的源 PTS。IMAGE 仅有 fps 时使用估算时间；提供每帧时间戳后保留实际输入时间。采样间隔内的动作可能遗漏，报告不承诺逐帧识别、精确切点或连续运动还原。

透明文件帧和 Components 的分离 alpha 在白背景合成，PQ/HLG HDR 文件和非 sRGB Components 需先转换为 SDR/sRGB。修正 MKV 非零 PTS 起点的时长、尾部截取与拼接时间轴。不同尺寸的采样帧保留比例、白色留边，并在 manifest 记录原尺寸、内容位置与实际张量宽高。支持原片后续短时间窗（位置 0–86400 秒，窗口最多 3600 秒）；`task:prompt` 的最终提示词为空或仅含空白时明确失败，避免把分析报告当作生成提示词。

`native` 模式仅在服务明确声明视频协议能力后可用；未声明能力时直接报错。旧节点支持 1–64 个实际采样帧，Full Video Analyze 按前述段数限制处理更长窗口；原生模式每段 2–16 帧。奇数帧由服务内部复制该段末帧完成时序配对，节点保留真实帧数、时间戳和证据 ID；单帧无法提供运动方向证据。选择 `task:prompt` 可反推英文视频生成提示词，结果同时保留采样限制。模型名称和官方模型卡不能代替本地能力检查。节点不额外下载模型；默认不自动转录音轨，可选 `transcript` 仍为用户提供的无时标文字，报告会与视觉证据区分。

视频采样核心固定复制自 Strata-T8 的 `tools/strata_video.py`，来源与 SHA256 记录在 `video-shared-source.json`。节点运行不依赖本地源码目录，也不启动 ffmpeg 子进程。

### 自动转录与音画联合分析

在 Strata 整合包的模型设置页单独配置语音模型目录，目录直接包含 **Confucius4-R2T2-Q4_K_M.gguf** 和 **mmproj-Confucius4-R2T2-Q8_0.gguf**，两份合计约 1.46 GB；VAD、Python 与原生运行组件由 Strata runtime 管理，节点不安装 vLLM、不下载权重，也不在 ComfyUI 的 Python 内加载 ASR 原生库。模型来源优先 [官方 ModelScope](https://modelscope.cn/models/netease-youdao/Confucius4-R2T2-GGUF/files)，也可从 [官方 Hugging Face](https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF/tree/86ff0251cb9f456b63aeef5f80137f104e22869a) 获取。语音 projector 与 Strata 的视觉 mmproj 是不同文件，不能互换。设备、语言、音轨和声道在 runtime 设置页配置，不将私人路径写入工作流。

**Full Video Analyze 的 `enable_asr` 默认关闭**。勾选后对整个选中范围执行一次转录任务，再将具有原时间戳的完整对白与视觉片段对应；跨片段对白使用同一 cue ID，最终汇总去重，保留原始转录表。默认关闭不加载 ASR；用户显式提供 `timed_transcript` 时即使关闭也可直接融合，无需再转录。原 `transcript` 入口保持无时标辅助文字的旧行为。

| 接法 | 输入与示例 |
| --- | --- |
| 直接分析视频音轨 | `LoadVideo → Full Video Analyze`，设置 `enable_asr:true`；[自动音画分析](examples/video-speech-analysis.api.json) |
| 转录一次后复用 | `LoadVideo → Speech Transcribe → Full Video Analyze.timed_transcript`；同一个 VIDEO 接分析节点，分析节点保持 `enable_asr:false`；[独立转录与复用](examples/video-speech-reuse.api.json) |
| 已有字幕 | 在分析节点的 `timed_transcript` 粘贴 SRT/VTT、参考 Confucius 节点的 `result_json` 或契约 JSON；[无需 ASR 的字幕融合](examples/video-subtitle-analysis.api.json) |
| 已有 IMAGE 帧 | 显式连接匹配的 `AUDIO` 并启用 ASR，或提供时标字幕；IMAGE 自身没有声音。旧 Video Analyze 接收采样帧时需另外接 AUDIO |

示例中的 `YOUR_VIDEO.mp4` 应替换成 `ComfyUI/input` 内的文件，Connection 档案名替换为本机设置。ASR 需要 **Strata-T8 0.1.42-t8.8** 或更新版本提供 `/v1/asr/settings` 与 `/v1/audio/transcribe`。1.2.0 新增 Speech Transcribe，保留原 13 个节点 ID 与原分析节点五项输出顺序；关闭 ASR 时既有文本/视觉功能保持兼容。

SRT/VTT 默认时间对应输入 VIDEO 的时间轴。提供裁切后的字幕可使用 JSON 的 `timeline:"selected_window"`，其时间从本次选择范围的 0 开始；`timeline:"input"` 使用输入时间。显式 AUDIO 的 `audio_offset_s` 表示音频第一个样本位于 VIDEO 输入时间的哪一秒：正值补前置静音，负值裁掉提前部分。官方 VIDEO 的截取、拼接和容器 PTS 独立处理，避免重复加偏移；VideoFromList 有 `complete_audio` 时优先使用其最终音轨。

运行设置中的 ASR `offset_ms` 是转录时标校正，节点在 VIDEO 输入时间轴上应用一次。它与 AUDIO 输入的 `audio_offset_s` 分别用于字幕校时和音轨位置；裁切窗口不会把提前的对白错误裁到临时上传音频的 0 秒。

时标是 **VAD 段级近似时间**，不是字词强制对齐。联合报告引用对白 ID 与帧 ID，区分画面观察、对白文字和推断；不凭转录认定说话人身份、口型同步、背景音乐或音效。低声、配乐、重叠多人语音可能误识别，截断/强制边界保留复核标记。实际 Q4、CPU/CUDA 与真实视频质量以 runtime 的实机验收记录为准，不以 CPU 模拟节点测试代替。

CPU ASR 不主动卸载驻留的 Strata 模型；CUDA 模式要求空闲、单请求服务及 `allow_lifecycle:true`，同卡使用 `same_gpu:true`。托管模式核对自有服务实例；external 档案已有 `instance_id` 时也会核对。节点先交接 ComfyUI 分配，runtime 在自己的服务租约内释放主模型与视觉进程后转录，不能抢占 DSH 的正在生成任务。主模型的 RAM/VRAM 门槛只用于主推理，不用于提前拒绝小型 ASR。视频范围最多 3600 秒，但单次节点上传还受 **64 MiB JSON/base64 请求预算**限制，Float32 PCM 约最多 48 MiB（16k mono 约 13 分钟，高采样率或多声道更短）；超限明确要求缩短范围。转换只复制选中 AUDIO 切片到 CPU；Stop/取消不发后续视觉请求，服务负责终止其自己的 ASR worker。

ASR 实现复用 [T8mars/comfyui-confucius-r2t2-t8](https://github.com/T8mars/comfyui-confucius-r2t2-t8)，感谢 [网易有道 Confucius4-R2T2](https://github.com/netease-youdao/Confucius4-R2T2)、[llama.cpp](https://github.com/ggml-org/llama.cpp) 与 [FireRedVAD](https://github.com/FireRedTeam/FireRedVAD)。ASR 代码、原生运行库、模型及 VAD 分别适用 Apache/MIT/网易有道模型许可，详见 runtime 的来源与许可清单。

## 环境与更新

当前节点 **1.2.0** 配套 Strata-T8 **0.1.42-t8.8**，提供 14 个节点与协议 1。新增默认关闭的语音转录和时标对白融合，修复稀疏源空窗口中断、首尾对白丢失、字幕校正与裁切/拼接时间轴。**419 项 CPU/HTTP 回归通过**；官方 VIDEO / torch CPU 验证实际 12 秒素材的帧率、裁切与拼接。真实 HTTP、Q4 语音模型与 native 视觉链路另完成音画联合验收，[范围与限制](https://github.com/T8mars/Strata-T8/blob/v0.1.42-t8.8/docs/VALIDATION-VIDEO-SPEECH-T8.md)。

RTX 4060 Ti 16GB、128GB RAM、SC117 IQ3_S、32K 上下文另完成 2 秒、24 FPS、48 帧合成视频的实际推理：frames 六段、native 三段都正确汇总约 1 秒处红→蓝及白圆向右、垂直位置不变。首次测试曾出现颜色和方向误判；为均匀背景增加可选 CPU 像素统计后，该案例通过。复杂动作、真实长片及 240/360 帧的 GPU 内容质量尚未验收，采样与文字汇总不能保证连续运动全部识别。

2026-10-12 在 RTX 4060 Ti 16GB、128GB RAM、SC117 IQ3_S、32K 上下文上，通过真实 V3 节点验证两种模式的 8 秒合成视频：8 帧、两个 4 帧分段和一次汇总，均正确识别红→绿→蓝与白圆左→右；各流程只使用一组模型进程，结束后双进程退出。文字、单图和推理中取消清理也通过。

真实 ComfyUI 队列另验证了原生视频分析→字段提取→报告保存及 8 张图片输出，同卡模式卸载官方 ModelPatcher 注册的 4MiB CUDA 模型，模型回到 CPU、ComfyUI 显存分配归零，Strata 完成后双进程退出。此为 external 服务验收；未重测完整扩散绘图、其他后端或真实长视频的识别质量。

节点 **1.0.7** 继续使用协议 1，最低运行包版本为 `0.1.39-t8.14`。修复 Release ZIP 和 Registry 安装后托管服务无法读取最低运行版本的问题；兼容信息集中在随包提供的 `version.json`，打包前核对三个版本文件与运行包兼容字段。**239 项节点与 HTTP 回归**通过，无跳过；[第八组 20 轮记录](docs/AUDIT-20-ROUND8-NODES.md)区分实际 HTTP、安装产物和资源替身验证。主模型、MTP 和视觉权重无需重新下载。

此前节点 **1.0.7** 与 Strata-T8 **0.1.42-t8.1**（上游 [v0.1.42](https://github.com/Niko1221/Strata/releases/tag/v0.1.42)，原生引擎 `0.1.42`）完成文本提示词、图片反推、结构化分镜与顺序批量的实际 384×384 绘图验收，确认语言和视觉进程退出后再采样。[此前验证范围、测量条件与资源限制](https://github.com/T8mars/Strata-T8/blob/main/docs/VALIDATION-UPSTREAM-0142-T8.md)。已有节点 ID 保持兼容，新增视频节点使用 **1.1.0**。

Python 3.10+；此前以运行包 0.1.39-t8.14 完成 ComfyUI 0.38.0 / 前端 1.53.10 / RTX 4060 Ti 16GB / 128GB RAM 的三类实际绘图验收。Windows 整合包需要 Windows 10 1903+/11 x64、AVX2 CPU、兼容显卡驱动，IQ3_S 建议 96GB 以上 RAM。默认同卡门槛为 12GiB 空闲显存、60GiB 空闲 RAM；其他后台进程仍会占用资源。AMD 文本以 Strata 上游支持列表为准，Windows AMD 视觉尚不支持，AMD 档案使用 `vision:no`。

节点使用独立语义版本，通过 Manager 更新后重启 ComfyUI；Git 安装可退出 ComfyUI 后运行 `git pull --ff-only`。运行包启动时自动检查版本，模型设置页提供一键更新，也可使用自己的 `UPDATE-PORTABLE.bat`，上游同步由 [Strata-T8](https://github.com/T8mars/Strata-T8) 维护。节点 ID 保持兼容；本机档案保留在安装目录外。

[第七组 20 轮检查与回归记录](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/AUDIT-20-ROUND7-NODES.md)；[第八组 20 轮检查与回归记录](docs/AUDIT-20-ROUND8-NODES.md)。节点支持当前 ComfyUI V3 API，并保留 V1 适配。

## 来源与致谢

感谢 [Niko1221/Strata](https://github.com/Niko1221/Strata) 的推理引擎与服务、[Qwen](https://huggingface.co/Qwen) 的模型、[ISTA-DASLab](https://huggingface.co/ISTA-DASLab) 的 GSQ-RCO/GGUF 权重，以及 [ComfyUI](https://github.com/Comfy-Org/ComfyUI) 和 [ComfyUI-Manager](https://github.com/Comfy-Org/ComfyUI-Manager) 的节点与分发平台。本项目由 T8 维护，采用 [MIT](LICENSE)；模型与第三方组件遵循各自许可证。

## T8star 与资源

**By T8star** · [整合包仓库](https://github.com/T8mars/Strata-T8) · [ComfyUI 节点仓库](https://github.com/T8mars/Comfyui-Strata-T8)

| 链接 | 地址 |
| --- | --- |
| B站 | [B站](https://space.bilibili.com/385085361) |
| YouTube | [YouTube](https://www.youtube.com/@T8star-Aix/) |
| API | [API](https://api.seedance.nz/sign-up?aff=5f4w) |
| 免费画廊 | [免费画廊](https://www.openzhenzhen.com) |
| 在线 AI 应用 | [在线 AI 应用](https://www.runninghub.ai/zh-cn/user-center/1907375370302308353/userPost?inviteCode=rh-v1121) |
| ComfyUI 整合包 | [ComfyUI 整合包](https://pan.quark.cn/s/264edb7e36bd) |
| Hugging Face | [Hugging Face](https://huggingface.co/t8star) |
