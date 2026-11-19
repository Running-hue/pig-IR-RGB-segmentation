# 模型下载、配置与跨平台部署

本说明对应 2026-11-19 的猪体分割工作台，目标是从 Git 克隆后能够补齐同一套模型并启动。Mac 已有运行验证；Windows 原生与 WSL 尚未实机验证，下面列出部署方式和当前代码限制。本文不包含训练流程：当前项目使用官方预训练权重，没有用本批猪只数据训练或微调。

## 1. Git 仓库包含什么

| 内容 | 处理方式 |
|---|---|
| Python、HTML、JS、测试、启动脚本 | 提交 |
| `README.md`、本说明、依赖清单 | 提交 |
| `model_manifest.json`、`prepare_models.py` | 提交：模型版本、下载地址、文件大小和 SHA256 均在这里 |
| `.models/` | 不提交；首次部署下载，或离线复制 |
| `.venv/`、`.deps/`、`.model-deps/`、缓存 | 不提交；在目标电脑重新安装 |
| `.state/` | 不提交；保存的是本机目录选择和个人进度状态 |
| 原始猪只数据、分割输出和筛选导出 | 单独备份，不能指望克隆代码恢复数据 |

`.gitignore` 已忽略模型、依赖目录、虚拟环境和 macOS 缓存文件。忽略规则不会取消已经跟踪的文件：如果之前误提交了权重或数据，需要单独从 Git 索引移除；不要因此删除本地模型或成果。`.DS_Store` 若已跟踪，也应另行清理索引。

提交这次补充文件时，在本项目目录检查：

```sh
git status --short
git diff --stat
git check-ignore .models/sam2.1_hiera_base_plus.pt
```

确认后追加一次普通提交即可，无需修改已有提交历史。此说明不要求上传模型，也没有替你执行 commit 或 push。

## 2. 模型与官方来源

| 项目 | 固定值 |
|---|---|
| 模型名称 | Meta SAM 2.1 Hiera Base Plus |
| 官方仓库 | <https://github.com/facebookresearch/sam2> |
| 官方代码提交 | `2b90b9f5ceec907a1c18123530e92e794ad901a4` |
| 配置名 | `configs/sam2.1/sam2.1_hiera_b+.yaml` |
| 配置本地位置 | `.models/sam2/sam2/configs/sam2.1/sam2.1_hiera_b+.yaml` |
| 配置 SHA256 | `37d6c56b07a7f8d08baaa314315c60dc3aabe2edc66cd92bac6d1ed50038e788` |
| 权重本地位置 | `.models/sam2.1_hiera_base_plus.pt` |
| 权重大小 | `323606802` 字节，约 `308.62 MiB` |
| 权重 SHA256 | `a2345aede8715ab1d5d31b4a509fb160c5a4af1970f199d9054ccfb746c004c5` |

官方权重直链：

```text
https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_base_plus.pt
```

固定版本的官方配置与安装说明：

- [SAM 2.1 Base Plus 配置](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/sam2/configs/sam2.1/sam2.1_hiera_b%2B.yaml)
- [官方安装说明](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/INSTALL.md)
- [官方模型和许可说明](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/README.md)

大小和 SHA256 来自本项目已经使用的本地基线；清单中逐项记录官方源码快照的校验值。清单内 42 个源码及说明文件已在临时目录从固定提交实际下载并逐项校验；权重直链与文件大小已核对，下载测试复用了校验通过的本地权重，没有重复下载整个权重文件。官方 README 声明其 SAM 2 权重和代码采用 Apache 2.0；下载时保留上游 `LICENSE` 与文件头声明。本项目没有据此替第三方依赖或用户数据另行授予许可。

`prepare_models.py` 下载清单列出的源码快照，不是完整训练仓库。程序在运行时把该源码目录加入 Python 搜索路径，因此不需要对这个精简目录执行 `pip install -e .`，也不需要编译 CUDA 扩展。不要只下载 `.pt`：缺少配套代码、配置或 Python 依赖同样无法推理。

## 3. 首次安装：Mac

