**주식 자동매매 백엔드 서비스 PRD (Product Requirements Document)**

---

## **1. 프로젝트 개요**

- **프로젝트 이름**: Auto-Trader
- **목적**: 사용자가 주식 자동매매를 설정하고, 실시간 데이터를 기반으로 매수/매도 전략을 실행하여 효율적인 투자 관리를 지원하는 백엔드 서비스.
- **주요 기능**:
  - 회원가입 및 계좌 등록
  - 실시간 소켓 통신을 통한 주식 현재가 및 호가 데이터 제공
  - 오토 스윙 등록 및 자동 매매/매도 실행
  - 이평선 지수(EMA), RSI, ADX, OBV를 활용한 매매/매도 전략

---

## **2. 주요 기능 설명**

### **2.1 회원가입 및 계좌 등록**

- **회원가입**:
  - 사용자 정보를 입력받아 회원가입 처리 (이메일, 비밀번호 등).
  - 비밀번호는 암호화하여 저장.
- **계좌 등록**:
  - 사용자가 주식 계좌를 등록할 수 있는 기능.
  - 계좌 등록 시 API 키와 Secret Key를 암호화하여 저장.

### **2.2 실시간 소켓 통신**

- **주식 현재가 및 호가 데이터 제공**:
  - 실시간 소켓 통신을 통해 주식의 현재가, 호가 데이터를 사용자에게 제공.
  - 데이터는 외부 API(증권사 API 등)에서 가져와 사용자에게 전달.
- **실시간 전략 실행**:
  - 이동평균선(BasicPlan) 및 RSI(RSIStrategy) 전략을 실시간으로 실행.
  - 실시간 체결 데이터를 기반으로 매수/매도 신호 생성.
  - OHLC(Open-High-Low-Close) 데이터 생성 및 분석.

### **2.3 오토 스윙 등록 및 자동 매매/매도**

- **오토 스윙 등록**:
  - 사용자가 매매 전략을 설정하여 오토 스윙을 등록.
  - 매매 전략은 이평선 지수(EMA), RSI, ADX, OBV를 기반으로 판단.
- **자동 매매/매도**:
  - 등록된 오토 스윙에 따라 1시간 간격으로 매매/매도 신호를 확인.
  - 매매/매도 신호 발생 시 자동으로 거래 실행.
  - 매매/매도 상태는 `SIGNAL` 값으로 관리:
    - `0`: 초기 상태
    - `1`: 1차 매수 완료
    - `2`: 2차 매수 완료
    - `3`: 매도 완료

### **2.4 매매/매도 전략**

- **사용 지표**:
  - **이평선 지수(EMA)**: 단기, 중기, 장기 이평선의 교차를 통해 매수/매도 신호 판단.
  - **RSI**: 과매수/과매도 상태를 확인하여 매수/매도 신호 판단.
  - **ADX**: 추세 강도를 확인하여 신호의 신뢰도를 보강.
  - **OBV**: 거래량 흐름을 확인하여 매수/매도 신호 판단.
- **전략 로직**:
  - 단기-중기 이평선 교차와 RSI, ADX, OBV 조건을 조합하여 1차 매수/매도 신호 판단.
  - 중기-장기 이평선 교차와 RSI, ADX, OBV 조건을 조합하여 2차 매수/매도 신호 판단.

---

## **3. 기술 스택**

- **프로그래밍 언어**: Python
- **프레임워크**: FastAPI
- **데이터베이스**: MySQL (SQLAlchemy ORM 사용)
- **비동기 처리**: Asyncio
- **실시간 통신**: WebSocket
- **외부 라이브러리**:
  - `pandas`: 데이터 처리
  - `numpy`: 수치 계산
  - `apscheduler`: 스케줄링
  - `cryptography`: 데이터 암호화
  - `httpx`: 외부 API 통신

---

## **4. API 명세**

### **4.1 회원 관련 API**

- **회원가입**: `POST /users/register`

  - **요청**:
    ```json
    {
      "email": "string",
      "password": "string"
    }
    ```
  - **응답**:
    ```json
    {
      "message": "회원가입 성공"
    }
    ```

