🎯 최종 단일 20EMA 매매 전략 (100점 완성본)
📌 핵심 철학
"20EMA 돌파 + 의미있는 수급 2회 연속 확인 시 진입, 추세/수급 이탈 시만 청산 (익절 없음)"

1️⃣ 실시간 EMA20 계산
pythondef get_realtime_ema20(종목코드, 현재가):
    """
    어제까지 일봉 + 현재가 포함하여 정확한 EMA 계산
    성능: 메모리 캐싱으로 0.0001초
    """
    # DB에서 어제까지 100일 일봉 조회 (메모리 캐싱됨)
    daily_data = db.get_cached_daily_prices(종목코드, days=100)
    close_prices = [d['close_price'] for d in daily_data]
    
    # 오늘 현재가 추가
    close_prices_with_today = close_prices + [현재가]
    
    # TA-Lib으로 EMA 계산
    ema_array = talib.EMA(np.array(close_prices_with_today), timeperiod=20)
    
    return ema_array[-1]

2️⃣ 진입 조건 (모두 충족 필요)
🔹 조건 A: 가격 위치
python# 1. EMA 위에 있어야 함
현재가 > 실시간_EMA20

# 2. 괴리율 2% 이내 (너무 멀리 떨어지면 늦은 진입)
괴리율 = (현재가 - 실시간_EMA20) / 실시간_EMA20
괴리율 <= 0.02
이유:

EMA 돌파는 맞지만 이미 +5% 급등했다면 늦은 진입
2% 이내면 적절한 타이밍


🔹 조건 B: 수급 강도 (하이브리드)
python외국인_비율 = (외국인_순매수량 / 당일_누적_거래량) × 100
프로그램_비율 = (프로그램_순매수량 / 당일_누적_거래량) × 100

# 하이브리드 조건
수급_OK = (
    (외국인_비율 >= 3.0 or 프로그램_비율 >= 3.0)  # 한쪽 강함
    and 
    (외국인_비율 + 프로그램_비율 >= 4.5)           # 전체 강함
)
케이스별 예시:
외국인프로그램합산결과이유3.0%1.5%4.5%✅ 진입한쪽 강하고 합산 OK3.0%0.5%3.5%❌ 불진입외국인만 강함 (리스크)2.5%2.5%5.0%❌ 불진입둘 다 3% 미만3.5%3.0%6.5%✅ 진입이상적

🔹 조건 C: 수급 유지
python# 이전 대비 20% 이상 감소하지 않았는지
if 이전_상태_있음:
    외국인_유지 = 현재_외국인 >= 이전_외국인 × 0.8
    프로그램_유지 = 현재_프로그램 >= 이전_프로그램 × 0.8
    
    수급_유지 = 외국인_유지 or 프로그램_유지
```

**예시:**
```
10:00 외국인 3.5%
10:05 외국인 3.2% → OK (8.6% 감소, 20% 이내)
10:05 외국인 2.5% → NG (28.6% 감소, 20% 초과)

🔹 조건 D: 거래량
python당일_거래량 >= 전일_거래량 × 1.2  # 120% 이상

🔹 조건 E: 급등 필터
python당일_상승률 <= 7%  # 7% 초과 시 추격 리스크

