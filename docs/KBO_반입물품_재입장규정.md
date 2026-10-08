# KBO 10구단 반입물품·재입장 규정 — 고정형 데이터

- 반입물품 원본 기준일: 2026-09-07
- 재입장 정보 반영일: 2026-09-08 (공식 규정 시행일이 아닌 파일 수정일)
- 재입장 정보 출처: 사용자 제공 안내. 이번 보완에서는 공식 원문을 별도로 검증하지 않음 (`reentry_status=PARTIAL`, `reentry_evidence_type=UNOFFICIAL` — 프로젝트 공통 태깅 어휘 기준)
- 검증 범위: 기존 반입물품 내용과 출처 표시는 원본을 유지했으며 이번 작업에서 재검증하지 않음
- **2026-09-08 갱신**: `data/raw/구장정보.xlsx`(Policies 시트, P012, source S037=LG 트윈스 공식 홈페이지 `lgtwins.com/ticket/general`, verified_at 2026-09-03)에서 LG 홈경기 한정 "도수 8% 이하 맥주만 허용" 예외가 공식 확인되어 LG를 `CONFIRMED_BASELINE`→`CONFIRMED`로 갱신함. 기존 `반입물품_구단별정리.md` 2번(잠실 커뮤니티 기준, "도수 5% 초과 금지")과 수치가 다른데, 이번 LG 전용 공식 출처가 더 신뢰도가 높아 LG에는 8% 기준을 적용하고 두산은 갱신하지 않음(범위가 `LG_HOME_GAME`으로 한정된 자료라 두산 홈경기까지 확대 적용하지 않음)
- **2026-09-08 밤 2차 갱신**: 팀원 3차 재검증(`backlog 보충.xlsx` 신규확보데이터 No.5·6, 한화 이글스 공식 FAQ, 2026-09-05 열람)으로 HANWHA 음료·음식물 반입 조건을 더 구체화함 — 음료는 기존 값(PET 1개 또는 캔 2개, 총량 1L 이하)과 정확히 일치해 교차검증만 하고 "유리병·1L 초과 반입 금지" 명시 문구를 `beverage_note`로 추가, 음식물은 기존 `hot_or_strong_smell_food=RESTRICTABLE` 한 줄뿐이던 것을 `food_carry_in_detail` 필드로 상세화(미리 잘라온 과일 허용, 던질 수 있는 과일류 제한 등). `status=CONFIRMED`는 변경 없음(이미 공식 FAQ 기반 CONFIRMED였음)
- **2026-09-28 갱신**: KBO SAFE 캠페인 공식 원문(반입금지물 표·FAQ 4)과 대조해 공통 규정 키를 갱신함 — `alcohol`(원칙은 알코올류 반입 지양, 미개봉 1L 이하 PET·캔은 구장 사정에 따라 허용될 수 있음, 병 소주 불가, 구단 예외 우선), `glass_bottle`·`beverage`·`food`·`bulky_items`·`dangerous_items`·`noisy_cheering_tools` 보강, `whole_throwable_fruit`·`banner` 추가. 이전 표에 없던 `alcohol` 행도 JSON과 맞춤. 구단별 값·재입장 정보는 변경 없음
- 목적: 챗봇/RAG에서 실시간 크롤링 없이 고정 규정으로 조회
- 반입물품 원칙: **KBO SAFE 공통규정이 기본값**이며, 구단/구장 공식 예외가 확인된 경우에만 `team_exception`에 추가
- 재입장 원칙: 사용자 제공 내용을 별도 `reentry_*` 키로 관리. 반입물품 공통규정을 재입장 절차·시간·외부음식 추가 반입에 자동 적용하지 않음
- 공통 공식 출처: https://www.koreabaseball.com/Kbo/BusinessAndEvent/Safe.aspx

## 공통 규정 키 (반입물품)

