#!/usr/bin/env bash
# Linux systemd 서비스 등록 (선택 사항)
# 사용법: sudo ./scripts/install_service.sh
# 서비스 이름: ema-gateio.service

set -e

SERVICE_NAME="ema-gateio"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_ROOT/venv/bin/python3"
MAIN_PY="$PROJECT_ROOT/main.py"

if [ ! -f "$MAIN_PY" ]; then
    echo "오류: main.py를 찾을 수 없습니다. ($MAIN_PY)"
    exit 1
fi

if [ ! -d "$PROJECT_ROOT/venv" ]; then
    echo "오류: venv가 없습니다. 먼저 ./scripts/ubuntu_setup.sh 를 실행하세요."
    exit 1
fi

# 실행 사용자 (현재 사용자로 실행되도록)
RUN_USER="${SUDO_USER:-$USER}"
RUN_HOME=$(getent passwd "$RUN_USER" | cut -d: -f6)

cat << EOF
서비스 설정:
  프로젝트 경로: $PROJECT_ROOT
  Python: $VENV_PYTHON
  실행 사용자: $RUN_USER
EOF

SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
sudo tee "$SERVICE_FILE" << SERVICEEOF
[Unit]
Description=EMA Candle Pattern Strategy (Gate.io BTCUSDT 30m)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_ROOT
Environment=PATH=$PROJECT_ROOT/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$VENV_PYTHON $MAIN_PY
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=multi-user.target
SERVICEEOF

echo ""
echo "서비스 파일 생성됨: $SERVICE_FILE"
echo ""
echo "명령어:"
echo "  서비스 시작:   sudo systemctl start $SERVICE_NAME"
echo "  서비스 중지:   sudo systemctl stop $SERVICE_NAME"
echo "  서비스 상태:   sudo systemctl status $SERVICE_NAME"
echo "  부팅 시 실행:  sudo systemctl enable $SERVICE_NAME"
echo "  로그 보기:     journalctl -u $SERVICE_NAME -f"
echo ""
sudo systemctl daemon-reload
echo "daemon-reload 완료. 위 명령으로 서비스를 시작하세요."
