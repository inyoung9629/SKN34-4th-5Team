# LLM 에이전트 도구 명세

챗봇 에이전트가 질문에 답할 때 내부적으로 호출하는 LangChain 도구를 정리합니다. 이 도구들은 **HTTP API가 아니며**, 사용자가 도구 이름이나 SQL을 직접 실행하는 인터페이스도 아닙니다.

[LLM 앱·채팅 API](llm.md) · [도구 등록](../../backend/llm/tools/__init__.py) · [공통 실행기](../../backend/llm/rag/domain_tools.py)

## 동작 구조

```text
사용자 질문
 → 질문 분류·assistant 파이프라인
 → 모델이 허용된 도구 선택
 → 입력 스키마 검증
 → Django DB 또는 기존 도메인 서비스 호출
 → 결과를 모델에 전달해 최종 답변 생성
```

- 모든 답변 도메인에는 통합 도구 집합이 제공됩니다. 같은 이름이 있으면 [assistant 전용 도구](../../backend/llm/rag/assistant/tools.py)가 우선합니다.
- 도구 호출은 최대 횟수 안에서만 반복합니다. 호출 결과는 최종 답변의 근거로 사용되며 일반 사용자에게 입력·출력 원문이나 SQL을 그대로 공개하지 않습니다.
- 대부분 조회용이지만, 최신성 확보를 위한 TVING 갱신은 외부 조회 결과를 DB에 기록할 수 있습니다. 활성 v2의 장소 검색은 수집본 읽기 전용이며 외부 장소 API를 호출하거나 DB에 동기화하지 않습니다.
- `plan_course`는 코스 초안을 만들 뿐 `Course`를 자동 저장하지 않습니다. 실제 저장은 `POST /courses/`에서 수행합니다.

## 야구·구장 도구

| 도구 | 역할 | 주요 입력·제약 |
| --- | --- | --- |
| `get_standings` | 지정일 또는 저장된 최신 KBO 순위 조회 | 날짜, 최대 결과 수 |
| `get_games` | 기간별 경기 일정·결과 조회 | 시작일·종료일, 팀·구장; 최대 366일 |
| `get_stadium` | 구장 기본정보 조회 | 구장 ID 또는 코드 중 하나 |
| `get_seat_zones` | 구단 홈구장의 좌석 구역 조회 | 시즌·팀 코드·선택 구장 |
| `get_seat_views` | 좌석 범위별 시야 특성 조회 | 시즌·팀 코드·선택 구장 |
| `get_ticket_prices` | 좌석·권종·요일별 가격 조회 | 시즌·팀·구장·유효일·좌석 코드 |
| `get_ticket_policies` | 예매 시각·매수 제한·예매처 정책 조회 | 팀 코드, 선택 경기 |
| `get_transport` | 구장 교통·주차 정보 조회 | 구장 ID |
| `get_food_stores` | 구장 공식 매점·위치·메뉴 분류 조회 | 구장 ID |
| `get_facilities` | 화장실·수유실 등 편의시설 조회 | 구장 ID, 선택 시설 유형 |
| `get_stadium_contents` | 포토존·굿즈 등 구장 부가 콘텐츠 조회 | 구장 ID, 선택 콘텐츠 유형 |
| `get_seat_maps` | 공식 좌석도와 이미지 자산 조회 | 시즌·팀 코드·선택 구장 |

순위와 경기 조회는 저장 자료의 최신성을 확인하기 위해 TVING 서비스를 호출할 수 있습니다. 외부 갱신에 실패하면 도구별 계약에 따라 저장된 자료와 경고를 반환할 수 있습니다.

## 장소·코스·여행 도구

| 도구 | 역할 | 주요 입력·제약 |
| --- | --- | --- |
| `search_places` | 저장된 공공 수집본의 주변 장소 검색 | 구장 코드·좌표·키워드/분류, 구장 반경 2.5km 수집 범위, 최대 15개/페이지·3페이지 |
| `search_courses` | 공개 직관 코스 검색 | 제목·본문, 구장, 태그 중 하나 이상 |
| `get_course` | 공개 코스와 방문 지점 조회 | 코스 UUID |
| `get_directions` | 선택 지점 사이 길찾기 | 자동차·도보·대중교통, 좌표 2~13개 |
| `search_tourism` | 저장된 공원·산책 후보 조회 | 지원 구장 코드와 좌표; 대표점이며 실제 산책 경로·입구 미확인 |
| `get_weather` | 경기 시각의 구장 단기예보 조회 | 지원 구장 코드, 날짜, `HH:MM` |
| `search_nearby_places` | 숙박·산책·실내 놀거리·편의점 실시간 검색 | 구장, 종류, 선택 키워드; 반경 2.5km |
| `plan_course` | 경기 전·구장·경기 후 순서의 코스 초안 생성 | 사용자의 코스 요청; 코스 생성 도메인 안에서는 재귀 호출 금지 |