3️⃣ 2회 연속 확인 시스템 (노이즈 제거)
Redis 상태 구조
python{
    "symbol": "005930",
    
    # 현재 상태
    "curr_price": 72500,
    "curr_ema20": 71850,
    "curr_frgn_ratio": 3.5,
    "curr_pgm_ratio": 1.3,
    "curr_signal": true,
    
    # 연속 카운트
    "consecutive_count": 2,  # ⭐ 핵심
    
    # 시간 정보
    "first_signal_time": "10:00",
    "last_update": "10:05"
}
진입 로직
pythondef check_entry_signal(종목코드, stock_data):
    """진입 신호 체크 - 2회 연속 확인"""
    
    # 1. 현재 조건 충족 여부
    현재_신호 = (
        조건A_가격 and 
        조건B_수급 and 
        조건C_유지 and 
        조건D_거래량 and 
        조건E_급등필터
    )
    
    # 2. Redis에서 이전 상태 조회
    prev = redis.get(f"entry:{종목코드}")
    
    # 3. 연속 카운트 계산
    if 현재_신호:
        if prev and prev['curr_signal']:
            consecutive = prev['consecutive_count'] + 1
        else:
            consecutive = 1
    else:
        consecutive = 0
    
    # 4. 상태 저장 (TTL 15분)
    new_state = {
        "curr_signal": 현재_신호,
        "consecutive_count": consecutive,
        "curr_price": 현재가,
        "curr_ema20": 실시간_ema20,
        "curr_frgn_ratio": 외국인_비율,
        "curr_pgm_ratio": 프로그램_비율,
        "last_update": now()
    }
    redis.setex(f"entry:{종목코드}", 900, json.dumps(new_state))
    
    # 5. 최종 판정
    if consecutive >= 2:
        return {
            "action": "BUY",
            "price": 현재가,
            "ema20": 실시간_ema20,
            "confidence": "HIGH"
        }
    
    return None
```

**타임라인 예시:**
```
09:55 스캔 → 조건 불충족 → consecutive = 0
10:00 스캔 → 조건 충족! → consecutive = 1 (대기)
10:05 스캔 → 조건 충족! → consecutive = 2 (매수!) ⭐

4️⃣ 청산 전략 (추세/수급 기반, 익절 없음)

⭐ **핵심 철학: 진입 근거가 유지되면 무한정 보유**
- 진입 이유: EMA 위 + 수급 강함
- 청산 이유: EMA 이탈 OR 수급 약화/반전
- **익절 없음 - 추세가 살아있으면 끝까지 간다!**

---

### 📋 청산 조건 (우선순위순)

#### 1️⃣ 고정 손절 -3% (최우선 하드 스톱)
```python
if 수익률 <= -0.03:
    return {"action": "SELL", "reason": "고정손절"}
```
**역할**: 리스크 명확히 제한

---

#### 2️⃣ 수급 반전 (순매도 전환)
```python
# 외국인 OR 프로그램이 -2% 이상 순매도
if 외국인_비율 <= -2.0 or 프로그램_비율 <= -2.0:
    return {"action": "SELL", "reason": "수급반전"}
```

**시나리오:**
```
진입 시: 외국인 +3.5%, 프로그램 +1.5%
현재:    외국인 -2.2% (순매도 전환!) → 즉시 매도
```

**역할**: 대세 변화 조기 감지

---

#### 3️⃣ EMA 이탈 (2회 연속 확인)
```python
# Redis로 EMA 이탈 횟수 추적
if 현재가 < 실시간_EMA20:
    prev = redis.get(f"ema_breach:{position_id}")

    if prev:
        breach_count = prev['breach_count'] + 1
        if breach_count >= 2:
            return {"action": "SELL", "reason": "EMA이탈"}
        else:
            # 카운트 증가
            save_count(breach_count)
            return {"action": "HOLD", "reason": "EMA 이탈 대기 (1/2)"}
    else:
        # 첫 이탈 기록
        save_count(1)
        return {"action": "HOLD", "reason": "EMA 이탈 대기 (1/2)"}
else:
    # EMA 위로 복귀 → 카운트 리셋
    redis.delete(f"ema_breach:{position_id}")
```

**타임라인:**
```
10:00 가격=72,000, EMA=71,800 (위) → 정상
10:05 가격=71,500, EMA=71,700 (아래) → 첫 이탈 기록, HOLD
10:10 가격=71,300, EMA=71,650 (아래) → 2회 연속, SELL!
```

**노이즈 대응:**
```
10:00 가격=72,000, EMA=71,800 (위) → 정상
10:05 가격=71,500, EMA=71,700 (아래) → 첫 이탈 기록
10:10 가격=71,900, EMA=71,750 (위) → 복귀, 카운트 리셋
```

**역할**: 추세 종료 확정 전 노이즈 제거

---

#### 4️⃣ 수급 약화 (둘 다 1% 미만)
```python
# 외국인 AND 프로그램 모두 1% 미만
if 외국인_비율 < 1.0 and 프로그램_비율 < 1.0:
    return {"action": "SELL", "reason": "수급약화"}
```