| key | value |
|---|---|
| bag | 1인당 45×45×20cm 이하 가방 1개 |
| shopping_bag | 1인당 30×50×12cm 이하 쇼핑백류(비닐백·에코백 등) 1개 |
| bulky_items | 정해진 가방·쇼핑백 외의 가방, 상자, 아이스박스, 돗자리, 휴대용 의자, 휴대용 간이테이블 반입 불가(구장별 잔디석 등 구단이 특별히 허용하는 구역은 예외). 좌석·컵홀더 등 경기장 시설물에 거치하는 방식의 물품도 제한. 드라이아이스·얼음 등 관람환경을 저해하는 품목은 크기와 관계없이 제한될 수 있음. 풍선, 드론, 킥보드 등 정상적이고 안전한 관람에 방해가 될 수 있는 물품도 구단 판단에 따라 제한. 유모차는 통행로를 방해할 수 있어 구장 운영에 따라 반입이 제한될 수 있음 |
| glass_bottle | 모든 유리병 반입 불가(병 소주·병맥주 등 병에 담긴 주류·음료 포함) |
| frozen_water | 반입 불가 |
| alcohol | KBO는 경기장 내 일체의 알코올류 반입을 지양한다. 단, 미개봉 상태의 총량 1L 이하 PET·알루미늄 캔은 구장 사정에 따라 반입이 허용될 수 있다(1인당 총량 1L 이하, PET 1개 또는 캔 2개). 허용이 기본값이 아니므로 소주 등 주종별 가능 여부는 방문 구단 규정을 확인해야 한다. 유리병(병 소주 포함)과 1L 초과 용기는 반입 불가. 막걸리 등 향이 강한 주류는 구단 정책에 따라 제한될 수 있다. 구단별 예외가 있으면 우선한다(예: LG 홈경기는 도수 8% 이하 맥주만). 경기장 내 주류 구매는 1인당 한 경기 최대 2L로 제한되고 8회말 종료 후 판매가 중단될 수 있으며, 지나친 알코올 섭취자는 경고 후 입장거부 또는 퇴장 조치될 수 있다 |
| beverage | 미개봉 음료에 한해 1인당 1L 이하 PET 1개 또는 캔 2개까지(총량 1L 이하). 유아용 젖병, 종이팩에 담긴 우유·주스, 의약품은 반입 가능 |
| food | 반입금지 품목이 아니고 허용 소지품 크기 범위 내이면 가능. 허용 크기를 넘거나 과도한 양이면 제한될 수 있음. 유아·산모·노약자·장애인에게 관람에 꼭 필요한 물품(필요량의 분유·이유식 등)은 확인되면 제재하지 않음 |
| hot_or_strong_smell_food | 뜨거운 국물·강한 냄새 등 타인 관람을 방해할 수 있는 음식은 제한 가능 |
| whole_throwable_fruit | 통과일 등 단단하여 다칠 위험이 있는 음식은 반입 제한(미리 잘라 온 경우 반입 가능) |
| dangerous_items | 총·도검류 등 무기류, 폭죽 등 화기·화약류, 부탄가스 등 인화성 물질, 마약류·생화학물질·유독성 물질, 매우 단단한 물질, 칼·가위 등 위해를 가할 수 있는 도구, 깃발·막대·삼각대 등 타격용으로 쓰일 위험이 있는 도구(재질 불문), 가스버너 등 취사도구 반입 불가. 투척 가능성이 있는 물품과 레이저 포인터도 제한. 이 외에도 안전이나 쾌적한 관람에 지장을 줄 물품은 구단·안전요원이 현장에서 판단해 제한할 수 있음 |
| noisy_cheering_tools | 앰프, 대북, 꽹과리, 부부젤라·에어혼 같은 모든 나팔류 등 소음이 심한 응원도구 반입 제한 |
| banner | 구단과 협의되지 않은 현수막 등 모든 표현물과 상업적 행위를 위한 물품은 반입 제한 |
| pet | 반입 제한(보조견 등 별도 법정 예외는 현장 기준 적용) |

## 10구단 고정값 (반입물품 원본)

