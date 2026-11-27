# 猪体分割与复核工作台

在浏览器中选择采集目录、分割图像类型和成果位置，用 SAM 2.1 生成猪体掩码。分割完成后可复核、修正和筛选，记录头侧方向，并导出掩码及 CSV。红外和 RGB 成果分别记录对应的深度坐标信息，供后续点云处理。

首次使用或换电脑时，按部署说明安装 Python 依赖并准备模型。安装后，Mac 双击 `启动SAM分割.command`，Windows 双击 `启动SAM分割.bat`。工作台地址为 `http://127.0.0.1:8768`。需要指定端口时可从项目根目录运行 `python3 app.py --port 8768 --open`。

- 安装、模型下载与校验见 [DEPLOYMENT.md](docs/DEPLOYMENT.md)
- 分割、复核和导出操作见 [USAGE.md](docs/USAGE.md)
- 目录分工、模块关系与数据流见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 按日期记录的代码变更见 [CHANGELOG.md](docs/CHANGELOG.md)
- Python 测试和浏览器验证见 [TESTING.md](docs/TESTING.md)

| 目录 | 内容 |
| --- | --- |
| `frontend/` | 页面、样式、画布编辑和浏览器工作流 |
| `backend/` | 本机服务、批量任务、SAM 推理、坐标校验和成果存取 |
| `scripts/` | 模型下载校验与 GrabCut 批处理命令 |
| `config/` | Python 依赖和固定版本的模型清单 |
| `tests/` | Python 测试、浏览器测试与共用测试数据生成代码 |
| `docs/` | 架构、代码变更、部署、使用与测试说明 |

`.models/` 存放模型权重和源码快照，`.state/settings.json` 保存本机上次选择的路径。采集、分割及导出数据由使用者选择目录保存。