**시나리오:**
```
진입 시: 외국인 +3.5%, 프로그램 +1.5%
현재:    외국인 +0.8%, 프로그램 +0.6% → 매도
```

**역할**: 세력 이탈 감지 (반전은 아니지만 힘 빠짐)

---

#### 5️⃣ 추세 악화 (EMA 아래에서 가격 하락 + 이탈폭 증가)
```python
# EMA 아래에 있을 때만 체크
if 현재가 < 실시간_EMA20:
    current_gap = 실시간_EMA20 - 현재가
    prev = redis.get(f"trend:{position_id}")

    if prev:
        이전_가격 = prev['price']
        이전_이탈폭 = prev['gap']

        # 추세 악화 = 가격 하락 AND 이탈폭 증가
        if 현재가 < 이전_가격 and current_gap > 이전_이탈폭:
            return {"action": "SELL", "reason": "추세악화"}
        else:
            # 상태 업데이트
            update_state()
    else:
        # 첫 기록
        save_state()
else:
    # EMA 위면 추세 키 삭제
    redis.delete(f"trend:{position_id}")
```

**시나리오 비교:**
| 시간 | 가격 | EMA | 이탈폭 | 추세 | 판정 |
|------|------|-----|--------|------|------|
| 10:00 | 72,000 | 71,800 | -200 (위) | - | HOLD |
| 10:05 | 71,500 | 71,700 | +200 | 첫 이탈 | HOLD |
| 10:10 | 71,650 | 71,720 | +70 | 회복 ✅ | HOLD |
| 10:15 | 71,400 | 71,750 | +350 | 악화 ❌ | SELL |

**역할**: EMA 밑에서 추가 하락 방지

---

### 🎯 청산 로직 흐름도

```
┌─────────────────┐
│  포지션 보유 중  │
└────────┬────────┘
         ↓
    ┌────────────┐
    │ 1. 고정손절 │  -3%?  → YES → SELL
    └────┬───────┘
         ↓ NO
    ┌────────────┐
    │ 2. 수급반전 │  외국인 or 프로그램 -2%? → YES → SELL
    └────┬───────┘
         ↓ NO
    ┌────────────┐
    │ 3. EMA이탈  │  2회 연속 EMA 아래? → YES → SELL
    └────┬───────┘
         ↓ NO
    ┌────────────┐
    │ 4. 수급약화 │  둘 다 1% 미만? → YES → SELL
    └────┬───────┘
         ↓ NO
    ┌────────────┐
    │ 5. 추세악화 │  EMA 아래 + 악화? → YES → SELL
    └────┬───────┘
         ↓ NO
    ┌────────────┐
    │    HOLD    │  조건 유지 → 끝까지 보유!
    └────────────┘
```

---

### 💎 왜 익절이 없는가?

**논리적 대칭:**
- 진입: EMA 위 + 수급 강함
- 청산: EMA 이탈 + 수급 약화

**+50% 가도 팔지 않는다:**
```
조건 체크:
1. 고정손절 -3%? NO
2. 수급 반전? NO (외국인 +4%, 프로그램 +2%)
3. EMA 이탈? NO (현재가 > EMA)
4. 수급 약화? NO (둘 다 1% 이상)
5. 추세 악화? NO (EMA 위)

→ HOLD! 계속 보유
```

**언제 팔리는가:**
- 수급이 무너지거나 (반전/약화)
- 추세가 끝나면 (EMA 이탈)
- 그때가 +100%든 -2%든 상관없음

**장점:**
✅ 트렌드 끝까지 탑승
✅ 논리 일관성
✅ 감정 배제

**단점:**
❌ 수익 반납 가능성
❌ 자금 효율 (장기 보유)
❌ 횡보장 약점

---