- **로그인**: `POST /users/login`
  - **요청**:
    ```json
    {
      "email": "string",
      "password": "string"
    }
    ```
  - **응답**:
    ```json
    {
      "access_token": "string",
      "token_type": "Bearer"
    }
    ```

### **4.2 계좌 관련 API**

- **계좌 등록**: `POST /accounts/register`

  - **요청**:
    ```json
    {
      "account_number": "string",
      "api_key": "string",
      "secret_key": "string"
    }
    ```
  - **응답**:
    ```json
    {
      "message": "계좌 등록 성공"
    }
    ```

- **잔고 조회**: `GET /accounts/balance`
  - **요청**: 없음
  - **응답**:
    ```json
    {
      "cash_balance": "number",
      "stock_balance": [
        {
          "stock_code": "string",
          "quantity": "number",
          "current_price": "number"
        }
      ]
    }
    ```

### **4.3 주식 관련 API**

- **실시간 웹소켓 연결**: `WebSocket /kis_socket`

  - **인증 요청**:
    ```json
    {
      "type": "auth",
      "token": "jwt_token",
      "strategy_type": "basic" // 또는 "rsi"
    }
    ```
  - **실시간 데이터 구독**:
    ```json
    {
      "tr_type": "1",
      "tr_id": "H0STASP0", // 호가 또는 "H0STCNT0" // 체결
      "st_code": "005930"
    }
    ```
  - **응답**: 실시간 주식 데이터 스트림

- **전략 데이터 조회**: `GET /realtime/strategy-data`

  - **요청**: Authorization 헤더에 JWT 토큰
  - **응답**:
    ```json
    {
      "status": "success",
      "data": {
        "contract_data": {},
        "executed_data": [],
        "active_strategies": []
      }
    }
    ```

- **OHLC 데이터 조회**: `GET /realtime/ohlc/{stock_code}`

  - **요청**:
    - `stock_code`: 종목코드
    - `bar_size`: 봉 크기 (기본값: "1Min")
    - Authorization 헤더에 JWT 토큰
  - **응답**:
    ```json
    {
      "status": "success",
      "data": [
        {
          "open": 50000,
          "high": 51000,
          "low": 49000,
          "close": 50500
        }
      ]
    }
    ```

- **주식 주문**: `POST /stocks/order`
  - **요청**:
    ```json
    {
      "stock_code": "string",
      "order_type": "buy/sell",
      "quantity": "number"
    }
    ```
  - **응답**:
    ```json
    {
      "message": "주문 성공",
      "order_id": "string"
    }
    ```

### **4.4 오토 스윙 관련 API**

- **오토 스윙 등록**: `POST /swing/register`

  - **요청**:
    ```json
    {
      "short_term": 5,
      "medium_term": 20,
      "long_term": 60,
      "rsi_threshold": 30,
      "adx_threshold": 25,
      "obv_threshold": 1000
    }
    ```
  - **응답**:
    ```json
    {
      "message": "오토 스윙 등록 성공"
    }
    ```

- **오토 스윙 상태 조회**: `GET /swing/status`
  - **요청**: 없음
  - **응답**:
    ```json
    {
      "status": "active",
      "signal": "buy",
      "last_updated": "datetime"
    }
    ```

---

## **5. 비즈니스 로직**

- **1시간 간격으로 매매/매도 신호 확인**:
  - `apscheduler`를 사용하여 1시간마다 매매/매도 신호를 확인.
  - 신호 발생 시 자동으로 거래 실행.
- **상태 관리**:
  - `SIGNAL` 값을 통해 매매/매도 상태를 관리.
  - 데이터베이스에 상태를 저장하여 지속적인 상태 추적 가능.

---

## **6. 보안**

- 사용자 데이터 및 계좌 정보는 암호화하여 저장.
- 외부 API 통신 시 HTTPS를 사용하여 데이터 암호화.

---

## **7. 성능**

- 실시간 소켓 통신의 안정성을 위해 WebSocket 연결 최적화.
- 비동기 처리로 대량의 요청을 효율적으로 처리.

---

## **8. 향후 확장**

- AI 기반의 매매 전략 추가.
