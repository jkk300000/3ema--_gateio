#!/usr/bin/env bash
# Linux/Ubuntu: 프로젝트 루트에서 실행 (어디서 호출해도 프로젝트 루트로 이동)
# 사용법: ./scripts/run.sh

set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

if [ ! -f "main.py" ] || [ ! -f "config.py" ]; then
    echo "오류: main.py 또는 config.py가 없습니다. 프로젝트 루트에서 실행하세요." >&2
    exit 1
fi

if [ -d "venv" ] && [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

if ! python3 -c "import gate_api" 2>/dev/null; then
    echo "의존성이 설치되지 않았습니다. 먼저 ./scripts/ubuntu_setup.sh 를 실행하세요." >&2
    exit 1
fi

exec python3 main.py
