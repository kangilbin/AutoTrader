"""
CSV 파일 기반 백테스팅 테스트 스크립트
"""
import sys
sys.path.insert(0, '/Users/apple/IdeaProjects/AutoTrader')

import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path

from app.domain.swing.backtest.strategies.single_ema_backtest_strategy import SingleEMABacktestStrategy


def load_csv_data(file_path: str) -> pd.DataFrame:
    """CSV 파일에서 주가 데이터 로드"""
    columns = [
        'ST_CODE', 'STCK_BSOP_DATE', 'STCK_OPRC', 'STCK_HGPR',
        'STCK_LWPR', 'STCK_CLPR', 'ACML_VOL', 'FRGN_NTBY_QTY',
        'REG_DT', 'MOD_DT'
    ]

    df = pd.read_csv(file_path, header=None, names=columns)
    return df


def analyze_result(result: dict, init_amount: int) -> dict:
    """백테스팅 결과 분석"""
    trades = result['trades']

    # 승패 분석
    sells = [t for t in trades if t['action'] == 'SELL']
    wins = [t for t in sells if t.get('realized_pnl', 0) > 0]
    losses = [t for t in sells if t.get('realized_pnl', 0) <= 0]

    win_rate = (len(wins) / len(sells) * 100) if sells else 0

    avg_win = sum(t['realized_pnl'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['realized_pnl'] for t in losses) / len(losses) if losses else 0
    avg_win_pct = sum(t['realized_pnl_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss_pct = sum(t['realized_pnl_pct'] for t in losses) / len(losses) if losses else 0

    # 손익비
    total_profit = sum(t['realized_pnl'] for t in wins) if wins else 0
    total_loss = abs(sum(t['realized_pnl'] for t in losses)) if losses else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')

    # MDD 계산
    capital_history = [init_amount]
    for t in trades:
        capital_history.append(t['current_capital'])

    peak = capital_history[0]
    max_dd = 0
    for c in capital_history:
        if c > peak:
            peak = c
        dd = (peak - c) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        'win_rate': win_rate,
        'wins': len(wins),
        'losses': len(losses),
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'avg_win_pct': avg_win_pct,
        'avg_loss_pct': avg_loss_pct,
        'profit_factor': profit_factor,
        'mdd': max_dd,
    }


def main():
    # 설정
    init_amount = 10_000_000
    eval_years = 2  # 2년치 백테스팅

    # CSV 파일 목록
    csv_dir = Path('/Users/apple/IdeaProjects/AutoTrader/tmp')
    csv_files = list(csv_dir.glob('*.csv'))

    print('=' * 80)
    print('단일 20EMA 전략 백테스팅 (CSV 기반, 2년)')
    print('=' * 80)

    strategy = SingleEMABacktestStrategy()
    all_results = []

    for csv_file in csv_files:
        st_code = csv_file.stem
        print(f'\n{"="*60}')
        print(f'종목: {st_code}')
        print('=' * 60)

        try:
            # 데이터 로드
            df = load_csv_data(str(csv_file))
            print(f'데이터 기간: {df["STCK_BSOP_DATE"].min()} ~ {df["STCK_BSOP_DATE"].max()}')
            print(f'총 데이터: {len(df)}일')

            # 평가 시작일 계산 (최근 2년)
            df['STCK_BSOP_DATE'] = pd.to_datetime(df['STCK_BSOP_DATE'], format='%Y%m%d')
            end_date = df['STCK_BSOP_DATE'].max()
            eval_start = end_date - relativedelta(years=eval_years)

            print(f'평가 기간: {eval_start.date()} ~ {end_date.date()} ({eval_years}년)')

            # 백테스팅 실행
            params = {
                'st_code': st_code,
                'swing_type': 'C',
                'short_term': 5,
                'medium_term': 20,
                'long_term': 60,
                'init_amount': init_amount,
                'buy_ratio': 0.5,
                'sell_ratio': 1.0,
                'eval_start': eval_start,
            }

            result = strategy.compute(df, params)
            analysis = analyze_result(result, init_amount)

            # === 디버그: 특정 날짜 구간 지표값 출력 ===
            debug_start = '2025-08-25'
            debug_end = '2025-09-05'
            debug_df = strategy._prepare_data(df.copy())
            debug_df = strategy._calculate_indicators(debug_df)
            mask = (debug_df['STCK_BSOP_DATE'] >= debug_start) & (debug_df['STCK_BSOP_DATE'] <= debug_end)
            debug_slice = debug_df.loc[mask]
            if not debug_slice.empty:
                print(f'\n[디버그] {debug_start} ~ {debug_end} 지표값')
                print('-' * 120)
                print(f'{"날짜":>12} | {"종가":>8} | {"EMA20":>8} | {"EMA120":>8} | {"괴리율":>6} | {"ADX":>6} | {"+DI":>6} | {"-DI":>6} | {"OBV_z":>6} | {"ATR":>8} | 매수조건')
                print('-' * 120)
                for idx in range(len(debug_slice)):
                    r = debug_slice.iloc[idx]
                    date_str = str(r['STCK_BSOP_DATE'])[:10]
                    close = r['STCK_CLPR']
                    ema20 = r.get('ema_20', float('nan'))
                    ema120 = r.get('ema_120', float('nan'))
                    gap = r.get('gap_ratio', float('nan'))
                    adx = r.get('adx', float('nan'))
                    plus_di = r.get('plus_di', float('nan'))
                    minus_di = r.get('minus_di', float('nan'))
                    obv_z = r.get('obv_z', float('nan'))
                    atr = r.get('atr', float('nan'))

                    # 매수 조건 체크
                    conditions = []
                    if pd.notna(ema20) and pd.notna(ema120):
                        conditions.append('하락장' if ema20 < ema120 else 'O상승장')
                    if pd.notna(ema20):
                        conditions.append('OEMA근접' if close >= ema20 * 0.995 else 'EMA먼')
                    if pd.notna(gap):
                        conditions.append('O괴리OK' if gap <= 0.05 else '괴리초과')
                    if pd.notna(adx) and pd.notna(plus_di) and pd.notna(minus_di):
                        trend_ok = plus_di > minus_di and adx > 20
                        conditions.append(f'O추세' if trend_ok else f'추세X(ADX{adx:.0f},+DI{plus_di:.0f},-DI{minus_di:.0f})')
                    if idx >= 2:
                        prev2 = debug_slice.iloc[idx-2]
                        if pd.notna(obv_z) and pd.notna(prev2.get('obv_z', float('nan'))):
                            obv_ok = obv_z > 0 and obv_z > prev2['obv_z']
                            conditions.append(f'OOBV' if obv_ok else f'OBV_X(z={obv_z:.2f})')
                    if idx >= 1:
                        prev1 = debug_slice.iloc[idx-1]
                        prev_bull = prev1['STCK_CLPR'] > debug_slice.iloc[idx-2]['STCK_CLPR'] if idx >= 2 else False
                        conditions.append('O전일양봉' if prev_bull else '전일음봉')

                    cond_str = ' | '.join(conditions)
                    print(f'{date_str:>12} | {close:>8,.0f} | {ema20:>8,.0f} | {ema120:>8,.0f} | {gap:>6.2%} | {adx:>6.1f} | {plus_di:>6.1f} | {minus_di:>6.1f} | {obv_z:>6.2f} | {atr:>8,.0f} | {cond_str}')
                print('-' * 120)
            # === 디버그 끝 ===

            # 결과 출력
            print(f'\n[성과]')
            print(f'초기 자본: {init_amount:,}원')
            print(f'최종 자본: {result["final_capital"]:,.0f}원')
            print(f'총 수익률: {result["total_return"]:+.2f}%')

            print(f'\n[거래]')
            print(f'총 거래: {result["total_trades"]}회')
            print(f'승/패: {analysis["wins"]}승 {analysis["losses"]}패')
            print(f'승률: {analysis["win_rate"]:.1f}%')

            if analysis['wins'] > 0:
                print(f'평균 이익: {analysis["avg_win"]:+,.0f}원 ({analysis["avg_win_pct"]:+.2f}%)')
            if analysis['losses'] > 0:
                print(f'평균 손실: {analysis["avg_loss"]:+,.0f}원 ({analysis["avg_loss_pct"]:+.2f}%)')

            print(f'\n[위험]')
            print(f'손익비: {analysis["profit_factor"]:.2f}')
            print(f'MDD: {analysis["mdd"]:.2f}%')

            # 거래 내역
            if result['trades']:
                print(f'\n[거래 내역]')
                print('-' * 60)
                for trade in result['trades']:
                    action = trade['action']
                    icon = 'B' if action == 'BUY' else 'S'
                    date_str = str(trade['date'])[:10]

                    if action == 'SELL':
                        pnl = trade.get('realized_pnl', 0)
                        pnl_pct = trade.get('realized_pnl_pct', 0)
                        reason = trade.get('reason', '')
                        print(f'[{icon}] {date_str} | {trade["quantity"]:>5}주 @ {trade["price"]:>8,.0f}원 | {pnl:+10,.0f}원 ({pnl_pct:+6.2f}%) | {reason}')
                    else:
                        reason = trade.get('reason', '')
                        print(f'[{icon}] {date_str} | {trade["quantity"]:>5}주 @ {trade["price"]:>8,.0f}원 | {reason}')

            all_results.append({
                'code': st_code,
                'return': result['total_return'],
                'trades': result['total_trades'],
                'win_rate': analysis['win_rate'],
                'profit_factor': analysis['profit_factor'],
                'mdd': analysis['mdd'],
            })

        except Exception as e:
            print(f'오류: {e}')
            import traceback
            traceback.print_exc()

    # 종합 통계
    if len(all_results) > 1:
        print('\n' + '=' * 80)
        print('종합 통계')
        print('=' * 80)

        avg_return = sum(r['return'] for r in all_results) / len(all_results)
        avg_win_rate = sum(r['win_rate'] for r in all_results) / len(all_results)
        avg_mdd = sum(r['mdd'] for r in all_results) / len(all_results)
        positive = len([r for r in all_results if r['return'] > 0])

        print(f'평균 수익률: {avg_return:+.2f}%')
        print(f'평균 승률: {avg_win_rate:.1f}%')
        print(f'평균 MDD: {avg_mdd:.2f}%')
        print(f'수익 종목: {positive}/{len(all_results)}개')

        best = max(all_results, key=lambda x: x['return'])
        worst = min(all_results, key=lambda x: x['return'])
        print(f'최고: {best["code"]} ({best["return"]:+.2f}%)')
        print(f'최저: {worst["code"]} ({worst["return"]:+.2f}%)')


if __name__ == '__main__':
    main()
