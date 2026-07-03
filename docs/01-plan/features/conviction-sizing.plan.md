# Plan: Conviction Score 기반 포지션 사이징

## 개요
매수 신호의 강도(Conviction Score)에 따라 투입 비율을 가변적으로 조절하여, 강한 신호에서는 더 많이 투입하고 약한 신호에서는 줄이는 포지션 사이징 시스템을 도입한다.

## 문제 정의

### 현재 포지션 사이징 로직

```
Qty = min(배정금 × ENTRY_PCT / 가격, 배정금 × MAX_LOSS_PCT / 리스크)
```

| 파라미터 | 값 | 역할 |
|---------|-----|------|
| ENTRY_PCT | 0.5 (50%) | 배정금 대비 투입 비율 상한 |
| MAX_LOSS_PCT | 0.03 (3%) | 손절 시 배정금 대비 최대 손실률 |

### 문제점

1. **신호 강도 무시**: ADX가 28이든 16이든, OBV z-score가 2.0이든 0.1이든 항상 동일한 50%를 투입
2. **강한 신호에서 과소 투입**: 확실한 매수 기회에도 배정금의 절반만 투입하여 수익 기회 제한
3. **MAX_LOSS_PCT 사실상 무의미**: ATR_MULTIPLIER=2.0으로 손절 폭이 넓지만, ENTRY_PCT 0.5가 먼저 걸려서 MAX_LOSS_PCT(3%)가 발동하지 않음 → 죽은 코드

### 근거 분석: MAX_LOSS_PCT 무의미 확인

일반적인 종목 (ATR = 주가의 2.5%) 기준:
```
주가: 80,000원 / ATR: 2,000원 / 배정금: 1,000만원
손절가: 80,000 - 2,000 × 2.0 = 76,000원
리스크/주: 4,000원

ENTRY_PCT 수량  = 1,000만 × 0.5 / 80,000 = 62주  ← 이게 먼저 걸림
MAX_LOSS_PCT 수량 = 1,000만 × 0.03 / 4,000 = 75주
최종: min(62, 75) = 62주 → MAX_LOSS_PCT 미작동
```

ENTRY_PCT를 0.8로 올려도 MAX_LOSS_PCT 3%가 걸려서 Conviction 효과를 무력화:
```
ENTRY_PCT 수량  = 1,000만 × 0.8 / 80,000 = 100주
MAX_LOSS_PCT 수량 = 1,000만 × 0.03 / 4,000 = 75주  ← 이게 걸림
최종: min(100, 75) = 75주 → Conviction 무관하게 75주 고정
```

## 변경 사항

### 새로운 포지션 사이징 공식

```
Qty = 배정금 × ENTRY_PCT × Conviction / 가격

ENTRY_PCT = 0.8 (최대 배정금의 80%)
Conviction = 0.4 ~ 1.0 (신호 강도에 따라 가변)
MAX_LOSS_PCT = 제거
```

| 신호 강도 | Conviction | 실제 투입 |
|----------|-----------|----------|
| 약한 신호 (겨우 통과) | 0.4 | 32% |
| 보통 신호 | 0.6 | 48% (≈기존 50%) |
| 강한 신호 | 0.8 | 64% |
| 매우 강한 신호 | 1.0 | **80% (풀 투입)** |

### Conviction Score 계산

OBV z-score를 핵심(70%), ADX를 보조(30%)로 사용한다.

**OBV z-score를 핵심으로 선택한 이유:**
- ADX, DI는 "추세가 있다/없다"를 알려주지만, 추세가 **지속될지**는 수급이 결정
- OBV z-score는 기관/세력의 매집 강도를 직접 반영
- 매도 로직에서도 OBV를 핵심 게이트로 사용 중 (2차 익절, 수급 안정화) → 일관성

**ADX를 보조로만 사용하는 이유:**
- ADX 30+은 추세 과열/소진 신호일 수 있어 높다고 무조건 좋지 않음
- 18~25 구간이 "아직 여력 있는 추세"로 더 유리할 수 있음

```python
def calc_conviction(adx, obv_z):
    # OBV 점수: z=0→0.3, z=0.5→0.6, z=1.0→0.8, z=1.5+→1.0
    obv_score = clip(0.3 + obv_z * 0.467, 0.3, 1.0)

    # ADX 점수: 15→0.3, 20→0.7, 25→1.0, 30+→0.8 (과열 감점)
    if adx <= 25:
        adx_score = clip(0.3 + (adx - 15) * 0.07, 0.3, 1.0)
    else:
        adx_score = max(0.8, 1.0 - (adx - 25) * 0.04)  # 25 이후 감점

    raw = obv_score * 0.7 + adx_score * 0.3

    # 0.4 ~ 1.0 범위로 매핑
    return clip(raw, 0.4, 1.0)
```

**DI 차이, EMA 괴리를 제외한 이유:**
- 이미 매수 진입 필터에서 걸러지므로 Conviction에 중복 반영 불필요

### 예상 시나리오별 결과

| 시나리오 | ADX | OBV z | Conviction | 투입비 |
|----------|-----|-------|-----------|--------|
| 강한 추세 + 강한 수급 | 24 | 1.5 | ~0.95 | 76% |
| 보통 추세 + 보통 수급 | 20 | 0.5 | ~0.62 | 50% |
| 겨우 통과 (애매한 신호) | 16 | 0.1 | ~0.43 | 34% |
| 과열 추세 + 강한 수급 | 35 | 1.5 | ~0.92 | 74% |

### MAX_LOSS_PCT 제거 근거

- 현재 구조에서 사실상 미작동 (ENTRY_PCT가 먼저 걸림)
- ENTRY_PCT 상향 시 Conviction을 무력화하는 부작용
- 손실 방어는 이미 `ATR×2.0` 손절이 담당
- 제거해도 리스크 구조 변화 없음

## 변경 대상

### 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/domain/swing/trading/strategies/base_single_ema.py` | ENTRY_PCT 0.8 변경, MAX_LOSS_PCT 제거, Conviction 계산 메서드 추가 |
| `app/domain/swing/backtest/strategies/single_ema_backtest_strategy.py` | 수량 계산 시 Conviction 적용, MAX_LOSS_PCT 로직 제거 |
| `app/domain/swing/trading/auto_swing_batch.py` | 실전 수량 계산 시 Conviction 적용, MAX_LOSS_PCT 로직 제거 |
| `app/domain/swing/backtest/strategies/README.md` | 포지션 사이징 문서 업데이트 |
| `app/domain/swing/trading/strategies/README.md` | 포지션 사이징 문서 업데이트 |

### 구현 순서

1. `base_single_ema.py` — 파라미터 변경 + `calc_conviction()` 메서드 추가
2. `single_ema_backtest_strategy.py` — 백테스트 수량 계산 로직 변경
3. 백테스트 결과 비교 (기존 고정 비율 vs Conviction 적용)
4. `auto_swing_batch.py` — 실전 수량 계산 로직 변경
5. README 양쪽 업데이트

## 리스크 및 고려사항

1. **백테스트 과최적화**: Conviction 가중치(OBV 70%, ADX 30%)와 점수 매핑은 백테스트에서 잘 나와도 실전에서 다를 수 있음 → 보수적 범위(0.4~1.0)로 설계
2. **기존 전략과 성과 비교 필수**: 도입 전 동일 기간 백테스트 비교로 개선 여부 확인
3. **실전 적용은 백테스트 검증 후**: 백테스트에서 먼저 적용/검증 → 결과 확인 후 실전 반영
