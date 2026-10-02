# llm/v2 체인

그래프는 메인 Agent(`create_agent`) 하나이고 checkpointer는 없습니다. 하위 Agent는 메인의 `ask_*` 도구로 호출됩니다.

```
before_agent: JEV 판정(요청당 1회) ─ NON_PASS → 범위 안내 AIMessage 후 end (모델·도구 호출 없음)
                                   └ PASS → model ⇄ tools (capability 로 노출된 도구 + ask_* 하위 Agent)
```

## 사용

```python
from langchain_core.messages import AIMessage, HumanMessage
from llm.v2.agent.chain import get_graph

result = get_graph().invoke({
    "messages": [HumanMessage("잠실 가요"), AIMessage("네"), HumanMessage("주차는?")],
    "context": {"stadium": "잠실야구장", "intent": "route", "origin": {"lat": 37.5, "lng": 127.0}},  # 선택
})
answer = result["messages"][-1].content
```

마지막 HumanMessage가 이번 질문입니다. 결과 `messages`에는 메인의 도구 호출·ToolMessage와 최종 답변이 붙습니다. `context`는 신뢰하지 않는 참고 데이터로만 쓰입니다. `get_graph()`는 실제 모델(`LLM_MODEL`)과 `llm/tools` 레지스트리로 한 번 조립됩니다. 테스트에서는 `build_graph(model, tools_by_name)`를 씁니다.

## 구성

| 파일 | 역할 |
|---|---|
| `agent/chain.py` | 메인 Agent 조립(`MAIN_RULES`, 모든 capability 도구 + `ask_*`), `get_graph`/`build_graph` |
| `agent/common.py` | `Decision`={allowed, capabilities}, state, 모델, 예산, `build_agent` |
| `agent/sub_agents.py` | `SPECIALISTS`, `_delegate`: 하위 Agent를 `ask_baseball`/`ask_travel_research`/`ask_place_data` 도구로 감쌈 |
| `agent/baseball_sub_agent.py` | 경기·순위·선수·규칙 + 구장 안 정보 + 야구 커뮤니티 |
| `agent/travel_sub_agent.py` | 구장 주변 맛집·카페·관광 후보 조사 |
| `agent/place_sub_agent.py` | 공개 코스, 특정 장소 확인 |
| `middleware/jev_guidelines.py` | JEV 분류(guard + capability Noul). `run_jev=True`(메인만)면 `before_agent`에서 요청당 한 번 부르고 입력 decision을 덮어씀. 하위 Agent는 state의 decision을 물려받음. 공통 페르소나·내용·근거·말투 시스템 프롬프트도 여기 있음 |
| `middleware/dynamic_tools.py` | capability → 도구 매핑과 노출. 노출되지 않은 도구 요청은 실행 직전 차단. `day_plan`(경기 전후 코스·하루 일정)만 `ask_*`와 `get_directions`를 노출함 |

## 동작

- `ask_*`는 `response_format="content_and_artifact"`입니다. content는 하위 Agent의 최종 답(실패 시 `[조회 실패] ...`)이고, artifact는 하위 대화 전체(task HumanMessage + AI/Tool)입니다. 서비스(`llm/service/chat_v2.py`)는 이 ToolMessage를 턴과 함께 같은 checkpoint thread에 저장합니다. 그래서 수정·삭제 시 함께 사라집니다.
- 스트리밍: 서비스가 `subgraphs=True`로 돌립니다. 하위 Agent 청크는 `_delegate`가 config metadata에 실은 `parent_id`(ask_* tool_call_id)로 구분합니다.
- 예산(invocation 당 model 호출 수 N): 하위 Agent는 `AGENT_MODEL_CALL_BUDGET`(기본 4), 메인은 `ORCHESTRATOR_MODEL_CALL_BUDGET`(기본 8, env 이름 호환 유지)이고 각자 독립입니다. 1..N-1번째 호출은 도구를 쓸 수 있고, N번째는 도구 없이 답합니다. N+1번째 provider 호출은 없고, 잘못된 값이면 시작 시 실패합니다. `get_directions`는 요청당 2회까지입니다. `*_RECURSION_LIMIT`는 V1 전용입니다.
- JEV는 1차 필터일 뿐이고 도구의 인증·권한 검사를 대신하지 않습니다. 분류기 예외는 그대로 올라갑니다(fail closed). JEV 호출에는 `OPENROUTER_API_KEY`가 필요합니다.

## 테스트

```
cd backend && python -m unittest llm.v2.tests.test_chain -v
```

fake model, fake tools, mocked JEV로 실제 create_agent 루프를 돌립니다. provider·DB·JEV 네트워크 호출은 없습니다.
