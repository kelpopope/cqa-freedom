#!/bin/bash
# Запуск CQA «с чистого листа» на macOS: стирает базу (cqa.db) и запускает сервер заново.
cd "$(dirname "$0")" || exit 1
exec "./start-mac.command" --reset