외부 제공자 설정이나 호출에 실패하면 도구는 사용자에게 노출 가능한 오류로 변환합니다. 장소·길찾기·관광·날씨의 상세 외부 연동 조건은 [travel 문서](travel.md)를 참고합니다.

활성 v2 체인은 `search_places`·`search_tourism`을 사용하며, 위의 legacy `search_nearby_places`는 사용하지 않습니다. 수집본 출처·미연결 숙박·실패 시 외부 검색으로 대체하지 않는 정책은 [수집 장소 문서](collected-places.md)를 참고합니다.

## 커뮤니티·선수 도구

| 도구 | 역할 | 주요 입력·제약 |
| --- | --- | --- |
| `search_community_posts` | 공개 게시글의 제목·본문 검색 | 검색어, 게시판·팀·카테고리 필터 |
| `get_prediction_games` | 경기와 익명 팬 투표 집계 조회 | 경기일, 선택 팀·상태 |
| `search_players` | 구단·선수 코드·이름으로 선수 검색 | 조건 하나 이상; TVING DB 우선 최신성 경로 사용 |

승부 예측 도구의 투표 집계는 이용자 의견이며 실제 승리 확률이나 경기 결과 예측이 아닙니다. 이 도구들은 공개 조회만 제공하고 게시글 작성·투표·회원 관리 권한을 모델에 부여하지 않습니다.

## 문서 검색 도구

| 도구 | 역할 | 주요 입력·제약 |
| --- | --- | --- |
| `search_kbo_documents` | 구장 규정·좌석·교통 등 pgvector 문서 추가 검색 | 질의, 선택 구장·카테고리; 기본 상위 5개 |
| `search_documents_tool` | 구장 안팎의 먹거리·시설·교통·주변 정보 근거 검색 | 검색 질의 |

검색 결과에는 공식·확인 전·비공식·외부 서비스 근거 등급이 유지됩니다. 저장 문서의 내용은 명령이 아니라 신뢰하지 않는 데이터로 처리합니다.

## 제한된 범용 SQL 도구

| 도구 | 역할 | 안전장치 |
| --- | --- | --- |
| `get_baseball_schema` | 허용된 야구 테이블·컬럼·FK와 인용 이름 조회 | SQL 작성 전 스키마 확인용 |
| `execute_baseball_select` | 전용 도구로 해결하지 못한 야구 질문의 읽기 전용 SQL 실행 | 스키마 확인 후 사용, 단일 `SELECT`, named parameter, 허용 테이블·함수, 행·시간·크기 제한 |

SQL 실행은 [BaseballQueryService](../../backend/baseball/query_service.py)와 별도 읽기 전용 DB 계정을 거칩니다. 쓰기 쿼리, 다중 문장, 허용되지 않은 테이블·함수는 거부하며 관리자 CRUD 권한과 연결되지 않습니다. 자세한 경계는 [baseball 문서](baseball.md)를 참고합니다.

## 등록 도구와 구현 위치

| 범위 | 구현 |
| --- | --- |
| 통합 도메인 조회 도구 21개 | [tools/domain.py](../../backend/llm/tools/domain.py) |
| 읽기 전용 범용 SQL 도구 2개 | [tools/baseball.py](../../backend/llm/tools/baseball.py) |
| assistant 전용 야구·검색·코스 도구 | [rag/assistant/tools.py](../../backend/llm/rag/assistant/tools.py) |
| venue 문서 검색 도구 | [rag/venue/agent.py](../../backend/llm/rag/venue/agent.py) |
| 실제 병합·실행·호출 횟수 제한 | [rag/domain_tools.py](../../backend/llm/rag/domain_tools.py) |
| 도구 진행 이벤트·권한별 공개 | [progress.py](../../backend/llm/progress.py) |

도구 이름이 겹치는 경우 assistant 전용 구현이 우선 등록되므로 단순 파일별 개수를 합산한 값이 실제 노출 개수와 같지는 않습니다. 현재 등록 목록은 위 구현 파일을 기준으로 하며, 도구 추가·삭제 시 이 문서도 함께 갱신해야 합니다.