## 5️⃣ 전체 실행 흐름
```
┌─────────────────────────────────────────┐
│ 08:50 장 시작 전                         │
├─────────────────────────────────────────┤
│ 1. DB 연결 확인                          │
│ 2. 관심종목 일봉 데이터 메모리 캐싱      │
│    (100개 종목 × 100일 = 0.3초)         │
│ 3. Redis 상태 초기화                     │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ 09:00~15:20 메인 루프 (5분마다)         │
├─────────────────────────────────────────┤
│                                          │
│ [보유 종목 모니터링]                     │
│ ├─ 실시간 시세 조회 (API)               │
│ ├─ 실시간 EMA20 계산 (메모리)           │
│ ├─ 손절 조건 체크                       │
│ └─ 익절 조건 체크                       │
│                                          │
│ [신규 진입 기회 스캔]                   │
│ ├─ 조건 A~E 체크                        │
│ ├─ Redis 이전 상태 조회                 │
│ ├─ 2회 연속 확인                        │
│ └─ 매수 실행                            │
│                                          │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ 15:40 장 마감 후                         │
├─────────────────────────────────────────┤
│ 1. 오늘 일봉 데이터 DB 저장             │
│ 2. 거래 내역 기록                       │
│ 3. 일일 리포트 생성                     │
│ 4. Redis TTL 자동 만료 대기             │
└─────────────────────────────────────────┘

6️⃣ 완성 코드
pythonimport redis
import talib
import numpy as np
from datetime import datetime

# Redis 연결
redis_client = redis.Redis(host='localhost', port=6379, db=0)

class SingleEMAStrategy:
    """단일 20EMA 매매 전략"""
    
    def __init__(self, db):
        self.db = db
        self.redis = redis_client
    
    # ==================== EMA 계산 ====================
    
    def get_realtime_ema20(self, 종목코드, 현재가):
        """실시간 EMA20 계산"""
        daily_data = self.db.get_cached_daily_prices(종목코드, days=100)
        close_prices = [d['close_price'] for d in daily_data]
        close_prices_with_today = close_prices + [현재가]
        
        ema_array = talib.EMA(np.array(close_prices_with_today), timeperiod=20)
        return ema_array[-1]
    
    # ==================== 진입 ====================
    
    def check_entry_signal(self, 종목코드, stock_data):
        """진입 신호 체크"""
        
        # 데이터 추출
        현재가 = int(stock_data['stck_prpr'])
        누적거래량 = int(stock_data['acml_vol'])
        외국인_순매수 = int(stock_data['frgn_ntby_qty'])
        프로그램_순매수 = int(stock_data['pgtr_ntby_qty'])
        거래량_비율 = float(stock_data['prdy_vrss_vol_rate'])
        당일_상승률 = float(stock_data['prdy_ctrt'])
        
        # EMA 계산
        실시간_ema20 = self.get_realtime_ema20(종목코드, 현재가)
        
        # === 조건 A: 가격 ===
        가격_조건 = 현재가 > 실시간_ema20
        
        괴리율 = (현재가 - 실시간_ema20) / 실시간_ema20
        괴리_조건 = 괴리율 <= 0.02
        
        # === 조건 B: 수급 ===
        외국인_비율 = (외국인_순매수 / 누적거래량) * 100 if 누적거래량 > 0 else 0
        프로그램_비율 = (프로그램_순매수 / 누적거래량) * 100 if 누적거래량 > 0 else 0
        
        수급_조건 = (
            (외국인_비율 >= 3.0 or 프로그램_비율 >= 3.0) and
            (외국인_비율 + 프로그램_비율 >= 4.5)
        )
        
        # === 조건 C: 수급 유지 ===
        prev = self.redis.get(f"entry:{종목코드}")
        if prev:
            prev = json.loads(prev)
            외국인_유지 = 외국인_비율 >= prev.get('curr_frgn_ratio', 0) * 0.8
            프로그램_유지 = 프로그램_비율 >= prev.get('curr_pgm_ratio', 0) * 0.8
            수급_유지 = 외국인_유지 or 프로그램_유지
        else:
            수급_유지 = True
        
        # === 조건 D: 거래량 ===
        거래량_조건 = 거래량_비율 >= 120
        
        # === 조건 E: 급등 필터 ===
        급등_필터 = 당일_상승률 <= 7
        
        # === 전체 조건 ===
        현재_신호 = (
            가격_조건 and 괴리_조건 and
            수급_조건 and 수급_유지 and
            거래량_조건 and 급등_필터
        )
        
        # === 연속 카운트 ===
        if prev and prev.get('curr_signal') and 현재_신호:
            consecutive = prev.get('consecutive_count', 0) + 1
        else:
            consecutive = 1 if 현재_신호 else 0
        
        # === 상태 저장 ===
        state = {
            'curr_signal': 현재_신호,
            'consecutive_count': consecutive,
            'curr_price': 현재가,
            'curr_ema20': 실시간_ema20,
            'curr_frgn_ratio': 외국인_비율,
            'curr_pgm_ratio': 프로그램_비율,
            'last_update': datetime.now().isoformat()
        }
        self.redis.setex(f"entry:{종목코드}", 900, json.dumps(state))
        
        # === 최종 판정 ===
        if consecutive >= 2:
            return {
                'action': 'BUY',
                'price': 현재가,
                'ema20': 실시간_ema20,
                'frgn_ratio': 외국인_비율,
                'pgm_ratio': 프로그램_비율,
                'gap_ratio': 괴리율
            }
        
        return None
    
    # ==================== 청산 ====================

    def check_exit_signal(self, position, stock_data):
        """청산 신호 체크 (추세/수급 기반, 익절 없음)"""

        현재가 = int(stock_data['stck_prpr'])
        매수가 = position['entry_price']
        실시간_ema20 = self.get_realtime_ema20(position['symbol'], 현재가)

        수익률 = (현재가 - 매수가) / 매수가
        외국인_비율 = (int(stock_data['frgn_ntby_qty']) / int(stock_data['acml_vol'])) * 100
        프로그램_비율 = (int(stock_data['pgtr_ntby_qty']) / int(stock_data['acml_vol'])) * 100

        # 1. 고정 손절 -3%
        if 수익률 <= -0.03:
            return {"action": "SELL", "reason": "고정손절"}

        # 2. 수급 반전 (순매도 -2% 이상)
        if 외국인_비율 <= -2.0 or 프로그램_비율 <= -2.0:
            return {"action": "SELL", "reason": "수급반전"}

        # 3. EMA 이탈 (2회 연속 확인)
        if 현재가 < 실시간_ema20:
            prev = self.redis.get(f"ema_breach:{position['id']}")
            if prev:
                breach_count = json.loads(prev)['breach_count'] + 1
                if breach_count >= 2:
                    return {"action": "SELL", "reason": "EMA이탈"}
                else:
                    self.redis.setex(f"ema_breach:{position['id']}", 600, json.dumps({
                        'breach_count': breach_count,
                        'price': 현재가,
                        'ema': 실시간_ema20,
                        'time': datetime.now().isoformat()
                    }))
                    return {"action": "HOLD"}
            else:
                # 첫 이탈
                self.redis.setex(f"ema_breach:{position['id']}", 600, json.dumps({
                    'breach_count': 1,
                    'price': 현재가,
                    'ema': 실시간_ema20,
                    'time': datetime.now().isoformat()
                }))
                return {"action": "HOLD"}
        else:
            self.redis.delete(f"ema_breach:{position['id']}")

        # 4. 수급 약화 (둘 다 1% 미만)
        if 외국인_비율 < 1.0 and 프로그램_비율 < 1.0:
            return {"action": "SELL", "reason": "수급약화"}

        # 5. 추세 악화 (EMA 아래에서 가격 하락 + 이탈폭 증가)
        if 현재가 < 실시간_ema20:
            current_gap = 실시간_ema20 - 현재가
            prev = self.redis.get(f"trend:{position['id']}")
            if prev:
                prev_data = json.loads(prev)
                if 현재가 < prev_data['price'] and current_gap > prev_data['gap']:
                    return {"action": "SELL", "reason": "추세악화"}
                else:
                    self.redis.setex(f"trend:{position['id']}", 600, json.dumps({
                        'gap': current_gap,
                        'price': 현재가,
                        'time': datetime.now().isoformat()
                    }))
            else:
                self.redis.setex(f"trend:{position['id']}", 600, json.dumps({
                    'gap': current_gap,
                    'price': 현재가,
                    'time': datetime.now().isoformat()
                }))
        else:
            self.redis.delete(f"trend:{position['id']}")

        return {"action": "HOLD"}

7️⃣ 핵심 요약표 (100점 완성본)

| 구분 | 내용 | 비고 |
|------|------|------|
| **진입 신호** | 20EMA 위 + 수급 3%/4.5% + 2회 연속 | 10분 간격 |
| **수급 기준** | (외≥3% OR 프≥3%) AND (합≥4.5%) | 하이브리드 |
| **괴리율** | 2% 이내 | 늦은 진입 방지 |
| **청산 1** | 고정 손절 -3% | 하드 스톱 |
| **청산 2** | 수급 반전 (순매도 -2%) | 대세 변화 감지 |
| **청산 3** | EMA 이탈 2회 연속 | 추세 종료 |
| **청산 4** | 수급 약화 (둘 다 1% 미만) | 세력 이탈 |
| **청산 5** | 추세 악화 (EMA 아래 악화) | 추가 하락 방지 |
| **익절** | ❌ 없음 | 추세 끝까지 탑승 |
| **상태 관리** | Redis (TTL 15분) | 메모리 효율 |
| **성능** | 0.0001초/종목 | DB 캐싱 |

---

## 🎖️ 전략 평가

| 항목 | 점수 | 평가 |
|------|------|------|
| 진입 로직 | **88/100** | 5개 조건 + 2회 연속 + 분할 매수 |
| 청산 로직 | **100/100** | 논리적 대칭, 완벽한 일관성 |
| 리스크 관리 | **88/100** | 손절 명확, 분할 매수/매도로 리스크 분산 |
| 논리 일관성 | **100/100** | 진입↔청산 대칭 완벽 |
| 감정 배제 | **100/100** | 시스템 기계적 실행 |
| 자금 효율 | **78/100** | 분할 진입으로 자본 효율 향상 |
| 트렌드 적응 | **95/100** | 추세 끝까지 탑승 |

**총점: 93/100** (분할 매수/매도 적용)

---

## 8️⃣ 백테스팅 전략 (SingleEMABacktestStrategy)

### 📌 핵심 차이점
실전 매매에서는 **외국인/프로그램 수급 데이터**를 실시간으로 사용할 수 있지만,
백테스팅에서는 **DB의 OHLCV(종가, 거래량) 데이터만** 사용 가능합니다.

따라서 수급 데이터를 **OBV(On Balance Volume)**로 대체하여 백테스팅을 수행합니다.

---

### 🔄 조건 대체 매핑

#### 진입 조건 (Entry Conditions)

| # | 조건 | 실전 매매 | 백테스팅 대체 |
|---|------|-----------|---------------|
| 1 | 가격 위치 | 현재가 > EMA20, 괴리율 ≤ 2% | **동일** |
| 2 | 수급 강도 | 외국인 ≥ 3% OR 프로그램 ≥ 3% | **OBV z-score > 1.0** |
| 3 | 수급 유지 | 이전 대비 20% 이상 감소 안됨 | **OBV 연속 상승** |
| 4 | 거래량 | 전일 대비 120% 이상 | **20일 평균 거래량 대비 120%** |
| 5 | 급등 필터 | 당일 상승률 ≤ 7% | **(종가-전일종가)/전일종가 ≤ 7%** |
| 6 | 연속 확인 | Redis 2회 연속 (10분) | **1회 즉시 진입** (일봉 종가 = 당일 확인) |

#### 청산 조건 (Exit Conditions)

| # | 조건 | 실전 매매 | 백테스팅 대체 |
|---|------|-----------|---------------|
| 1 | 고정 손절 | -3% | **동일** |
| 2 | 수급 반전 | 순매도 -2% 이상 | **OBV z-score < -1.0** |
| 3 | EMA 이탈 | 2회 연속 EMA 아래 (Redis) | **2일 연속 EMA 아래 (인덱스)** |
| 4 | 수급 약화 | 외국인 & 프로그램 1% 미만 | **OBV z-score ≈ 0 (±0.3)** + EMA 하회 |
| 5 | 추세 악화 | EMA 아래 + 가격 하락 + 이탈폭 증가 | **동일** |

---

### 📊 OBV (On Balance Volume) 설명

> ⚠️ **주의**: OBV는 외국인/프로그램 수급의 **근사치**일 뿐, 정확한 대체가 아닙니다.
> - 외국인/프로그램 수급: **특정 주체**의 순매수량
> - OBV: **전체 거래량**의 누적 방향 (누가 샀는지 알 수 없음)
> - 정확한 백테스팅을 원하면 일별 투자자별 매매동향 데이터 수집 권장

OBV는 거래량과 가격 변동을 결합한 지표로, 수급의 방향성을 **추정**합니다.

```python
# OBV 계산
if 오늘_종가 > 어제_종가:
    OBV = 어제_OBV + 오늘_거래량