| team_code | 구단 | 홈구장 | policy_basis | team_exception | status | evidence_type |
|---|---|---|---|---|---|---|
| LG | LG 트윈스 | 잠실야구장 | KBO SAFE 공통규정 + LG 공식 티켓·입장 안내 | **주류: 도수 8% 이하 맥주만 허용** (LG 공식 안내로 확인, 2026-09-08 발견·반영). 그 외 반입물품은 공통규정 적용 | CONFIRMED | OFFICIAL |
| DOOSAN | 두산 베어스 | 잠실야구장 | KBO SAFE 공통규정 | 공식 구단 고유 예외 미확인 → 공통규정 적용 | CONFIRMED_BASELINE | OFFICIAL |
| KIWOOM | 키움 히어로즈 | 고척스카이돔 | KBO SAFE + 키움 공식 Q&A | 최초 입장 외부음식 가능. **재입장 시 외부음식 추가 반입 제한.** 주류는 공식 Q&A의 현행 기준을 우선 적용 | CONFIRMED | OFFICIAL |
| SSG | SSG 랜더스 | 인천 SSG랜더스필드 | KBO SAFE 공통규정 | 팀 고유 세부 규정은 공식 원문 미확인 → 공통규정 적용 | CONFIRMED_BASELINE | OFFICIAL |
| KT | KT 위즈 | 수원 KT위즈파크 | KBO SAFE 공통규정 | **기존 미완성 2구단 중 1곳.** 2026-09-07 현재 팀 공식 공개자료에서 별도 예외를 확인하지 못함 → 공통규정 고정 적용 | CONFIRMED_BASELINE | OFFICIAL |
| HANWHA | 한화 이글스 | 대전 한화생명 볼파크 | 한화 공식 FAQ + KBO SAFE | 가방/쇼핑백 규격, 아이스박스·대형가방 제한, 미개봉 음료 1L 총량 기준(유리병·1L 초과 반입 금지 명시)을 공식 FAQ에서 재확인. 음식물은 반입금지 품목·소지품 크기 범위 내 가능하되 뜨거운 국물류·강한 냄새 음식·던질 수 있는 과일류는 제한, 미리 잘라온 과일은 허용(2026-09-08 밤 2차 갱신, `backlog 보충.xlsx` 신규확보데이터 No.5·6) | CONFIRMED | OFFICIAL |
| SAMSUNG | 삼성 라이온즈 | 대구 삼성 라이온즈파크 | KBO SAFE + 삼성 공식 안내 | 캐리어·아이스박스·의자·유리병 반입 금지 안내 확인. 그 외 항목은 KBO 공통규정 적용 | CONFIRMED | OFFICIAL |
| KIA | KIA 타이거즈 | 광주-기아 챔피언스필드 | KBO SAFE + KIA 공식 FAQ | 막걸리·홍어·마라류 등 향이 강한 음식, 국물 포함 음식 등 구단 안내상 추가 제한 가능. 과일은 잘라온 경우 허용 안내 | CONFIRMED | OFFICIAL |
| LOTTE | 롯데 자이언츠 | 사직야구장 | KBO SAFE 공통규정 | 롯데 공식 홈페이지에서 KBO 안전가이드라인을 안내하므로 공통규정을 구장 기준으로 사용 | CONFIRMED | OFFICIAL |
| NC | NC 다이노스 | 창원 NC파크 | NC 공식 KBO 안전가이드라인 안내 + KBO SAFE | **기존 미완성 2구단 중 1곳.** NC 공식 공지에서 전 KBO 구장 안전정책 적용을 안내. 별도 팀 고유 예외는 현재 공식 확인되지 않아 공통규정 고정 적용 | CONFIRMED_BASELINE | OFFICIAL |

## 재입장 정보의 출처와 적용 범위

아래 내용은 사용자가 제공한 안내를 구조화한 것이며, 공식 규정으로 별도 확인한 자료가 아니다. 위 반입물품 표의 `OFFICIAL`, 팀별 기존 `status`, 기존 `source`를 이번 재입장 안내 전체의 검증 근거로 사용하지 않는다. 재입장 안내의 상태와 출처는 `reentry_status`, `reentry_source`를 사용한다.

### 사용자 제공 원문

> 재입장의 경우 [잠실,사직]은 실물 티켓, 앱 티켓을 게이트 출입시 매번 확인 / [인천 랜더스 필드, 대구 라이온즈 파크, 광주 챔피언스 필드,한화 볼파크, 창원 NC파크,수원 위즈파크, 고척] 은 게이트를 나갈때 전용 게이트 안 쪽에 있는 요원에게 손등 도장 및 팔찌를 요청해야함. 그외 [고척]은 재입장시 외부 음식 추가 반입이 금지됨 [수원 위즈파크]는 재입장 전용 대기줄이 따로 존재하며 [대전,창원 등 최신/지방 구장]은 8회초~말 이후 재입장 전면 통제될수도 있음

### 구조화 시 적용한 범위

