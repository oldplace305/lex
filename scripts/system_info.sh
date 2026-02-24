#!/bin/bash
# システム情報を表示するスクリプト
echo "=== Mac mini システム情報 ==="
echo "ホスト名: $(hostname)"
echo "稼働時間: $(uptime | sed 's/.*up //' | sed 's/,.*//')"
echo "メモリ使用: $(memory_pressure 2>/dev/null | head -1 || echo '取得不可')"
echo "ディスク使用: $(df -h / | tail -1 | awk '{print $3 " / " $2 " (" $5 ")"}')"
echo "Python: $(python3 --version)"
echo "Node: $(node --version)"
echo "=========================="