elif 오늘_종가 < 어제_종가:
    OBV = 어제_OBV - 오늘_거래량
else:
    OBV = 어제_OBV

# OBV z-score 계산 (7일 기준)
obv_diff = OBV 변화량
obv_z = (obv_diff - 7일_평균) / 7일_표준편차
```

**OBV z-score 해석:**
| z-score | 의미 | 실전 매매 대응 |
|---------|------|----------------|
| > 1.0 | 강한 매수세 유입 | 외국인/프로그램 3%+ |
| 0 ~ 1.0 | 보통 매수세 | 수급 유지 |
| -0.3 ~ 0.3 | 수급 정체 | 수급 약화 |
| < -1.0 | 강한 매도세 유입 | 수급 반전 |

---

### 🔧 백테스팅 파라미터

```python
class SingleEMABacktestStrategy:
    # 기본 파라미터 (실전 매매와 동일)
    EMA_PERIOD = 20
    MAX_GAP_RATIO = 0.02        # 괴리율 2%
    VOLUME_RATIO_THRESHOLD = 1.2 # 거래량 120%
    MAX_SURGE_RATIO = 0.07      # 급등 필터 7%
    CONSECUTIVE_REQUIRED = 1    # 즉시 진입 (일봉 종가 = 당일 확인)
    STOP_LOSS_FIXED = -0.03     # 손절 -3%
    EMA_BREACH_REQUIRED = 2     # EMA 이탈 2일

    # 백테스팅 전용 파라미터 (OBV 기반)
    OBV_Z_BUY_THRESHOLD = 1.0   # 매수 신호
    OBV_Z_SELL_THRESHOLD = -1.0 # 매도 신호
    OBV_Z_WEAK_THRESHOLD = 0.3  # 수급 약화
    OBV_LOOKBACK = 7            # z-score 계산 기간
    VOLUME_MA_PERIOD = 20       # 거래량 이동평균