- 잠실 안내는 같은 구장을 사용하는 LG·DOOSAN 두 블록에 동일하게 반영했다. 사직은 LOTTE에 반영했다.
- 나머지 명시된 7개 구장은 퇴장할 때 전용 게이트 안쪽 요원에게 손등 도장 및 팔찌를 요청하는 절차로 기록했다. 도장·팔찌의 대체 가능 여부나 세부 사용 방법은 제공되지 않았으므로 덧붙이지 않았다.
- 고척의 `reentry_external_food=N`은 원본 값을 유지했다. 다른 구장에는 해당 정보가 제공되지 않았으므로 `UNKNOWN`으로 기록했다. 일반 외부음식 반입 규정으로 재입장 시 추가 반입 가능 여부를 대신 판단하지 않는다.
- 수원의 재입장 전용 대기줄은 `Y`로 기록했다. 다른 구장은 대기줄 정보가 없어 `UNKNOWN`이며, 대기줄이 없다는 뜻이 아니다. 전용 게이트와 전용 대기줄은 서로 다른 항목이다.
- 후반 이닝 통제는 이름이 명시된 대전(HANWHA)·창원(NC)에만 `POSSIBLE`, `8회초~말 이후`로 반영했다. “등 최신/지방 구장”이라는 원문의 범위는 보존하되, 추가 대상 구장이 특정되지 않아 다른 구장으로 임의 확대하지 않았다. `8회초부터 항상 금지`, `8회말 종료 후 금지` 등으로 시점을 확정하지 않는다.

## 재입장 공통 키 정의

모든 구단에 아래 재입장 키를 동일하게 제공한다. `UNKNOWN`은 “제공 자료에 정보 없음”, `N`은 “금지”, `Y`는 “있음”, `POSSIBLE`은 “통제 가능성”이다. 기존 반입물품 키는 원본을 유지하므로 반입물품 키의 종류까지 모든 구단이 같지는 않다.

| key | value 형식 | 의미 |
|---|---|---|
| reentry_method | TICKET_CHECK / HAND_STAMP_AND_WRISTBAND_REQUEST | 재입장 절차 유형. 티켓 확인형 또는 퇴장 시 손등 도장 및 팔찌 요청형 |
| reentry_procedure | 안내 문장 | 사용자가 제공한 게이트 출입·퇴장 절차 |
| reentry_external_food | N / UNKNOWN | 재입장 시 외부음식 추가 반입. N은 금지, UNKNOWN은 제공 자료에 정보 없음 |
| reentry_external_food_note | 안내 문장 | 외부음식 추가 반입의 제한 내용 또는 정보 미제공 안내 |
| reentry_dedicated_queue | Y / UNKNOWN | 재입장 전용 대기줄. Y는 있음, UNKNOWN은 정보 없음이며 대기줄이 없다는 뜻이 아님 |
| reentry_late_inning_restriction | POSSIBLE / UNKNOWN | 후반 이닝 재입장 통제 가능성. POSSIBLE은 조건부 통제 가능이며 항상 금지라는 뜻이 아님 |
| reentry_late_inning_timing | 8회초~말 이후 / UNKNOWN | 사용자가 제시한 시간 범위를 그대로 보존. 정확한 통제 개시 시점을 확정하지 않음 |
| reentry_late_inning_note | 안내 문장 | 통제 가능성 및 적용 대상의 불확실성 |
| reentry_status | PARTIAL | 프로젝트 공통 `status` 어휘(CONFIRMED/PARTIAL/OPEN/RECHECK/CONFIRMED_BASELINE/HISTORICAL_ONLY) 중 하나. 재입장 안내는 사용자 제공 자료이며 공식 원문을 별도로 검증하지 않았으므로 PARTIAL로 표기 |
| reentry_evidence_type | UNOFFICIAL | 프로젝트 공통 `evidence_type` 어휘(OFFICIAL/DERIVED/THIRD_PARTY_API/UNOFFICIAL) 중 하나. 사용자 제공·비공식 출처이므로 UNOFFICIAL로 표기 |
| reentry_source | USER_PROVIDED | 이번 재입장 보완 정보의 출처. 기존 반입물품 source와 별도 |
| reentry_source_url | UNKNOWN | 사용자 제공 재입장 안내에는 원문 URL이 제시되지 않음 |
| reentry_updated_at | YYYY-MM-DD | 파일에 재입장 정보를 반영한 날짜. 공식 규정 시행일이 아님 |

**참고**: 반입물품 쪽 `status`/`evidence_type`와 이름이 겹치지 않도록 재입장 쪽은 `reentry_status`/`reentry_evidence_type`라는 별도 키 이름을 쓴다(같은 코드블록 안에 두 값을 같은 이름으로 두면 프로그램이 한 줄씩 읽을 때 나중 값이 앞의 값을 덮어써서 반입물품 status가 유실된다). 다만 **값 자체는 프로젝트 전체가 쓰는 공통 어휘를 그대로 재사용**한다 — 필드 이름은 데이터 종류마다 달라도, 값은 항상 정해진 같은 단어 집합 중 하나이므로 필터링 로직을 일관되게 짤 수 있다.

