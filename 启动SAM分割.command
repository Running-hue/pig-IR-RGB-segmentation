#!/bin/zsh
cd "$(dirname "$0")" || exit 1
task_python="$PWD/.venv/bin/python"
if [[ ! -x "$task_python" ]]; then
  task_python="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
fi
if [[ ! -x "$task_python" ]]; then
  task_python="$(command -v python3)"
fi
if curl --silent --fail --max-time 2 http://127.0.0.1:8768/api/captures >/dev/null 2>&1; then
  open 'http://127.0.0.1:8768'
  exit 0
fi
"$task_python" app.py --port 8768 --open
if [[ $? -ne 0 ]]; then
  echo '启动失败。依赖及操作方法见 README.md。'
  read -k 1 '?按任意键关闭'
fi
