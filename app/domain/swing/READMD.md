🎯 최종 단일 20EMA 매매 전략 (완성본)
📌 핵심 철학
"20EMA 돌파 + 의미있는 수급 2회 연속 확인 시 진입, 추세 악화 시 손절"

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

4️⃣ 매도 전략
🔴 손절 (3단계 방어)
1단계: 하드 손절 (즉시)
python# 고정 손절 -3%
if 현재가 < 매수가 × 0.97:
    return {"action": "SELL", "reason": "고정손절-3%"}

# EMA 대폭 이탈 (EMA -3% 아래)
if 현재가 < 실시간_EMA20 × 0.97:
    return {"action": "SELL", "reason": "EMA대폭이탈"}
2단계: 추세 기반 손절 (악화 시)
python# EMA 아래지만 -3%까지는 아닌 구간
if 0 < 이탈폭 < 실시간_EMA20 × 0.03:
    
    prev = redis.get(f"stop:{position_id}")
    
    if prev:
        # ⭐ 핵심: 추세 악화 확인
        가격_하락 = 현재가 < 이전_가격
        이탈_증가 = 현재_이탈폭 > 이전_이탈폭
        
        if 가격_하락 and 이탈_증가:
            # 악화 추세 → 손절
            return {"action": "SELL", "reason": "추세악화"}
        else:
            # 회복/횡보 → 대기
            update_state()
            return {"action": "HOLD"}
    else:
        # 첫 이탈 → 기록
        save_state()
        return {"action": "HOLD"}
시나리오 비교:
시간가격EMA이탈폭추세판정10:0072,00071,800-200-HOLD10:0571,50071,700+200첫이탈HOLD10:1071,65071,720+70회복중 ✅HOLD10:1571,40071,750+350악화 ❌SELL

🟢 익절 (2단계)
1단계: 목표 익절
pythonif 현재가 >= 매수가 × 1.08:
    return {"action": "SELL", "reason": "목표익절+8%"}
2단계: 수급 이탈 익절
pythonif (현재가 > 매수가 × 1.03 and  # 최소 3% 수익
    외국인_비율 < 1.0 and 
    프로그램_비율 < 1.0):
    return {"action": "SELL", "reason": "수급이탈"}
```

**이유:** 
- 최소 수익 확보 후 수급 약화 시 빠른 탈출
- 급락 전 선제적 익절

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
    
    # ==================== 매도 ====================
    
    def check_exit_signal(self, position, stock_data):
        """매도 신호 체크"""
        
        현재가 = int(stock_data['stck_prpr'])
        매수가 = position['entry_price']
        실시간_ema20 = self.get_realtime_ema20(position['symbol'], 현재가)
        
        손실률 = (현재가 - 매수가) / 매수가
        수익률 = (현재가 - 매수가) / 매수가
        현재_이탈폭 = 실시간_ema20 - 현재가
        
        # === 손절 1: 하드 스톱 ===
        
        # 고정 손절 -3%
        if 손실률 <= -0.03:
            return {"action": "SELL", "reason": "고정손절-3%"}
        
        # EMA 대폭 이탈
        if 현재가 < 실시간_ema20 * 0.97:
            return {"action": "SELL", "reason": "EMA대폭이탈"}
        
        # === 손절 2: 추세 기반 ===
        
        if 0 < 현재_이탈폭 < 실시간_ema20 * 0.03:
            
            prev = self.redis.get(f"stop:{position['id']}")
            
            if prev:
                prev = json.loads(prev)
                이전_가격 = prev['price']
                이전_이탈폭 = prev['gap']
                
                # 추세 악화 확인
                가격_하락 = 현재가 < 이전_가격
                이탈_증가 = 현재_이탈폭 > 이전_이탈폭
                
                if 가격_하락 and 이탈_증가:
                    return {"action": "SELL", "reason": "추세악화"}
                else:
                    # 상태 업데이트
                    self.redis.setex(f"stop:{position['id']}", 600, json.dumps({
                        'gap': 현재_이탈폭,
                        'price': 현재가,
                        'time': datetime.now().isoformat()
                    }))
                    return {"action": "HOLD"}
            else:
                # 첫 이탈 기록
                self.redis.setex(f"stop:{position['id']}", 600, json.dumps({
                    'gap': 현재_이탈폭,
                    'price': 현재가,
                    'time': datetime.now().isoformat()
                }))
                return {"action": "HOLD"}
        
        # === 익절 ===
        
        # 목표 익절 +8%
        if 수익률 >= 0.08:
            return {"action": "SELL", "reason": "목표익절+8%"}
        
        # 수급 이탈 익절
        외국인_비율 = (int(stock_data['frgn_ntby_qty']) / int(stock_data['acml_vol'])) * 100
        프로그램_비율 = (int(stock_data['pgtr_ntby_qty']) / int(stock_data['acml_vol'])) * 100
        
        if (수익률 >= 0.03 and 
            외국인_비율 < 1.0 and 
            프로그램_비율 < 1.0):
            return {"action": "SELL", "reason": "수급이탈"}
        
        # === 정상 ===
        self.redis.delete(f"stop:{position['id']}")
        return {"action": "HOLD"}

7️⃣ 핵심 요약표
  구분   | 내용                             | 비고 | 
진입 신호 | 20EMA 위 + 수급 3%/4.5% + 2회 연속  | 10분 
확인수급  | 기준(외≥3% OR 프≥3%) AND (합≥4.5%) | 하이브리드
괴리율   | 2% 이내                           | 늦은 진입 방지
손절     | -3% 또는 EMA -3% 또는 추세 악화      |3단계 방어
익절     | +8% 또는 수급 이탈(+3%↑)           | 2단계
상태 관리 | Redis (TTL 15분)                 | 메모리 효율
성능     | 0.0001초/종목                     | DB 캐싱