## 10구단 재입장 요약 (사용자 제공)

| team_code | 홈구장 | 게이트 출입·퇴장 절차 | 재입장 시 외부음식 추가 반입 | 재입장 전용 대기줄 | 후반 이닝 통제 |
|---|---|---|---|---|---|
| LG | 잠실야구장 | 게이트 출입 시 실물 티켓·앱 티켓 매번 확인 | 정보 없음 | 정보 없음 | 개별 적용 여부 미확인 |
| DOOSAN | 잠실야구장 | 게이트 출입 시 실물 티켓·앱 티켓 매번 확인 | 정보 없음 | 정보 없음 | 개별 적용 여부 미확인 |
| KIWOOM | 고척스카이돔 | 퇴장 시 전용 게이트 안쪽 요원에게 손등 도장 및 팔찌 요청 | 금지 | 정보 없음 | 개별 적용 여부 미확인 |
| SSG | 인천 SSG랜더스필드 | 퇴장 시 전용 게이트 안쪽 요원에게 손등 도장 및 팔찌 요청 | 정보 없음 | 정보 없음 | 개별 적용 여부 미확인 |
| KT | 수원 KT위즈파크 | 퇴장 시 전용 게이트 안쪽 요원에게 손등 도장 및 팔찌 요청 | 정보 없음 | 별도 대기줄 있음 | 개별 적용 여부 미확인 |
| HANWHA | 대전 한화생명 볼파크 | 퇴장 시 전용 게이트 안쪽 요원에게 손등 도장 및 팔찌 요청 | 정보 없음 | 정보 없음 | 8회초~말 이후 전면 통제 가능; 정확한 시점·조건 미확인 |
| SAMSUNG | 대구 삼성 라이온즈파크 | 퇴장 시 전용 게이트 안쪽 요원에게 손등 도장 및 팔찌 요청 | 정보 없음 | 정보 없음 | 개별 적용 여부 미확인 |
| KIA | 광주-기아 챔피언스필드 | 퇴장 시 전용 게이트 안쪽 요원에게 손등 도장 및 팔찌 요청 | 정보 없음 | 정보 없음 | 개별 적용 여부 미확인 |
| LOTTE | 사직야구장 | 게이트 출입 시 실물 티켓·앱 티켓 매번 확인 | 정보 없음 | 정보 없음 | 개별 적용 여부 미확인 |
| NC | 창원 NC파크 | 퇴장 시 전용 게이트 안쪽 요원에게 손등 도장 및 팔찌 요청 | 정보 없음 | 정보 없음 | 8회초~말 이후 전면 통제 가능; 정확한 시점·조건 미확인 |

## 팀별 키값

### LG
```text
team_code=LG
stadium=잠실야구장
carry_in_policy=KBO_SAFE_COMMON+TEAM_OFFICIAL_FAQ
team_exception=주류는 도수 8% 이하 맥주만 허용(그 외 주류 제한). 그 외 반입물품은 공통규정 적용
alcohol=도수 8% 이하 맥주만 허용 안내
status=CONFIRMED
source=https://www.lgtwins.com/ticket/general
reentry_method=TICKET_CHECK
reentry_procedure=게이트 출입 시 실물 티켓·앱 티켓을 매번 확인
reentry_external_food=UNKNOWN
reentry_external_food_note=제공된 재입장 자료에는 외부음식 추가 반입 허용·금지 정보가 없음
reentry_dedicated_queue=UNKNOWN
reentry_late_inning_restriction=UNKNOWN
reentry_late_inning_timing=UNKNOWN
reentry_late_inning_note=이 구장은 후반 이닝 통제 대상으로 개별 명시되지 않음. 사용자 안내의 최신/지방 구장 등 범위에 포함되는지는 미확인
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

### DOOSAN
```text
team_code=DOOSAN
stadium=잠실야구장
carry_in_policy=KBO_SAFE_COMMON
team_exception=NONE_CONFIRMED
status=CONFIRMED_BASELINE
reentry_method=TICKET_CHECK
reentry_procedure=게이트 출입 시 실물 티켓·앱 티켓을 매번 확인
reentry_external_food=UNKNOWN
reentry_external_food_note=제공된 재입장 자료에는 외부음식 추가 반입 허용·금지 정보가 없음
reentry_dedicated_queue=UNKNOWN
reentry_late_inning_restriction=UNKNOWN
reentry_late_inning_timing=UNKNOWN
reentry_late_inning_note=이 구장은 후반 이닝 통제 대상으로 개별 명시되지 않음. 사용자 안내의 최신/지방 구장 등 범위에 포함되는지는 미확인
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

