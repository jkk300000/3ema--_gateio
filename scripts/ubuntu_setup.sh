#!/bin/bash
# Ubuntu 24.04 LTS x64 - EMA 캔들패턴 전략 설치 스크립트
# 사용법: chmod +x scripts/ubuntu_setup.sh && ./scripts/ubuntu_setup.sh

set -e

APP_NAME="3ema-gateio"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME}"
VENV_DIR="$INSTALL_DIR/venv"

echo "=== $APP_NAME 설치 (Ubuntu 24.04 LTS x64) ==="

# 1. 시스템 패키지 업데이트 및 Python 환경 설치
echo "[1/5] 시스템 패키지 및 Python 환경 설치..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-venv python3-pip

# 2. 프로젝트 디렉터리 (현재 디렉터리가 프로젝트 루트라고 가정)
if [ ! -f "main.py" ] || [ ! -f "requirements.txt" ]; then
    echo "오류: main.py 또는 requirements.txt가 없습니다. 프로젝트 루트에서 실행하세요."
    exit 1
fi

PROJECT_ROOT="$(pwd)"
echo "[2/5] 프로젝트 루트: $PROJECT_ROOT"

# 3. 가상환경 생성 및 의존성 설치
echo "[3/5] 가상환경 생성 및 의존성 설치..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. .env 파일 확인
echo "[4/5] .env 설정 확인..."
if [ ! -f ".env" ]; then
    echo ".env 파일이 없습니다. 아래 내용으로 .env를 생성하세요."
    cat << 'ENVEXAMPLE'
# Gate.io API (필수)
GATE_API_KEY=your_api_key
GATE_API_SECRET=your_api_secret

# 선택 (기본값 사용 시 생략 가능)
# GATE_FUTURES_BASE=https://fx-api.gateio.ws/api/v4
# TEST_MODE=False
# TEST_ENTRY_OVERRIDE=False
ENVEXAMPLE
    if [ -n "${GATE_API_KEY}" ] && [ -n "${GATE_API_SECRET}" ]; then
        echo "환경변수 GATE_API_KEY, GATE_API_SECRET이 설정되어 있으면 .env에 반영할 수 있습니다."
    fi
else
    echo ".env 파일이 이미 존재합니다."
fi

# 5. 실행 테스트
echo "[5/5] 실행 테스트 (import만)..."
python3 -c "
import sys
sys.path.insert(0, '.')
import config
from src.engine import TradingEngine
print('OK: import 성공')
"

echo ""
echo "=== 설치 완료 ==="
echo "실행: source venv/bin/activate && python main.py"
echo "또는: ./scripts/run.sh"
deactivate 2>/dev/null || true
