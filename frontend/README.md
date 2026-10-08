# KBO ROUTE 프론트엔드

흰색·파란색, 큰 제목과 야구공 배경을 사용하는 직관 루트 서비스의 프론트 프로토타입입니다. Next.js App Router, React, TypeScript로 구현했습니다.

백엔드 담당자는 API 계약, 환경변수 소유권, 로컬 저장 데이터의 이전 방법을 정리한 [프론트엔드 → 백엔드 연동 인계서](../docs/handoffs/FRONTEND_BACKEND_HANDOFF.md)를 먼저 확인하세요.

## 실행

`frontend` 폴더에서 실행합니다.

```bash
npm install
npm run dev
```

접속 주소는 `http://localhost:3000`입니다. 팀 설정에 맞춰 개발 서버를 3000번 포트로 고정했습니다. 다른 프로그램이 3000번을 사용 중이면 해당 서버를 먼저 중지해야 합니다.

팀 Docker 환경은 저장소 루트에서 `docker compose up -d --build`로 실행합니다. 이번 변경에서 한글 글꼴 패키지 `@fontsource-variable/noto-sans-kr`를 추가했으므로 의존성 설치 또는 재빌드가 필요합니다. 전체 Docker 환경 구동은 별도 확인이 필요합니다.

지도 기능을 로컬 Node로 실행할 때는 `frontend/.env.example`을 참고해 `frontend/.env.local`에 공개 JavaScript 키만 설정합니다. 팀 Docker에서는 루트 `.env.example`의 `NEXT_PUBLIC_KAKAO_MAP_KEY`, `KAKAO_REST_API_KEY`, `TOUR_API_KEY`를 사용하며 REST·관광공사 키는 Django backend에만 전달됩니다. 배포 주소도 카카오 JavaScript SDK 도메인에 등록해야 합니다.

## 화면과 현재 동작

| 주소 | 구성 |
| --- | --- |
| `/` | 챗봇 질문, 티빙 당일 경기·선발 투수·좌우 이동, 정규리그 10팀 순위, 샘플 코스 6개 |
| `/schedule` | 2026년 월·날짜별 경기 일정·결과, 투수·구장 정보, 구단 필터 |
| `/standings` | 정규리그 팀 순위 전체 12개 항목, 타율·평균자책·최근 10경기, 기록별 정렬 |
| `/routes` | 검색·9개 구장 필터·최신순·좋아요순·페이지 이동, 로딩·빈 상태 |
| `/routes/[id]` | 본문, 지도·방문 순서, 좋아요·공유, 직접 저장한 코스 수정·삭제 확인 |
| `/routes/new` | 구장 반경 2.5km 장소 탐색, 핀과 빈 지도에서 코스 추가, 완성 후 이동 시간 조회와 이름으로 저장 |
| `/community`, `/community/teams` | 자유 게시판, 전체·10개 구단 선택. 게시글 API 연결 전 |
| `/community/predictions` | 승부 예측, 전체·10개 구단 선택. 게시글 API 연결 전 |
| `/stadiums` | 데이터에 포함된 9개 구장 검색과 위치 안내 |
| `/guide` | 야구 기본 규칙, 관람 준비 체크리스트 |
| `/login`, `/signup` | 입력 폼, 입력값 확인, 소셜 로그인 버튼 |

UI/UX 가이드의 큰 항목 2~5에 맞춰 여섯 기본 화면, 반응형, 로딩·오류 상태를 구성했습니다. 기존 파랑·흰색과 상단의 KBO 로고·로그인·회원가입을 유지하고 모바일 하단 탐색을 추가했습니다. 카드 목록은 모바일 1열·태블릿 2열·PC 3열입니다. 구현 범위와 팀 연결 작업은 [UI/UX 구현 현황](docs/UIUX_PROGRESS.md)을 참고하세요.

코스·좋아요·조회 수는 현재 브라우저의 로컬 저장소에 보관됩니다. 내 코스는 내용을 복사하거나 다른 앱에 보내 공유할 수 있으며, 서버에 공개 게시글로 등록되지는 않습니다. 샘플 코스는 예시이고, 구장 사진은 실제 해당 구장의 사진이 아닌 분위기 이미지입니다. 사진 출처는 `public/images/SOURCES.md`에 있습니다.

경기 일정·선발 투수·팀·개인 순위와 팀·선수 상세는 브라우저가 `/api/v1/tving/`으로 Django에 직접 요청합니다. Django가 TVING 공개 응답을 검증하고 기존 `Team`·`Game`·`StandingHistory`와 선수 관계형 엔티티를 조건부 갱신하며, 실패 시 완전한 저장본만 `stale`로 구분해 반환합니다. Next 서버에는 수집 타이머나 로컬 snapshot writer가 없습니다. 설정과 callable 도구 계약은 [KBO 데이터 안내](docs/KBO_DATA.md)와 [TVING 도구 인계](../docs/handoffs/TVING_TOOLS_HANDOFF.md)를 참고하세요.

## 챗봇

챗봇은 회원이면 보호된 session/message API, 게스트면 DB에 쓰지 않는 임시 `/api/v1/chat/guest/` SSE를 사용합니다. 대화는 브라우저 메모리에만 있고 로그인·계정 변경 때 정리되며, 회원 Stop은 서명된 체크포인트로 받은 prefix만 저장합니다. Next.js 중계는 없습니다. 자세한 계약은 [챗봇 연결 가이드](docs/CHAT_SETUP.md)를 참고하세요.

## 실제 서비스 연결 시 남은 작업

- 회원·소셜 로그인 API, 세션과 작성자 권한 연결. 현재 로그인 성공 처리는 하지 않습니다.
- 게시글·좋아요·페이지 조회 API 연결. 현재 로컬 저장소는 `lib/routes.ts`에 모았습니다.
- 카카오 지도는 브라우저 SDK를 쓰고 장소 검색은 `/api/v1/places/search/` Django API를 직접 호출합니다. 검색 결과 저장도 백엔드가 담당합니다. 배포 시 JavaScript SDK 도메인을 등록하고 구장 경계 필터를 검증해야 합니다.
- 관광공사 장소는 `/api/v1/tourism/` Django API로 조회하며 기존 `Place` 원장과 provider 전용 OneToOne metadata를 사용합니다. 두 서버 키 모두 Next에 전달하지 않습니다.
- CKEditor 5 라이선스 설정과 이미지 업로드. 사용자가 라이선스 없이 우선 진행하기로 선택해 현재는 일반 본문 입력을 사용하며, `components/editor.tsx`에 라이선스 설정 어댑터를 준비했습니다.
- 챗봇의 팀 RAG·경기 정보·지도 데이터 연결과 답변 검증. 작성 화면의 코스 예시는 미리 작성된 내용입니다.
- 서비스 정책 문구 확정. 회원가입 화면의 정책 안내는 초안입니다.

## 검사

```bash
npm run lint
npm test
npx tsc --noEmit
npm run build
```

최종 브라우저 확인 대상은 360px·768px·1440px입니다. 결과와 외부 연결 상태는 [UI/UX 구현 현황](docs/UIUX_PROGRESS.md)에 구분해 기록합니다.