### KIWOOM
```text
team_code=KIWOOM
stadium=고척스카이돔
carry_in_policy=KBO_SAFE_COMMON_WITH_TEAM_EXCEPTION
first_entry_external_food=Y
reentry_external_food=N
status=CONFIRMED
source=https://heroesbaseball.co.kr/fans/qna/list.do
reentry_method=HAND_STAMP_AND_WRISTBAND_REQUEST
reentry_procedure=게이트를 나갈 때 전용 게이트 안쪽에 있는 요원에게 손등 도장 및 팔찌 요청
reentry_external_food_note=재입장 시 외부음식 추가 반입 금지
reentry_dedicated_queue=UNKNOWN
reentry_late_inning_restriction=UNKNOWN
reentry_late_inning_timing=UNKNOWN
reentry_late_inning_note=이 구장은 후반 이닝 통제 대상으로 개별 명시되지 않음. 사용자 안내의 최신/지방 구장 등 범위에 포함되는지는 미확인
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

### SSG
```text
team_code=SSG
stadium=인천 SSG랜더스필드
carry_in_policy=KBO_SAFE_COMMON
team_exception=NONE_OFFICIALLY_CONFIRMED
status=CONFIRMED_BASELINE
reentry_method=HAND_STAMP_AND_WRISTBAND_REQUEST
reentry_procedure=게이트를 나갈 때 전용 게이트 안쪽에 있는 요원에게 손등 도장 및 팔찌 요청
reentry_external_food=UNKNOWN
reentry_external_food_note=제공된 재입장 자료에는 외부음식 추가 반입 허용·금지 정보가 없음
reentry_dedicated_queue=UNKNOWN
reentry_late_inning_restriction=UNKNOWN
reentry_late_inning_timing=UNKNOWN
reentry_late_inning_note=이 구장은 후반 이닝 통제 대상으로 개별 명시되지 않음. 사용자 안내의 최신/지방 구장 등 범위에 포함되는지는 미확인
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

### KT
```text
team_code=KT
stadium=수원 KT위즈파크
carry_in_policy=KBO_SAFE_COMMON
team_exception=NONE_OFFICIALLY_CONFIRMED
status=CONFIRMED_BASELINE
note=기존 반입물품 MD의 미완성 항목을 공통 공식규정으로 고정 보완
reentry_method=HAND_STAMP_AND_WRISTBAND_REQUEST
reentry_procedure=게이트를 나갈 때 전용 게이트 안쪽에 있는 요원에게 손등 도장 및 팔찌 요청
reentry_external_food=UNKNOWN
reentry_external_food_note=제공된 재입장 자료에는 외부음식 추가 반입 허용·금지 정보가 없음
reentry_dedicated_queue=Y
reentry_late_inning_restriction=UNKNOWN
reentry_late_inning_timing=UNKNOWN
reentry_late_inning_note=이 구장은 후반 이닝 통제 대상으로 개별 명시되지 않음. 사용자 안내의 최신/지방 구장 등 범위에 포함되는지는 미확인
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

### HANWHA
```text
team_code=HANWHA
stadium=대전 한화생명 볼파크
carry_in_policy=TEAM_OFFICIAL_FAQ
bag=45x45x20cm 이하 1개
shopping_bag=30x50x12cm 이하 1개
icebox=N
large_bag=N
beverage=미개봉 PET 1개 또는 캔 2개, 총량 1L 이하
beverage_note=유리병 반입 금지, 총 반입 용량 1L 초과 금지 (한화 이글스 공식 FAQ 명시, 2026-09-08 밤 2차 갱신)
hot_or_strong_smell_food=RESTRICTABLE
food_carry_in_detail=반입금지 품목 외 음식물은 소지품 크기·개수 허용 범위 내 반입 가능. 뜨거운 국물류·냄새가 강한 음식·던질 수 있는 과일류 제한. 미리 잘라온 과일은 가능 (한화 이글스 공식 FAQ, 2026-09-08 밤 2차 갱신)
status=CONFIRMED
source=https://www.hanwhaeagles.co.kr/ETC/CU/ETCCUCUI01.do?kind=FQ2&length=10&search=&start=0
reentry_method=HAND_STAMP_AND_WRISTBAND_REQUEST
reentry_procedure=게이트를 나갈 때 전용 게이트 안쪽에 있는 요원에게 손등 도장 및 팔찌 요청
reentry_external_food=UNKNOWN
reentry_external_food_note=제공된 재입장 자료에는 외부음식 추가 반입 허용·금지 정보가 없음
reentry_dedicated_queue=UNKNOWN
reentry_late_inning_restriction=POSSIBLE
reentry_late_inning_timing=8회초~말 이후
reentry_late_inning_note=사용자 안내상 대전·창원 등 최신/지방 구장은 8회초~말 이후 재입장이 전면 통제될 수도 있음. 정확한 시점과 적용 조건은 미제공이며 현장 확인 필요
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

