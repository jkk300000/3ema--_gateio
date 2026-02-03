#!/bin/bash
# 프로젝트 루트에서 실행: ./scripts/run.sh
# 가상환경이 있으면 활성화 후 main.py 실행

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

if [ -d "venv" ] && [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

if ! python3 -c "import gate_api" 2>/dev/null; then
    echo "의존성이 설치되지 않았습니다. 먼저 ./scripts/ubuntu_setup.sh 를 실행하세요."
    exit 1
fi

exec python3 main.py
