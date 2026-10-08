"""[venue] 구장 안팎 도메인 — 먹거리·편의시설·교통·포토존·주변 맛집 (담당: 현준)

현준 test.py(LangChain Agent + search_documents_tool)를 Django 로 옮긴 것.
흐름: 구장 슬롯(별칭 사전·직전 대화·프론트 힌트) → 되묻기 가드 → Agent(create_agent)가 search_documents_tool 호출 여부 판단
      → 도구 안에서 검색어 변환(LLM) → 벡터 검색(구장·카테고리·안/밖 필터) → 부족하면 키워드 fallback → 답변 생성
디스패처와의 약속: answer(question, history, hint_stadium) -> {"answer", "sources", "route"} · READY

test.py 에서 바뀐 것:
  - 검색 구현은 llm.tools.knowledge 에서 소유하고 이 에이전트는 슬롯/출처 문맥을 전달한다
  - "다른 Search Agent 담당" 차단(순위·일정·티켓) 삭제 — 디스패처가 그 질문을 여기로 안 보낸다
  - 담당 카테고리 화이트리스트는 knowledge 검색에서 유지한다
  - 프롬프트 말투는 ../persona 에서, 근거 등급(grade)을 sources 에 넣음
  - 구단 미지정 되묻기는 프롬프트에만 맡기지 않고 코드로도 막는다(채점 가능하게)
"""
import logging
import os
import time

from django.conf import settings
from langchain_openai import ChatOpenAI

from ...progress import config_kwargs

from ....tools import knowledge
from ..domain_tools import tools_for, visible_text
from ..persona import FIXED
from .prompts import SYSTEM

log = logging.getLogger(__name__)

try:
    from langchain.agents import create_agent      # langchain>=1.0
    READY = True
except ImportError:                                 # langchain 패키지가 없으면 디스패처가 club 으로 보낸다
    create_agent = None
    READY = False
    log.warning("langchain.agents.create_agent 를 불러올 수 없어 venue 도메인을 비활성화합니다 (pip install langchain)")

LLM_MODEL = os.getenv("LLM_MODEL") or "gpt-6-luna"
infer_slots = knowledge.infer_slots
transform_query = knowledge.transform_query
search_documents_tool = knowledge.search_documents_tool

# ── LLM · Agent (서버 기동 후 1회 생성) ──────────────────────────────────────
_llm = None
_agent = None

def llm():
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(model=LLM_MODEL, timeout=25, max_retries=0, reasoning_effort="medium", use_responses_api=True,
                          max_tokens=settings.USAGE_MAX_CALL_OUTPUT_TOKENS)
    return _llm

def agent():
    global _agent
    if _agent is None:
        _agent = create_agent(model=llm(), tools=tools_for("venue"), system_prompt=SYSTEM)
    return _agent

# ── 진입점 ──────────────────────────────────────────────────────────────────
def _stadium_from_history(history):
    for m in reversed(history or []):
        if m.get("role") == "user":
            s = infer_slots(m["content"])
            if s.get("stadium_code"):
                return s
    return {}

def _answer(question, history=None, hint_stadium=None):
    if not READY:
        raise RuntimeError("venue 도메인 비활성화 (langchain 패키지 없음)")
    history = history or []
    timing, route = {}, []

    slots = infer_slots(question)
    if not slots.get("stadium_code"):
        prev = _stadium_from_history(history)
        if prev:
            slots["stadium_code"], slots["team_code"] = prev["stadium_code"], prev.get("team_code")
            route.append(f"carry:{prev['stadium_code']}")
        elif hint_stadium:
            slots["stadium_code"] = hint_stadium
            route.append(f"hint:{hint_stadium}")
    if not slots.get("stadium_code"):
        return {"answer": FIXED["clarify"], "sources": [], "route": "guard:clarify", "timing": timing}

    ctx = {"slots": slots}
    token = knowledge.search_context.set(ctx)
    try:
        messages = [*[{"role": m["role"], "content": m["content"]} for m in history[-6:]],
                    {"role": "user", "content": question}]
        t0 = time.perf_counter()
        result = agent().invoke(
            {"messages": messages},
            **config_kwargs(recursion_limit=6),
        )
        timing["agent_ms"] = round((time.perf_counter() - t0) * 1000)
    finally:
        knowledge.search_context.reset(token)

    final = result["messages"][-1]
    text = visible_text(final.content)
    tool_called = any(type(m).__name__ == "ToolMessage" for m in result["messages"])

    last = ctx.get("last") or {}
    docs = last.get("documents", [])
    sources = [{"doc_id": d["metadata"].get("doc_id"), "grade": knowledge.grade(d["metadata"]),
                "category": d["metadata"].get("category"), "stadium": d["metadata"].get("stadium_code"),
                "match_type": d["metadata"].get("match_type")} for d in docs]
    route.append(f"agent:{'tool' if tool_called else 'no_tool'}:{last.get('search_method', '-')}"
                 f":{slots.get('stadium_code')}:{','.join(slots.get('categories') or []) or '-'}")
    return {"answer": text, "sources": sources, "route": " ".join(route), "timing": timing}

def answer(question, history=None, hint_stadium=None):
    from llm.tools.assistant import request_state
    with request_state(hint_stadium, question, history):
        return _answer(question, history, hint_stadium)