### SAMSUNG
```text
team_code=SAMSUNG
stadium=대구 삼성 라이온즈파크
carry_in_policy=KBO_SAFE_COMMON_WITH_TEAM_EXCEPTION
carrier=N
icebox=N
portable_chair=N
glass_bottle=N
status=CONFIRMED
source_note=삼성 라이온즈 공식 SNS 고객센터 이용안내
reentry_method=HAND_STAMP_AND_WRISTBAND_REQUEST
reentry_procedure=게이트를 나갈 때 전용 게이트 안쪽에 있는 요원에게 손등 도장 및 팔찌 요청
reentry_external_food=UNKNOWN
reentry_external_food_note=제공된 재입장 자료에는 외부음식 추가 반입 허용·금지 정보가 없음
reentry_dedicated_queue=UNKNOWN
reentry_late_inning_restriction=UNKNOWN
reentry_late_inning_timing=UNKNOWN
reentry_late_inning_note=이 구장은 후반 이닝 통제 대상으로 개별 명시되지 않음. 사용자 안내의 최신/지방 구장 등 범위에 포함되는지는 미확인
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

### KIA
```text
team_code=KIA
stadium=광주-기아 챔피언스필드
carry_in_policy=KBO_SAFE_COMMON_WITH_TEAM_EXCEPTION
strong_smell_food=RESTRICTED_OR_RESTRICTABLE
soup_food=RESTRICTED_OR_RESTRICTABLE
whole_throwable_fruit=RESTRICTED
cut_fruit=Y
status=CONFIRMED
source_note=KIA 타이거즈 공식 경기장 이용 FAQ
reentry_method=HAND_STAMP_AND_WRISTBAND_REQUEST
reentry_procedure=게이트를 나갈 때 전용 게이트 안쪽에 있는 요원에게 손등 도장 및 팔찌 요청
reentry_external_food=UNKNOWN
reentry_external_food_note=제공된 재입장 자료에는 외부음식 추가 반입 허용·금지 정보가 없음
reentry_dedicated_queue=UNKNOWN
reentry_late_inning_restriction=UNKNOWN
reentry_late_inning_timing=UNKNOWN
reentry_late_inning_note=이 구장은 후반 이닝 통제 대상으로 개별 명시되지 않음. 사용자 안내의 최신/지방 구장 등 범위에 포함되는지는 미확인
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

