# 프로젝트 문서

현재 실행 방법은 [프로젝트 README](../README.md)를 기준으로 합니다. `docs/`는 목적에 따라 아래처럼 구분합니다.

## 문서 구조

| 경로 | 내용 |
| --- | --- |
| [`api/`](api/) | 앱별 API 명세, 에이전트 도구, API 계약 |
| [`guides/`](guides/) | 환경 설정, 인덱싱, 데모 계정 등 실행 안내 |
| [`handoffs/`](handoffs/) | 프론트·백엔드 및 기능별 연동 인계 문서 |
| [`references/`](references/) | 조사 결과, 정책, 설계 판단과 마이그레이션 참고자료 |
| [`deliverables/`](deliverables/) | 과제 제출용 데이터·아키텍처·RAG·테스트 문서 |
| [`architecture/`](architecture/) | 시스템·데이터·UX 흐름 다이어그램과 HTML |
| [`test_results/`](test_results/) | 테스트 결과 원본 |
| [`postman/`](postman/) | Postman 컬렉션 |
| [`images/`](images/) | README·문서 이미지 자산 |
| [`archive/`](archive/) | 현재 상태가 아닌 과거 작업 기록과 PDF 스냅샷 |

## API와 에이전트

| 문서 | 내용 |
| --- | --- |
| [회원·인증 API](api/accounts.md) | 가입·로그인·JWT·프로필·관리자 회원 관리 |
| [채팅·RAG API](api/llm.md) | 채팅 세션·메시지·SSE·turn 확정 |
| [야구 데이터 API](api/baseball.md) | 공개 조회·관리자 CRUD·읽기 전용 SQL 경계 |
| [코스·장소 API](api/travel.md) | 코스·장소·길찾기·날씨·관광 |
| [커뮤니티 API](api/community.md) | 게시글·댓글·초안·이미지·승부예측 |
| [TVING 데이터 API](api/tving.md) | 경기·구단·선수·스냅샷 |
| [LLM 에이전트 도구](api/agent-tools.md) | 앱별 내부 도구, 입력 제약, 외부 연동과 SQL 안전장치 |
| [API 계약 참고](api/API_CONTRACTS.md) | DTO와 연동 계약 관련 참고 문서 |

## 실행·운영 안내

- [프론트엔드 → 백엔드 통합 인계](handoffs/FRONTEND_BACKEND_HANDOFF.md)
- [Python·VS Code 환경 설정](guides/환경설정_my_venv.md)
- [RAG 인덱싱 실행 가이드](guides/RAG인덱싱_실행가이드_20260910.md)
- [데모 사용자 안내](guides/DEMO_USERS.md)
- [팀 GitHub 협업 규칙](TEAM_GITHUB_RULES.md)

## 런타임 입력·도메인 자료

아래 파일은 단순 참고 문서가 아니라 일부 코드·데이터 파이프라인에서 현재 경로를 직접 사용하므로 최상위에 유지합니다.

- [`KBO_반입물품_재입장규정.json`](KBO_반입물품_재입장규정.json) — RAG 인덱싱이 읽는 10구단 구조화 자료
- [`KBO_반입물품_재입장규정.md`](KBO_반입물품_재입장규정.md) — 사람이 읽는 반입·재입장 자료
- [`기초규칙_요약본.md`](기초규칙_요약본.md) — RAG 인덱싱 입력
- [`반입물품_구단별정리.md`](반입물품_구단별정리.md) — 전처리 참고 입력
- [`포항_특별경기_예외처리.md`](포항_특별경기_예외처리.md) — `stadium_code=OTHER` 예외 규칙
- [`원정응원가이드.xlsx`](원정응원가이드.xlsx) — 원정 응원 참고 자료

## 참고자료

- [코스 장소 선정 기준](references/코스장소_선정기준_핫플정의_20260916.md)
- [먹거리·플레이스 반경 및 자동화 정책](references/먹거리_플레이스_반경및자동화_정책_20260908.md)
- [재입장 규정 검증 메모](references/재입장규정_검증메모.md)
- [구장별 공식 좌석배치도 조사](references/구장별_공식_좌석배치도_출처조사.md)
- [구장 좌석 이미지 출처 조사](references/자리어때_구장좌석이미지_출처조사.md)

## 과거 기록

`archive/` 문서는 작성 당시 상태를 보존합니다. 설치 명령, 폴더 구조와 현재 동작은 루트 README와 현행 소스·API 문서를 우선합니다.
