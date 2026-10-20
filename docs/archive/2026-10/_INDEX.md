# Archive Index - 2026-10

| Feature | Archived | Match Rate | Documents |
|---------|----------|-----------|-----------|
| stock-review | 2026-10-21 | N/A (설계 문서 없음) | report |

> **stock-review** — Plan/Design 문서 없이 채팅 합의 계획으로 진행했다. Gap 분석 대신
> 코드 리뷰 2회(백엔드·프론트 각 10건 → 17건 수정), 테스트 201개, KIS·Gemini 실호출, HTTP E2E로 검증했다.

## ⚠️ 후속 항목 (배포 전 권장)
- **stock-review**: 운영 환경변수 `GEMINI_API_KEY` 설정 후 백엔드(AutoTrader `1244434`)·프론트(AutotradeMobile `460bbff`)를 함께 배포. DB 마이그레이션 없음.
- 리뷰 수정 이후 실기기 재확인 미수행 — 특히 결과 화면 '스윙 등록하기' → 등록 → 뒤로가기 흐름(`router.navigate` 전환), 로그아웃 후 헤더 상태.
- Gemini 무료 등급은 응답 60~130초·수시 503, 입력 데이터가 Google 제품 개선에 사용된다 (계좌·보유 정보는 넣지 않음).
- 미사용이 된 Google OAuth 토큰 저장(`USER.GOOGLE_ACCESS_TOKEN` 등, `/oauth/google/token`, 프론트 `token_expired` 처리) 정리는 별도 작업.