以下命令在项目根目录（能看到 `app.py` 的目录）执行。示例使用 Python 3.11；原开发机已验证 Python 3.12.14。需要先自行安装对应 Python 和 pip。

```sh
cd "/你的代码位置/segmentation_workbench"
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-sam.txt
.venv/bin/python prepare_models.py
.venv/bin/python prepare_models.py --check
.venv/bin/python app.py --port 8768 --open
```

首次准备好之后，可双击 `启动SAM分割.command`；它优先使用本项目 `.venv/bin/python`。原开发机保留 Codex Python 的兼容回退，新电脑不需要安装 Codex。若启动脚本没有执行权限，在项目目录执行：

```sh
chmod +x 启动SAM分割.command 启动分割.command
```

Apple Silicon 和 Intel Mac 的 PyTorch 可用安装包可能不同。本文没有验证 Intel Mac 的新环境；如果对应平台无法安装要求版本的 PyTorch，需要另行确认支持的环境，不能仅凭已有 `.command` 保证可运行。

## 4. 首次安装：Windows 原生

安装 64 位 Python 3.11，并确保 Python Launcher 的 `py` 命令可用。在 PowerShell 中进入项目目录：

```powershell
cd "D:\你的代码位置\segmentation_workbench"
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-sam.txt
.\.venv\Scripts\python.exe prepare_models.py
.\.venv\Scripts\python.exe prepare_models.py --check
.\.venv\Scripts\python.exe app.py --port 8768 --open
```

无需激活虚拟环境，以上命令直接调用虚拟环境解释器。首次安装后可双击 `启动SAM分割.bat`；该脚本要求 `.venv` 已准备好，不会自动安装依赖或下载模型。Windows 脚本和整个 Windows 原生流程尚未实机验证。

当前系统目录窗口仅在 macOS 启用。Windows 会使用网页内的目录浏览器，也可直接填 `D:\数据\itof` 等完整路径；导出位置仍可在前端选择。

当前代码自动选择设备的逻辑是“有可用 MPS 则用 MPS，否则用 CPU”。所以 Windows 即使有 NVIDIA 显卡，当前前端仍会走 CPU；安装 CUDA 版 PyTorch 本身不会让当前页面自动启用 CUDA。项目尚未提供前端或启动参数来选择 CUDA。

上游官方安装说明推荐 Windows 使用 WSL + Ubuntu；如果原生依赖安装失败，可在 WSL 中重新创建 Linux 虚拟环境后使用第 3 节同类命令（按实际版本调用 `python3`）。Windows 磁盘路径在 WSL 中应使用 `/mnt/d/...`。这条路径也未实机验证，且当前项目的自动设备逻辑在 WSL 仍会选择 CPU。

## 5. 依赖和已验证的环境记录

- `requirements.txt`：NumPy、Pillow、OpenCV，仅基础图像功能。
- `requirements-sam.txt`：包含基础依赖，额外安装 PyTorch、torchvision、Hydra、iopath、tqdm。
- 依赖文件使用版本范围，不是全平台锁文件。模型代码和权重已固定；pip 在不同时期解析的 Python 包可能不同，数值结果也不保证逐位相同。

原开发机 2026-11-18 已安装版本，仅作为排查和复现实验记录：

```text
Python 3.12.14
torch 2.10.0
torchvision 0.25.0
numpy 2.5.5
Pillow 13.0.0
opencv-python-headless 5.1.0.95
hydra-core 1.3.8
omegaconf 2.3.1
iopath 0.1.10
tqdm 4.70.1
portalocker 4.4.2
```