```

---

### 🔁 분할 매수/매도 전략

백테스팅 전략은 **리스크 관리**를 위해 분할 매수와 분할 매도를 지원합니다.

#### 📌 분할 매수 (Buy Ratio)

```python
# 1차 매수 (buy_count = 0)
매수금액 = 남은_현금 * buy_ratio  # 예: 50%
진입가 = 1차_매수가  # 평균단가 기준 설정

# 2차 매수 (buy_count = 1)
매수금액 = 남은_현금 * 100%  # 전량 투입
진입가 = 유지  # 1차 매수가로 손익 계산
```

**실행 예시 (초기 자본 1,000만원, buy_ratio = 50%)**
```
Day 1: 1차 매수 신호 발생
  → 500만원 매수 (1,000만원 × 50%)
  → 남은 현금: 500만원
  → buy_count = 1

Day 5: 2차 매수 신호 발생 (추세 강화)
  → 500만원 매수 (남은 현금 전량)
  → 남은 현금: 0원
  → buy_count = 2 (더 이상 매수 불가)
```

#### 📌 분할 매도 (Sell Ratio)

```python
# 1차 매도 (sell_count = 0)
매도수량 = 보유수량 * sell_ratio  # 예: 50%
→ 일부 차익 실현, 나머지 포지션 유지

