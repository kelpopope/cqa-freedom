#!/bin/bash
# Запуск CQA на macOS: двойной клик по этому файлу. Остановить — закрыть окно Терминала или Ctrl+C.
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[CQA] Нужен Python 3.11+. Установите с https://www.python.org/downloads/ и запустите снова."
  read -n 1 -s -r -p "Нажмите любую клавишу…"; echo; exit 1
fi
"$PY" tools/run.py "$@"
status=$?
if [ $status -ne 0 ]; then read -n 1 -s -r -p "Нажмите любую клавишу, чтобы закрыть…"; echo; fi
exit $status
