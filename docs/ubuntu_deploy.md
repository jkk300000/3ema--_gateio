# Ubuntu / Linux 배포 가이드

이 문서는 **Ubuntu 24.04 LTS** 및 일반 **Debian/Linux**에서 EMA 캔들패턴 전략을 실행하는 방법을 안내합니다.

**프로젝트 경로:** `~/3ema-gateio` (일반 사용자) 또는 `/root/3ema-gateio` (root 사용자). 아래 예시는 `~/3ema-gateio` 기준입니다.

---

## 0. Linux에서 빠르게 실행하기

```bash
# 프로젝트 루트로 이동 (경로: ~/3ema-gateio 또는 /root/3ema-gateio)
cd ~/3ema-gateio

# 실행 권한 부여 (한 번만)
chmod +x scripts/ubuntu_setup.sh scripts/run.sh scripts/install_service.sh

# 설치 (가상환경 + 의존성)
./scripts/ubuntu_setup.sh

# .env 설정 후 실행
./scripts/run.sh
```

스크립트가 Windows에서 편집된 경우 줄바꿈(CRLF) 때문에 `bad interpreter` 오류가 날 수 있습니다.  
저장소에 `.gitattributes`가 있으면 클론 시 `*.sh`는 LF로 받아집니다. 이미 CRLF로 되어 있다면:

```bash
sudo apt-get install -y dos2unix
dos2unix scripts/*.sh
```

---

## 1. GitHub 리포지토리를 Ubuntu로 옮기기

### Git이 없으면 설치

```bash
sudo apt-get update
sudo apt-get install -y git
```

### GitHub에서 클론

```bash
# HTTPS (저장소 URL 그대로 사용)
git clone https://github.com/사용자명/리포지토리명.git 3ema-gateio
cd 3ema-gateio
```

```bash
# 또는 SSH (키 설정된 경우)
git clone git@github.com:사용자명/리포지토리명.git 3ema-gateio
cd 3ema-gateio
```

**예시 (리포지토리 URL이 https://github.com/myuser/3ema-gateio 인 경우):**
```bash
git clone https://github.com/myuser/3ema-gateio.git 3ema-gateio
cd 3ema-gateio
```

### 나중에 최신 코드 받기

```bash
cd ~/3ema-gateio
git pull origin main
# 또는 기본 브랜치가 master면: git pull origin master
```

---

### GitHub 인증 오류 시 (Invalid username or token / Password authentication not supported)

GitHub는 **비밀번호 로그인을 지원하지 않습니다.** 아래 둘 중 하나로 설정하세요.

#### 방법 1: Personal Access Token (HTTPS용)

1. GitHub 웹: **Settings → Developer settings → Personal access tokens → Tokens (classic)**  
   또는 바로: https://github.com/settings/tokens
2. **Generate new token (classic)** 클릭
3. Note: `ubuntu-deploy` 등 원하는 이름
4. Expiration: 90 days 또는 No expiration
5. Scope: **repo** 체크
6. **Generate token** 클릭 후 **토큰을 복사** (한 번만 보여짐)

**클론/풀 시 사용:**

```bash
# 클론 시 (비밀번호 자리에 토큰 입력)
git clone https://github.com/사용자명/리포지토리명.git 3ema-gateio
# Username: 본인 GitHub 아이디
# Password: [복사한 Personal Access Token 붙여넣기]

# 한 번 저장해 두려면 (다음부터 비밀번호 안 물어뜀)
git config --global credential.helper store
# 위처럼 한 번 토큰 입력하면 ~/.git-credentials 에 저장됨
```

#### 방법 2: SSH 키 (추천, 토큰 입력 불필요)

```bash
# 1. SSH 키 생성 (이메일은 본인 GitHub 이메일)
ssh-keygen -t ed25519 -C "your_email@example.com" -f ~/.ssh/id_ed25519 -N ""

# 2. 공개키 출력 (이걸 GitHub에 등록)
cat ~/.ssh/id_ed25519.pub
```

3. GitHub 웹: **Settings → SSH and GPG keys → New SSH key**  
   Title: `Ubuntu 24.04` 등  
   Key: 위에서 출력한 `cat ~/.ssh/id_ed25519.pub` 내용 전체 붙여넣기 → **Add SSH key**

```bash
# 4. SSH로 클론 (HTTPS 대신 git@github.com 사용)
git clone git@github.com:사용자명/리포지토리명.git 3ema-gateio
cd 3ema-gateio
```

**이미 HTTPS로 클론한 경우 SSH로 바꾸기:**

```bash
cd ~/3ema-gateio
git remote set-url origin git@github.com:사용자명/리포지토리명.git
git pull origin main
```

---

### 방법 B: 파일 직접 복사 (Git 없이)
```bash
# 로컬 PC에서 우분투 서버로 복사 (서버의 프로젝트 경로: ~/3ema-gateio)
scp -r ./프로젝트폴더/* user@서버IP:~/3ema-gateio/
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
