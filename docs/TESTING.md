# 测试说明

在项目根目录运行以下命令。把示例中的 `python3` 换成部署工作台时使用的 Python 解释器即可。

```sh
python3 -m unittest discover -s tests -t . -v
python3 -m scripts.prepare_models --check
```

Python 测试覆盖 RGB／红外坐标、掩码修订、失败帧复核、模型清单、原生目录选择、任务与导出。模型校验命令独立运行，读取本机已准备的模型文件。

浏览器测试使用 Node.js、Playwright 和 Chrome。现有脚本的默认配置使用 Mac 开发机上的浏览器及 Playwright，`browser_input_modes.cjs` 支持使用本地安装的 Playwright 和 `CHROME_PATH` 指定的浏览器。先在一个终端启动测试服务，再将它输出的临时 `root` 路径传给另一个终端的 `.cjs` 脚本。

| 测试服务 | 浏览器脚本 | 检查范围 |
| --- | --- | --- |
| `python3 -m tests.browser.browser_input_modes_fixture` | `node tests/browser/browser_input_modes.cjs '<root>'` | 红外和 RGB 分割、SAM 操作、保存和再次打开 |
| `python3 -m tests.browser.browser_failed_review_fixture` | `node tests/browser/browser_failed_review.cjs '<root>'` | 失败帧复核、恢复分割和记录导出 |
| `python3 -m tests.browser.browser_review_fixture` | `node tests/browser/browser_review.cjs '<root>'` | 多相机筛选、自动切换、页面尺寸和失败历史 |
| `python3 -m tests.browser.browser_fixture --capture '<单帧采集目录>'` | `node tests/browser/browser_workflow.cjs '<root>'` | 真实 SAM 红外分割、保存、CSV 和再次打开 |
| `python3 -m tests.browser.browser_export_fixture` | `node tests/browser/browser_export.cjs '<root>'` | 导出范围、导出内容和再次打开 |

测试服务使用临时目录，运行完成后用 `Ctrl+C` 退出服务。截图写入 `tests/artifacts/`。

已打开成果的本机工作台可运行只读页面检查。该检查拦截写入请求，并验证当前目录的页面布局、CSV 和失败帧预览。

```sh
node tests/browser/browser_layout.cjs http://127.0.0.1:8768
```
