# CLAUDE.md

이 파일은 Claude Code(claude.ai/code)가 이 저장소의 코드에서 작업할 때 참고할 지침을 제공합니다.

## 프로젝트 개요

Auto-Trader는 FastAPI로 구축된 한국 주식 자동매매 백엔드 서비스입니다. 사용자가 계좌를 등록하고 한국투자증권(KIS) API에 연결하여 기술 지표(EMA, RSI, ADX, OBV)를 기반으로 자동 스윙 매매 전략을 실행할 수 있습니다.

## 커맨드

### 개발
```bash
# 의존성 설치 (uv 패키지 매니저 사용)
uv sync

# 개발 서버 실행
uvicorn app.main:app --reload

# Docker로 실행
docker build -t auto-trader .
docker run -p 8000:auto-trader
```

### 환경 변수
`.env` 파일에 다음 항목이 필요합니다:
- `DATABASE_URL`: MySQL 비동기 연결 문자열 (asyncmy 드라이버)
- KIS API 자격증명 (AUTH_KEY 테이블을 통해 사용자별로 관리)

## 아키텍처

### 디렉토리 구조
```
app/
├── main.py              # FastAPI 앱 진입점 및 모든 라우트 정의
├── api/                 # 외부 API 통합
│   ├── kis_open_api.py  # KIS OAuth 토큰 관리
│   └── local_stock_api.py # KIS 국내 주식 API 호출
├── infrastructure/
│   ├── database/        # SQLAlchemy 비동기 설정, 테이블 정의
│   └── security/        # JWT 유틸, 암호화
├── module/
│   ├── schedules.py     # APScheduler 크론 작업
│   └── redis_connection.py # Redis 싱글톤
├── swing/               # 스윙 매매 도메인
│   ├── strategies/      # 매매 전략 구현
│   │   ├── base_strategy.py
│   │   ├── ema_strategy.py      # EMA 골든크로스 전략
│   │   └── ichimoku_strategy.py # 일목균형표 전략
│   ├── tech_analysis.py # 기술 지표 계산
│   ├── auto_swing_batch.py # 정기 매매 작업
│   └── backtest/        # 백테스팅 기능
└── [domain]/            # user, account, auth, stock, order 모듈
    ├── *_model.py       # Pydantic 모델
    ├── *_service.py     # 비즈니스 로직
    └── *_crud.py        # 데이터베이스 작업
```

### 핵심 패턴

1. **데이터베이스**: MySQL과 비동기 SQLAlchemy(asyncmy). `Database` 클래스에서 엔진/세션 싱글톤 패턴. 시작 시 자동으로 테이블 생성.

2. **인증**: `fastapi-jwt-auth`를 통한 JWT 토큰. 사용자 자격증명 + KIS API 키는 `cryptography`로 암호화.

3. **정기 매매**: APScheduler가 `trade_job`을 1시간 단위로(평일 9AM-3PM) 실행하고, `day_collect_job`은 3:31PM에 일일 데이터 수집.

4. **매매 전략**: `BaseStrategy` 추상 클래스를 사용한 전략 패턴. 현재 구현:
   - `EmaStrategy`: EMA 골든크로스 (단기/중기/장기)
   - `IchimokuStrategy`: 일목균형표 신호

5. **신호 흐름**: SWING_TRADE의 SIGNAL 컬럼이 상태 추적: 0=초기, 1=1차 매수, 2=2차 매수, 3=매도

### 데이터베이스 테이블
- USER, ACCOUNT, AUTH_KEY: 사용자/계좌 관리
- STOCK_INFO, STOCK_DAY_HISTORY: 주식 마스터 데이터 및 OHLCV
- SWING_TRADE, EMA_OPT: 매매 설정
- TRADE_HISTORY: 체결 내역

### 외부 의존성
- KIS Open API: 실시간 시세 및 주문 체결
- Redis: 토큰 캐싱
- MySQL: 데이터 영속성
- TA-Lib: 기술 지표 계산
