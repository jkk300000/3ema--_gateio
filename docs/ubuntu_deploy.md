# Ubuntu 24.04 LTS x64 배포 가이드

## 1. 프로젝트 올리기

```bash
# 방법 A: Git으로 클론
git clone <저장소_URL> 3ema-gateio
cd 3ema-gateio

# 방법 B: 파일 직접 복사 (로컬에서)
# scp -r ./3ema전략_gateio/* user@서버IP:~/3ema-gateio/
# ssh user@서버IP "cd ~/3ema-gateio && ..."
```

## 2. 설치 스크립트 실행

```bash
cd ~/3ema-gateio   # 프로젝트 루트로 이동

chmod +x scripts/ubuntu_setup.sh scripts/run.sh scripts/install_service.sh
./scripts/ubuntu_setup.sh
```

## 3. .env 설정

```bash
# .env 파일 생성
nano .env
```

필수 내용:

```
GATE_API_KEY=your_api_key
GATE_API_SECRET=your_api_secret
```

## 4. 실행

### 포그라운드 (터미널에서 직접)

```bash
source venv/bin/activate
python main.py
```

또는

```bash
./scripts/run.sh
```

### 백그라운드 (nohup)

```bash
nohup ./scripts/run.sh > run.log 2>&1 &
tail -f run.log
```

### systemd 서비스 (재부팅 후 자동 실행)

```bash
sudo ./scripts/install_service.sh
sudo systemctl start ema-gateio
sudo systemctl enable ema-gateio   # 부팅 시 자동 시작
journalctl -u ema-gateio -f       # 로그 보기
```

## 5. 유용한 명령어

| 목적 | 명령어 |
|------|--------|
| 서비스 시작 | `sudo systemctl start ema-gateio` |
| 서비스 중지 | `sudo systemctl stop ema-gateio` |
| 서비스 상태 | `sudo systemctl status ema-gateio` |
| 로그 실시간 | `journalctl -u ema-gateio -f` |
| 로그 최근 100줄 | `journalctl -u ema-gateio -n 100` |

## 6. 수동 설치 (스크립트 없이)

```bash
# Python 환경
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip

# 가상환경
cd ~/3ema-gateio
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 실행
python main.py
```
