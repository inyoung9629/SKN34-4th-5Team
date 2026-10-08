# 채팅 계획 UI 계약

범위는 루트 작성 이동 안내와 조건 질문만이다. 선수·경기 카드와 생성 HTML은 사용하지 않는다.

V2 메인 모델은 `day_plan` capability의 `present_planning_questions` 도구로 공개 payload를 선택한다. 새 직관/야구 여행 계획은 조사 전에 이동 안내를 내고, 모르는 조건만 질문한다. 후속 질문/기존 대화/context의 조건을 다시 묻지 않도록 prompt와 모델 history에 선택 질문을 포함한다.

```json
{"offer_writer":true,"questions":[{"question":"동행은?","choices":["혼자","친구"]}]}
```

- questions 0~4개, question 1~160자, choices 2~4개, choice 1~80자·중복 금지
- backend는 검증된 도구 artifact만 SSE `planning`으로 내보낸다. 같은 payload를 `done.planning`과 저장 history assistant의 `planning`으로 투영한다. 알 수 없는 필드는 공개하지 않는다.
- frontend는 같은 경계를 검증한다. 잘못된 optional UI는 무시하고 기존 text/tools 답변을 유지한다.
- 회원 Bearer JWT 및 비회원 guest session 경로를 그대로 사용한다. private tool arguments/detail은 기존 역할 투영을 따른다.
- 실시간 `planning.offer_writer`만 20초 countdown을 시작한다. 목적지는 고정 `/routes/new`; 모델 URL은 허용하지 않는다. history/done 복원은 countdown을 시작하지 않는다.
- 즉시 이동/자동 이동은 한 번만 실행한다. 머무르기·입력·질문 선택/직접 입력·전송·중단·화면/대화/계정 변경·unmount는 취소한다. 같은 대화에서는 이동 제안을 반복하지 않는다.
- root ChatProvider가 대화·원문 요청·chat draft를 유지하며 writer draft/course는 자동 교체하지 않는다. 기존 writer의 ChatPopup으로 이어지고 요청을 다시 전송하지 않는다.
- 최신 assistant 질문 그룹만 활성화한다. 선택 변경·직접 입력 가능; 전체 그룹은 기존 user-message 전송으로 한 번 보낸다. 기존 composer draft는 보존한다. 실패는 기존 retry/text flow로 처리한다.
