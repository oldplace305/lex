#!/bin/bash
# Lex 停止スクリプト
pkill -f "sakana-bot/venv/bin/python -m bot.main" && echo "Bot停止しました" || echo "Botは起動していません"
