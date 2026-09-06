#!/bin/bash

# Скрипт для запуска бота в фоне

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Активируем виртуальное окружение
source .venv/bin/activate

# Запускаем бота в фоне с логированием
nohup python bot.py >> bot.log 2>&1 &

# Сохраняем PID процесса
echo $! > bot.pid

echo "Бот запущен с PID: $(cat bot.pid)"
echo "Логи в: $SCRIPT_DIR/bot.log"