### LOTTE
```text
team_code=LOTTE
stadium=사직야구장
carry_in_policy=KBO_SAFE_COMMON
team_exception=NONE_CONFIRMED
status=CONFIRMED
reentry_method=TICKET_CHECK
reentry_procedure=게이트 출입 시 실물 티켓·앱 티켓을 매번 확인
reentry_external_food=UNKNOWN
reentry_external_food_note=제공된 재입장 자료에는 외부음식 추가 반입 허용·금지 정보가 없음
reentry_dedicated_queue=UNKNOWN
reentry_late_inning_restriction=UNKNOWN
reentry_late_inning_timing=UNKNOWN
reentry_late_inning_note=이 구장은 후반 이닝 통제 대상으로 개별 명시되지 않음. 사용자 안내의 최신/지방 구장 등 범위에 포함되는지는 미확인
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

### NC
```text
team_code=NC
stadium=창원 NC파크
carry_in_policy=KBO_SAFE_COMMON
team_exception=NONE_OFFICIALLY_CONFIRMED
status=CONFIRMED_BASELINE
source=https://www.ncdinos.com/dinos/notice/view.do?seq=39560
note=기존 반입물품 MD의 미완성 항목을 NC 공식 KBO 안전정책 공지 근거로 고정 보완
reentry_method=HAND_STAMP_AND_WRISTBAND_REQUEST
reentry_procedure=게이트를 나갈 때 전용 게이트 안쪽에 있는 요원에게 손등 도장 및 팔찌 요청
reentry_external_food=UNKNOWN
reentry_external_food_note=제공된 재입장 자료에는 외부음식 추가 반입 허용·금지 정보가 없음
reentry_dedicated_queue=UNKNOWN
reentry_late_inning_restriction=POSSIBLE
reentry_late_inning_timing=8회초~말 이후
reentry_late_inning_note=사용자 안내상 대전·창원 등 최신/지방 구장은 8회초~말 이후 재입장이 전면 통제될 수도 있음. 정확한 시점과 적용 조건은 미제공이며 현장 확인 필요
reentry_status=PARTIAL
reentry_evidence_type=UNOFFICIAL
reentry_source=USER_PROVIDED
reentry_source_url=UNKNOWN
reentry_updated_at=2026-09-08
```

## 챗봇 응답 규칙

### 반입물품 (기존 규칙)

1. 구단별 `team_exception`이 있으면 공통규정보다 우선한다.
2. `CONFIRMED_BASELINE`은 "KBO 공통 안전규정 기준"이라고 답한다.
3. 공식 예외가 없는 구단에 비공식 블로그의 세부 수치를 덧붙이지 않는다.
4. 구장별 특별석(잔디석 등)은 별도 허용 규정이 있을 수 있으므로 질문이 특정 좌석까지 포함하면 좌석 규정을 추가 확인한다.


### 재입장 (이번 보완)

1. 재입장 질문에는 해당 구단의 `reentry_*` 키를 사용한다. 재입장 절차와 반입물품 규정을 별개로 다룬다.
2. `reentry_method=TICKET_CHECK`이면 “게이트 출입 시 실물 티켓·앱 티켓을 매번 확인하는 방식으로 안내되어 있습니다”라고 답한다. 티켓 종류를 둘 다 소지해야 한다고 해석하지 않는다.
3. `reentry_method=HAND_STAMP_AND_WRISTBAND_REQUEST`이면 “게이트를 나갈 때 전용 게이트 안쪽 요원에게 손등 도장 및 팔찌를 요청하세요”라고 답한다. 제공되지 않은 도장·팔찌 대체 가능 여부나 추가 절차는 만들지 않는다.
4. 고척의 `reentry_external_food=N`은 “재입장 시 외부음식 추가 반입 금지”를 뜻하며, 재입장 자체가 금지라는 뜻이 아니다.
5. 수원의 `reentry_dedicated_queue=Y`이면 재입장 전용 대기줄이 별도로 있다는 점을 안내한다. 다른 구장의 `UNKNOWN`을 “대기줄 없음”으로 답하지 않는다.
6. 대전·창원의 `reentry_late_inning_restriction=POSSIBLE`이면 “8회초~말 이후 재입장이 전면 통제될 수 있으므로 퇴장 전에 현장 요원에게 확인하세요”라고 안내한다. 특정 시점부터 항상 금지라고 단정하지 않는다. 다른 구장은 원문의 “등 최신/지방 구장”에 포함되는지 미확인이다.
7. `UNKNOWN`은 제공 자료로는 알 수 없다고 답한다. 허용·금지 또는 없음으로 임의 치환하지 않는다.
8. 이번에 추가한 재입장 절차·대기줄·후반 이닝 안내는 `reentry_status=PARTIAL`, `reentry_evidence_type=UNOFFICIAL`인 사용자 제공 정보다. 공식 확인 여부를 묻는 경우 "비공식 정보이며 현장 상황에 따라 다를 수 있습니다" 문구를 붙인다. 기존 반입물품 쪽 `status=CONFIRMED`나 `source`를 신규 재입장 안내의 공식 근거로 인용하지 않는다.

## 프로그램에서 읽을 때의 주의사항

각 팀의 `text` 코드 블록을 읽을 때 `키=값`은 첫 번째 등호에서만 분리한다. 출처 URL에는 등호가 포함될 수 있으므로 `key, value = line.split("=", 1)` 방식으로 처리한다. 일부 구단에 없는 기존 반입물품 키를 모든 구단의 필수값으로 가정하지 않는다.

동봉 JSON은 MD의 구단별 키값과 같은 내용을 `teams` 객체에 담았다. 예를 들어 `teams["LG"]["reentry_procedure"]`로 조회한다. `carry_in_common_rules`는 원본 반입물품 공통규정이며, 구단별 반입물품 예외나 재입장 규정으로 자동 병합해 둔 값이 아니다.