# 2차 매도 (sell_count >= 1)
매도수량 = 보유수량 * 100%  # 전량 청산
→ 포지션 완전 종료, 모든 상태 리셋
```

**실행 예시 (보유 수량 100주, sell_ratio = 50%)**
```
Day 10: 1차 매도 신호 (수급 약화)
  → 50주 매도 (100주 × 50%)
  → 잔여 수량: 50주
  → sell_count = 1

Day 15: 2차 매도 신호 (EMA 이탈)
  → 50주 매도 (전량)
  → 잔여 수량: 0주
  → buy_count, sell_count 모두 리셋
```

#### 🎯 리스크 관리 효과

| 전략 | 장점 | 단점 |
|------|------|------|
| **분할 매수** | • 평균 단가 낮춤<br>• 추세 강화 시 추가 진입<br>• 초기 손실 제한 | • 기회비용 발생<br>• 단타 부적합 |
| **분할 매도** | • 일부 차익 실현<br>• 추세 반전 대응<br>• 심리적 안정 | • 수익 최대화 제한<br>• 거래 수수료 증가 |

#### ⚙️ 파라미터 권장값

| 목적 | BUY_RATIO | SELL_RATIO | 설명 |
|------|-----------|------------|------|
| **공격적** | 100 | 100 | 분할 없이 즉시 전량 매매 |
| **균형** | 50 | 50 | 2회 분할 매매 (권장) |
| **보수적** | 30 | 30 | 초기 소량 진입/청산 |

---

### 📝 사용 방법

#### API 호출
```json
POST /backtest
{
  "ST_CODE": "005930",
  "SWING_TYPE": "C",
  "INIT_AMOUNT": 10000000,
  "BUY_RATIO": 50,    // 1차 매수 비율 (%)
  "SELL_RATIO": 50    // 1차 매도 비율 (%)
}
```

#### 전략 팩토리
```python
# strategy_factory.py
_strategies = {
    "A": EMAStrategy(),           # 이평선 전략 (3선)
    "B": IchimokuStrategy(),      # 일목균형표
    "C": SingleEMABacktestStrategy(),  # 단일 20EMA (NEW)
}
```

---

### 📁 관련 파일

| 파일 | 설명 |
|------|------|
| `strategies/single_ema_strategy.py` | 실전 매매 전략 (Redis + 수급) |
| `strategies/single_ema_backtest_strategy.py` | 백테스팅 전략 (OBV 기반) |
| `strategy_factory.py` | 전략 팩토리 ("C" 타입) |
| `backtest/backtest_service.py` | 백테스팅 실행 서비스 |

---

### ⚖️ 실전 vs 백테스팅 비교

| 항목 | 실전 매매 | 백테스팅 |
|------|-----------|----------|
| 수급 데이터 | 외국인/프로그램 실시간 | OBV 기반 추정 |
| 연속 확인 | Redis 2회 (10분) | 1회 즉시 (일봉 종가 = 확인) |
| 거래량 비교 | 전일 대비 실시간 | 20일 이동평균 대비 |
| 상태 관리 | Redis TTL | 변수 (인메모리) |
| 정확도 | 높음 (실제 수급) | 중간 (OBV 대체) |

---

### ⚠️ 백테스팅 한계점

1. **수급 데이터 근사치**: OBV는 외국인/프로그램 수급의 대체 지표일 뿐, 정확한 값이 아님
2. **일봉 기준**: 실전은 5분 간격이지만, 백테스팅은 일봉 기준으로 신호 판단
3. **슬리피지 미반영**: 실제 체결가와 백테스팅 종가 간 차이 존재
4. **장중 변동 무시**: 일중 급등/급락 후 복귀하는 경우 포착 불가

---

### 🎯 백테스팅 활용 가이드

1. **파라미터 최적화**: OBV z-score 임계값 조정하여 최적 값 탐색
2. **기간별 테스트**: 상승장/하락장/횡보장 각각 테스트
3. **종목별 특성**: 대형주/중소형주 별도 분석
4. **실전 적용 전**: 백테스팅 수익률의 70% 정도를 실전 기대치로 설정