原开发机历史依赖分布在 `.deps/`、`.model-deps/` 和原解释器环境。新电脑应使用干净 `.venv`，不要复制这些本地依赖目录；它们可能含平台专用的动态库，而且代码会优先搜索这些历史目录。PyTorch 与 torchvision 应成对安装；如包安装失败，先参考 [PyTorch 官方安装页面](https://pytorch.org/get-started/locally/) 和固定提交的 SAM 安装说明，不要把不匹配的版本混装。

若需要记录某次实际实验的完整环境，在使用该解释器的终端中另存 `python -m pip freeze` 输出，并连同实际设备、操作系统、模型清单和成果元数据一并归档。原开发机这种分目录依赖环境只执行系统解释器的 `pip freeze`，可能漏掉通过搜索路径加载的包。

## 6. 下载、修复、离线复制与手工准备

`prepare_models.py` 只使用 Python 标准库；即使还没安装 PyTorch，也能先准备模型。Mac 示例（Windows 将解释器换成 `.\.venv\Scripts\python.exe`）：

```sh
.venv/bin/python prepare_models.py          # 下载缺失项；已有且校验通过的文件直接复用
.venv/bin/python prepare_models.py --check  # 仅离线核验，不联网，不修改文件
.venv/bin/python prepare_models.py --repair # 下载缺失项并替换校验不符的模型文件
```

下载完成后才替换目标文件；中断或校验失败不会用残缺权重覆盖原文件。网络超时后可重试，已完整下载的文件会跳过；单个 `.pt` 文件不支持按字节断点续传。`--repair` 之前先备份自己修改过的模型代码。脚本只处理 `.models` 下的模型文件，不读取原始猪只数据、分割成果或 `.state`。

模型文件准备后的主要层级：

```text
segmentation_workbench/
├── model_manifest.json                     # 提交到 Git
├── prepare_models.py                       # 提交到 Git
├── requirements-sam.txt                    # 提交到 Git
└── .models/                                # 不提交到 Git
    ├── sam2.1_hiera_base_plus.pt
    └── sam2/
        ├── SOURCE.json                     # 脚本生成，兼容已有批处理来源记录
        ├── LICENSE
        └── sam2/
            ├── build_sam.py
            ├── sam2_image_predictor.py
            ├── modeling/...
            └── configs/sam2.1/sam2.1_hiera_b+.yaml
```

离线电脑：在能联网的机器运行下载脚本，再把整个 `.models/` 复制到离线机器的项目目录，运行 `--check`。Python 依赖也必须提前在目标系统安装，或准备对应系统、架构和 Python 版本的安装包；复制 Mac 虚拟环境不能替代 Windows 安装。

手工下载时，可从第 2 节直链下载权重放在指定位置；官方代码应检出相同提交，例如在 `.models/sam2` 尚不存在时：

```sh
git clone https://github.com/facebookresearch/sam2.git .models/sam2
git -C .models/sam2 checkout 2b90b9f5ceec907a1c18123530e92e794ad901a4
```

再运行 `prepare_models.py` 补齐并生成来源记录，最后 `--check`。自动脚本直接访问 GitHub 固定版本文件，无需安装 Git。手工复制权重后也必须核对完整 SHA256；同名但不同版本的文件不能混用。

## 7. 当前推理配置与原理

| 项目 | 当前行为 |
|---|---|
| 模型配置 | 官方 Base Plus YAML，`image_size: 1024`、`compile_image_encoder: False` |
| 模型输入 | 新建任务可选红外或 RGB；红外为增强灰度复制三通道，RGB 为按实际标定重映射到已保存对齐深度网格的彩色通道 |
| 红外增强 | 有效像素 1% 与 99.5% 分位拉伸，幂指数 `0.65`，见 `core.normalize_ir` |
| 深度作用 | 不直接输入 SAM；红外候选使用原始深度；RGB 分割先校正彩色图，统计使用对应 `depth_aligned_to_color_mm.npy`，缺少则不计算；深度数组不重采样、不重复变换 |
| 批处理提示 | 临时计算 GrabCut 候选，距离变换选内部前景点；RGB 候选从彩色图缩小计算再恢复尺寸，失败时使用默认点／框；不依赖旧成果目录 |
| 小结果重试 | SAM 面积小于候选面积的 `35%` 时，使用左右扩 `40 px`、上下扩 `35 px` 的框重试；这不是准确率阈值 |
| 人工交互 | 前景／背景点或框；运行 SAM 后先更新画布，点击保存才写入修正 |
| 推理设置 | `torch.inference_mode()`；CPU 线程数 `4`；`multimask_output=False` |
| 官方默认稳定性回退 | `build_sam2` 默认启用，delta `0.05`、threshold `0.98`；因此不能把“请求单结果”理解为关闭官方内部候选回退 |
| 孔洞／小碎片处理 | `SAM2ImagePredictor` 默认 `max_hole_area=0`、`max_sprinkle_area=0`，本项目未主动启用其 CUDA 连通域填补 |
| 项目连通域处理 | 保留命中最多前景点的区域，并以面积作后续比较；不额外腐蚀或平滑猪体边界 |
| 保存坐标 | 红外为原始深度网格，RGB 为保存的对齐深度输出网格；单通道 PNG，背景 `0`、前景 `255`，不同模式不能直接复用掩码 |

代码入口为 `sam_backend.py`，浏览器批处理使用 `sam_batch.segment_frame`。旧命令行 `sam_batch.py --baseline ...` 是历史比较入口，要求已有 GrabCut 初稿；日常浏览器任务不需要 `--baseline`。

SAM 输出是可见目标猪初稿，不保证自动完成躯干解剖学边界。头、耳、尾、腿、遮挡和出画仍需复核。内部模型质量分数不是本数据上的 IoU、Dice 或准确率；未有人工真值前不能把它写成实测效果。不同设备或依赖版本可能产生细微差异。

## 8. 数据、成果与迁移

原始采集应保持猪只／采集层级。红外模式每次采集至少有 `infrared.npy`、`infrared.png`、`depth_mm.npy`；红外和深度必须是同尺寸二维数组。RGB 模式需要 `color.png` 与 `reconstruction.json` 中实际彩色流及对齐输出 profile、配对帧身份和单位比例，不能用相同尺寸代替几何核对。可选 `depth_aligned_to_color_mm.npy` 必须与其保存时的输出 profile 一致。没有对齐深度仍可校正图像并分割、修正及筛选，但不能生成点云；不能用原始深度缩放替代配准。辅助参考图存在时显示，不要求 RGB 模式必须带红外文件。深度预览由对应数组生成。

成果的 `dataset.json` 与逐帧 `segmentation.json` 记录 `input_mode`（`infrared`／`rgb`）；旧版无该字段时按红外处理。逐帧记录还包含 `coordinate_space`、`depth_file`。同一批数据两种模式的成果需保存在独立目录，重新打开和筛选导出会保留其模式。RGB 模式保存校正后的 `color_display.png`，红外模式保存 `infrared_display.png`。每帧新增 `geometry.json` 与 `image_valid_mask.png`，筛选导出一并保留。新 RGB 清单的 `geometry_version` 为 `rgb-aligned-depth-v1`；缺少该版本的旧 RGB 成果按未校正处理，只允许查看、筛选和导出，需新建 RGB 任务才能获得可用于点云的掩码。旧红外继续使用原始网格，缺少重建标定时不会标记 `pointcloud_ready`。

1. 启动后，在前端选择原始目录和分割成果保存位置；新任务使用新的成果文件夹名。
2. 打开旧成果时，选择含 `dataset.json` 的根目录，不选某头猪或单次采集目录。
3. 换电脑后原始路径变化，在“原始目录已移动”处选择新根目录，保持相对层级。无需手工改每帧 JSON。
4. `segmentation.json` 保存逐帧当前状态、筛选结论、备注及版本；`revisions/` 保留修订。原始数据与整个成果目录都应备份。
5. “保留”与“掩码已确认”独立；测量等后续流程应检查 `reviewed=true` 并读取对应的 `trunk_mask.png`。
6. “导出筛选成果”复制选中采集的分割文件和记录，不复制原始红外、深度、RGB。迁移导出成果时也要带上原始数据，或保证原始目录可访问。

关闭浏览器不会停止后台分割，但不能关闭本机服务。服务中断后已完成成果保留，未完成任务不能当作完整批次使用。当前绑定 `127.0.0.1`，用于本机浏览器访问；它不是已配置好的公网或多人共享服务。

### 点云几何约定与数据完整性

- RGB 使用 `reconstruction.json` 的实际彩色流 profile 作为输入，`alignment.output_depth_frame.profile` 作为输出。当前采集记录的 `TargetDistortion=0`，输出无畸变，所以必须先去畸变 RGB；不使用任意新内参，不裁剪，深度数组原值不动。
- 红外保持原始网格，并核对实际红外／深度内参、畸变及二者到彩色相机的外参是否一致。反投影根据对应深度 profile 逆畸变，不对有畸变的数据直接使用针孔射线。
- 点云函数为 `geometry.backproject_mask`，返回 XYZ 毫米和像素 UV。优先读取对应 sensor code，乘该帧实际 `value_scale_mm_per_code`，否则使用毫米数组；排除无效图像区域和零／无效深度。
- 已对齐深度的值按目标相机 Z 使用，RGB 输出位于彩色相机坐标系，不再应用一次深度到彩色外参。红外输出位于深度相机坐标系，若后续合并两类点云，必须明确统一坐标系。
- `geometry.json` 包含 K、畸变模型／系数、坐标系、深度来源、单位比例、有效区域与源文件哈希。点云接口会重新核对来源；标定或深度改变后拒绝复用旧几何成果。迁移时保留每帧 `reconstruction.json`、原始图像、对应深度及 sensor code（若有），不能只带掩码。

当前未增加前端点云生成按钮。这次补齐分割到点云的几何基础，后续可直接接入已检查的反投影接口。数值投影回环测试验证代码使用了相符的模型，不等于验证相机标定的物理精度或猪体分割准确率。

几何依据可参照 [OpenCV 重映射接口](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html) 与 [Orbbec Align 实现](https://github.com/orbbec/OrbbecSDK_v2/blob/main/src/filter/publicfilters/AlignImpl.cpp)；本项目以采集时保存的实际输出 profile 为准，不用 SDK 默认值猜测几何。

## 9. 启动与检查

启动后访问 `http://127.0.0.1:8768`。浏览器没有自动打开时手动输入地址；端口已被工作台占用时使用已有页面。需要自选端口可执行 `app.py --port 其他端口 --open`，此时不要再使用固定为 8768 的双击脚本访问该实例。

Mac 自检（Windows 替换解释器路径）：

```sh
.venv/bin/python prepare_models.py --check
.venv/bin/python -c "from sam_backend import ready, SAMPredictor; print('模型文件:', ready()); print('实际设备:', SAMPredictor().device)"
.venv/bin/python -m unittest discover -s tests -v
```

第二条会实际加载模型，耗时与内存占用取决于电脑；文件校验通过仅代表下载内容完整，不代表依赖已经安装或设备已经验证。首次部署再在浏览器用少量采集测试分割、保存、导出和重新打开，确认后再跑全量数据。原有 Playwright 测试包含开发机特定路径，不作为新电脑直接可运行的通用安装检查。

| 现象 | 检查方向 |
|---|---|
| 提示 SAM 权重或代码缺失 | 先运行下载脚本，再运行 `--check` |
| `No module named torch/hydra/cv2` | 用启动服务的同一解释器安装 `requirements-sam.txt` |
| 模型配置找不到／权重不匹配 | 核对固定代码提交、配置名和模型 SHA256，勿混入其他 SAM 版本 |
| GitHub／权重服务器连接失败 | 检查网络、代理或换联网机器下载后离线复制；不要关闭 HTTPS 校验 |
| 换电脑后提示原始目录不存在 | 在打开成果表单选择移动后的原始根目录 |
| Windows 有 GPU 但仍慢 | 当前自动逻辑使用 CPU，尚未接入 CUDA 自动选择 |
| macOS 系统目录窗口打不开 | 可直接输入完整目录路径；检查系统脚本是否正常运行 |
| 导出提示目录已有文件 | 改用新的导出文件夹名，避免覆盖现有成果 |

可复现的内容包括固定模型文件与本项目代码逻辑；Windows 环境支持、不同硬件速度和最终猪体分割质量，需要在对应电脑和人工标注数据上实际验证。
