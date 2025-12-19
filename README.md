# Auto-Trader

한국투자증권(KIS) API를 활용한 주식 자동매매 백엔드 서비스

## 소개

Auto-Trader는 FastAPI 기반의 한국 주식 자동매매 시스템입니다. 사용자가 계좌를 등록하고 기술적 지표(EMA, RSI, ADX, OBV)를 기반으로 스윙 매매 전략을 자동 실행할 수 있습니다.

## 주요 기능

- **회원 관리**: 회원가입, JWT 기반 인증
- **계좌 연동**: KIS API 키 암호화 저장 및 계좌 관리
- **실시간 데이터**: WebSocket을 통한 실시간 시세/호가 조회
- **자동 매매**: 5분 간격 스윙 매매 신호 분석 및 자동 주문
- **매매 전략**: EMA 골든크로스, 일목균형표, 단순이평선 등 다양한 전략 지원
- **백테스팅**: 과거 데이터 기반 전략 검증

## 기술 스택

| 분류 | 기술 |
|------|------|
| Framework | FastAPI, Uvicorn |
| Database | MySQL (aiomysql), SQLAlchemy 2.x |
| Cache | Redis |
| Authentication | fastapi-jwt-auth, bcrypt |
| Scheduler | APScheduler |
| Technical Analysis | TA-Lib, pandas |
| HTTP Client | httpx |
| Encryption | cryptography |

## 요구사항

- Python 3.12+
- MySQL 8.0+
- Redis
- TA-Lib (주가 계산 시스템 라이브러리)

## 설치

### 1. TA-Lib 설치 (시스템 의존성)

```bash
# macOS
brew install ta-lib

# Ubuntu/Debian
sudo apt-get install ta-lib

# Windows
# https://github.com/cgohlke/talib-build/releases 에서 wheel 다운로드
```

### 2. 프로젝트 설정

```bash
# 저장소 클론
git clone https://github.com/your-username/auto-trader.git
cd auto-trader

# 의존성 설치 (uv 패키지 매니저)
uv sync

# 또는 pip 사용
pip install -e .
```

### 3. 환경 변수 설정

`.env` 파일을 생성하고 다음 항목을 설정:

```env
# Database
DATABASE_URL=mysql+aiomysql://user:password@localhost:3306/auto_trader

# Redis
REDIS_URL=redis://localhost:6379

# JWT
JWT_SECRET_KEY=your-secret-key

# Encryption
AES_KEY=your-32-byte-aes-key
```

### 4. 데이터베이스 초기화

애플리케이션 시작 시 테이블이 자동 생성됩니다.

## 실행

### 개발 서버

```bash
uvicorn app.main:app --reload
```

### Docker

```bash
docker build -t auto-trader .
docker run -p 8000:8000 auto-trader
```

### API 문서

서버 실행 후 접속:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 프로젝트 구조

```
app/
├── main.py                 # FastAPI 앱 진입점
├── api/                    # 외부 API 통합
│   ├── kis_open_api.py     # KIS OAuth 토큰 관리
│   └── local_stock_api.py  # KIS 국내 주식 API
├── infrastructure/
│   ├── database/           # DB 연결 및 테이블 정의
│   └── security/           # JWT, 암호화 유틸
├── module/
│   ├── schedules.py        # APScheduler 작업
│   └── redis_connection.py # Redis 연결
├── swing/                  # 스윙 매매 도메인
│   ├── strategies/         # 매매 전략
│   │   ├── base_strategy.py
│   │   ├── ema_strategy.py
│   │   └── ichimoku_strategy.py
│   ├── tech_analysis.py    # 기술 지표 계산
│   ├── auto_swing_batch.py # 자동 매매 배치
│   └── backtest/           # 백테스팅
├── user/                   # 사용자 도메인
├── account/                # 계좌 도메인
├── auth/                   # 인증 도메인
├── stock/                  # 주식 도메인
└── order/                  # 주문 도메인
```

## 매매 전략

### EMA 골든크로스 전략

단기/중기/장기 지수이동평균선의 교차를 분석하여 매매 신호 생성:

- **1차 매수**: 단기 EMA가 중기 EMA 상향 돌파 + RSI/ADX/OBV 조건 충족
- **2차 매수**: 중기 EMA가 장기 EMA 상향 돌파 + 추가 조건 충족
- **매도**: 데드크로스 또는 목표 수익률 도달

### 일목균형표 전략

전환선, 기준선, 선행스팬을 활용한 매매 신호 생성

### 단일 이동평균선 매매법

### 신호 상태 (SIGNAL)

| 값 | 상태 |
|----|------|
| 0 | 초기 (매수 대기) |
| 1 | 1차 매수 완료 |
| 2 | 2차 매수 완료 |
| 3 | 매도 완료 |

## 스케줄러

| 작업 | 주기 | 설명 |
|------|------|------|
| trade_job | 평일 9:00-15:00 (1시간 간격) | 매매 신호 분석 및 주문 |
| day_collect_job | 평일 15:31 | 일일 OHLCV 데이터 수집 |

## API 엔드포인트

### 인증
- `POST /users/register` - 회원가입
- `POST /users/login` - 로그인

### 계좌
- `POST /accounts/register` - 계좌 등록
- `GET /accounts/balance` - 잔고 조회

### 스윙 매매
- `POST /swing/register` - 스윙 매매 등록
- `GET /swing/status` - 매매 상태 조회
- `GET /swing/list` - 스윙 목록 조회

### 주식
- `POST /stocks/order` - 주식 주문
- `WebSocket /kis_socket` - 실시간 시세

## 라이선스

MIT License