#!/bin/bash

# Скрипт для остановки бота

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/bot.pid" ]; then
    PID=$(cat "$SCRIPT_DIR/bot.pid")
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        # Ждём, пока телеграм отпустит getUpdates: если стартовать новый бот
        # раньше, тот упадёт с Conflict и останется без поллинга
        for _ in $(seq 30); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 0.5
        done
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID"
            echo "Бот с PID $PID не отвечал, прибит принудительно"
        else
            echo "Бот с PID $PID остановлен"
        fi
        rm "$SCRIPT_DIR/bot.pid"
    else
        echo "Процесс с PID $PID не найден"
        rm "$SCRIPT_DIR/bot.pid"
    fi
else
    echo "Файл bot.pid не найден"
fi
