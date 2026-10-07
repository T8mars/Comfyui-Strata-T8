# Strata-T8 for ComfyUI

将 [Strata](https://github.com/Niko1221/Strata) 的本地语言与视觉推理接入 ComfyUI：提示词生成、结构化分镜、图片描述与批量工作流。Strata 使用独立进程；同卡模式在推理结束后确认引擎退出，再交给下游绘图节点。

**节点与运行包独立发行。** 本仓库不含模型、Python 或推理引擎；[Strata-T8 整合包](https://github.com/T8mars/Strata-T8) 提供 Python、依赖、CUDA/HIP 引擎，VisionReady 另含视觉权重与配置。

## 安装

Registry 版本审核通过后，在 ComfyUI-Manager 搜索 **Strata-T8**，节点 ID `strata-t8`，Publisher `t8star`。尚未显示时可使用 Release ZIP，或在 `ComfyUI/custom_nodes` 中执行：

```bash
git clone https://github.com/T8mars/Comfyui-Strata-T8.git
```

使用 **ComfyUI 的 Python** 安装本目录 `requirements.txt`，然后重启。仅增加 `jsonschema`；图片处理使用 ComfyUI 已有的 PyTorch、NumPy 与 Pillow。无需把 Strata 的 CUDA/Python 依赖装入 ComfyUI。

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
| 服务控制 | Connection、Control：状态、加载、卸载、停止自有服务及依赖透传 |

Structured 和 Vision 的 JSON Schema 根须显式写 `"type":"object"`；支持声明 Draft 4、7、2019-09 或 2020-12，仅允许本地引用。Vision 的 Schema 留空表示普通图片分析；重复 JSON 字段会报错。旧草案混合 dependencies 的 anchor 组合请改用 JSON Pointer 引用。

导入 [examples](examples) 中三个 API 工作流，替换档案名与 SD1.5 绘图模型；看图示例先把附带 PNG 放入 `ComfyUI/input`。文本 → 绘图、图片反推 → 绘图、两镜头分镜 → 批量绘图均有示例。详细配置与限制见 [使用说明](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/USAGE.md)。

## 环境与更新

节点 **1.0.7** 继续使用协议 1，最低运行包版本为 `0.1.39-t8.14`。修复 Release ZIP 和 Registry 安装后托管服务无法读取最低运行版本的问题；兼容信息集中在随包提供的 `version.json`，打包前核对三个版本文件与运行包兼容字段。**239 项节点与 HTTP 回归**通过，无跳过；[第八组 20 轮记录](docs/AUDIT-20-ROUND8-NODES.md)区分实际 HTTP、安装产物和资源替身验证。主模型、MTP 和视觉权重无需重新下载。

此前节点 1.0.6 与 Strata-T8 **0.1.40-t8.2**（上游 [v0.1.40.2](https://github.com/Niko1221/Strata/releases/tag/v0.1.40.2)，原生引擎 `0.1.40.2`）的文本提示词、图片反推、结构化分镜及顺序批量完成实际 384×384 绘图，确认语言和视觉进程退出后再采样。[验证范围与测量条件](https://github.com/T8mars/Strata-T8/blob/main/docs/VALIDATION-UPSTREAM-01402-T8.md)。

Python 3.10+；此前以运行包 0.1.39-t8.14 完成 ComfyUI 0.38.0 / 前端 1.53.10 / RTX 4060 Ti 16GB / 128GB RAM 的三类实际绘图验收。Windows 整合包需要 Windows 10 1903+/11 x64、AVX2 CPU、兼容显卡驱动，IQ3_S 建议 96GB 以上 RAM。默认同卡门槛为 12GiB 空闲显存、60GiB 空闲 RAM；其他后台进程仍会占用资源。AMD 文本以 Strata 上游支持列表为准，Windows AMD 视觉尚不支持，AMD 档案使用 `vision:no`。

节点使用独立语义版本，通过 Manager 更新后重启 ComfyUI；Git 安装可退出 ComfyUI 后运行 `git pull --ff-only`。运行包使用自己的 `UPDATE-PORTABLE.bat`，上游同步由 [Strata-T8](https://github.com/T8mars/Strata-T8) 维护。节点 ID 保持兼容；本机档案保留在安装目录外。

[第七组 20 轮检查与回归记录](https://github.com/T8mars/Comfyui-Strata-T8/blob/main/docs/AUDIT-20-ROUND7-NODES.md)；[第八组 20 轮检查与回归记录](docs/AUDIT-20-ROUND8-NODES.md)。节点支持当前 ComfyUI V3 API，并保留 V1 适配。

## 来源与致谢

感谢 [Niko1221/Strata](https://github.com/Niko1221/Strata) 的推理引擎与服务、[Qwen](https://huggingface.co/Qwen) 的模型、[ISTA-DASLab](https://huggingface.co/ISTA-DASLab) 的 GSQ-RCO/GGUF 权重，以及 [ComfyUI](https://github.com/Comfy-Org/ComfyUI) 和 [ComfyUI-Manager](https://github.com/Comfy-Org/ComfyUI-Manager) 的节点与分发平台。本项目由 T8 维护，采用 [MIT](LICENSE)；模型与第三方组件遵循各自许可证。
