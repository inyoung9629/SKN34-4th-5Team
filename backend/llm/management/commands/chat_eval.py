r"""로컬 챗봇 일괄 평가 — 크롬에서 한 문항씩 치는 대신 실제 v2 그래프에 질문을 넣고 결과를 채점·저장한다.

실제 모델·도구·DB·Qdrant 를 쓴다(비용 발생). 채팅 세션·크레딧은 쓰지 않는다(그래프 직접 실행).
    type rag_test\chat_eval\questions_1012.jsonl | docker compose exec -T backend python manage.py chat_eval --questions - --label A
    ... --ids rule1,hl1        일부만
    ... --category 규정        카테고리만
결과: /app/artifacts/chat_eval/<label>_<시각>.jsonl (문항별 원본: 도구 호출·도구 결과 앞부분·답변·영상 카드·토큰)
      + 콘솔 표(문항별 통과/실패와 실패 사유). 같은 문항 파일로 --label 을 바꿔 돌리면 A/B 비교가 된다.

문항 형식 (jsonl 한 줄):
  id, category, turns(질문 목록. 마지막 질문을 채점, 앞 질문은 같은 대화로 먼저 실행), expect(answer|clarify|refuse|no_lookup|any),
  must([["a","b"],["c"]] = (a 또는 b) 그리고 c, 공백 무시), forbid(답변에 있으면 실패), tools_any(하나 이상 호출), tools_all([[a,b],[c]] = 묶음마다 하나 이상),
  tools_none(호출하면 실패), no_url(답변 본문 URL 금지), videos(required|none), max_videos, note(사람이 볼 것)
채점은 결정적 규칙만 한다. 의미 정확성·충실도(DeepEval judge)는 이 결과 파일을 입력으로 따로 잰다.
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

URL = re.compile(r"https?://|youtu\.be|youtube\.com/watch", re.I)
REFUSE = re.compile(r"도와드리기 어렵|도와드릴 수 없|답변드리기 어렵|안내드리기 어렵|제공할 수 없|제공하지 않|범위(를|가)? 벗어|"
                    r"KBO[^.\n]{0,20}(만|관련)[^.\n]{0,20}(도와|안내|답변)|처리할 수 없|할 수 없습니다")
LOOKUP_FREE = {"present_planning_questions"}  # 되묻기 UI 는 조회가 아니다
OUT = Path("/app/artifacts/chat_eval")


def norm(text):
    return re.sub(r"\s+", "", text or "").lower()


def run_turn(graph, history, question):
    """그래프 1턴 실행. 메인·하위 에이전트의 도구 호출과 결과(앞부분), 최종 답, 영상 카드, 판정을 모은다."""
    from langchain_core.callbacks import get_usage_metadata_callback
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from llm.v2.agent.chat_ui import highlight_videos
    human = HumanMessage(question)
    calls, results, main_messages, answer, decision = [], [], [], "", None
    started = time.monotonic()
    with get_usage_metadata_callback() as usage:
        stream = graph.stream({"messages": [*history, human]}, stream_mode="updates", subgraphs=True)
        for ns, data in stream:
            for update in (data or {}).values():
                if not isinstance(update, dict):
                    continue
                if not ns and update.get("decision"):
                    decision = update["decision"]
                for message in update.get("messages") or ():
                    if isinstance(message, AIMessage) and message.tool_calls:
                        calls += [{"name": c["name"], "args": c.get("args"), "depth": len(ns)} for c in message.tool_calls]
                    elif isinstance(message, AIMessage) and not ns:
                        answer = str(message.text)
                    elif isinstance(message, ToolMessage):
                        results.append({"name": message.name, "status": message.status, "depth": len(ns),
                                        "content": str(message.content)[:3000]})
                    if not ns:
                        main_messages.append(message)
    return {"question": question, "answer": answer, "tool_calls": calls, "tool_results": results,
            "videos": highlight_videos(main_messages), "decision": decision,
            "latency_s": round(time.monotonic() - started, 2),
            "usage": {model: dict(u) for model, u in usage.usage_metadata.items()}}, [human, AIMessage(answer)]


def grade(case, turn):
    """결정적 채점. 실패 사유 목록을 돌려준다(빈 목록 = 통과)."""
    fails, answer = [], turn["answer"]
    names = {c["name"] for c in turn["tool_calls"]}
    lookups = names - LOOKUP_FREE
    expect = case.get("expect", "answer")
    if not answer.strip():
        fails.append("빈 답변")
    for group in case.get("must", []):
        options = group if isinstance(group, list) else [group]
        if not any(norm(o) in norm(answer) for o in options):
            fails.append("필수 누락: " + "/".join(options))
    for word in case.get("forbid", []):
        if norm(word) in norm(answer):
            fails.append("금지 표현: " + word)
    if case.get("tools_any") and not names & set(case["tools_any"]):
        fails.append("필요 도구 미호출: " + "/".join(case["tools_any"]))
    for group in case.get("tools_all", []):
        if not names & set(group):
            fails.append("필요 도구 미호출: " + "/".join(group))
    if set(case.get("tools_none", [])) & names:
        fails.append("금지 도구 호출: " + ",".join(sorted(set(case["tools_none"]) & names)))
    if case.get("no_url") and URL.search(answer):
        fails.append("답변 본문에 URL")
    videos = turn["videos"]
    if case.get("videos") == "required" and not videos:
        fails.append("영상 카드 없음")
    if case.get("videos") == "none" and videos:
        fails.append("영상 카드가 나오면 안 됨")
    if case.get("max_videos") is not None and len(videos) > case["max_videos"]:
        fails.append(f"영상 카드 {len(videos)}개 > {case['max_videos']}")
    if expect == "clarify":
        if lookups:
            fails.append("되묻기 전에 조회함: " + ",".join(sorted(lookups)))
        if "?" not in answer and "present_planning_questions" not in names:
            fails.append("되묻는 질문 없음")
    if expect == "no_lookup" and lookups:
        fails.append("조회가 필요 없는데 조회함: " + ",".join(sorted(lookups)))
    if expect == "refuse":
        if lookups:
            fails.append("거절해야 하는데 조회함: " + ",".join(sorted(lookups)))
        if not REFUSE.search(answer):
            fails.append("거절 표현 없음(사람 확인)")
    return fails


class Command(BaseCommand):
    help = "v2 챗봇 일괄 평가(실제 모델·도구). 문항 jsonl → 결과 jsonl + 통과/실패 표"

    def add_arguments(self, parser):
        parser.add_argument("--questions", required=True, help="문항 jsonl 경로, '-' 면 표준 입력")
        parser.add_argument("--label", default="run", help="A/B 구분 이름 (예: A-vector, B-rerank)")
        parser.add_argument("--ids", default="", help="쉼표로 구분한 문항 id만")
        parser.add_argument("--category", default="", help="이 카테고리만")
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **o):
        import os
        text = sys.stdin.read() if o["questions"] == "-" else Path(o["questions"]).read_text(encoding="utf-8")
        cases = [json.loads(line) for line in text.splitlines() if line.strip()]
        if len({c["id"] for c in cases}) != len(cases):
            raise CommandError("문항 id 중복")
        if o["ids"]:
            wanted = set(o["ids"].split(","))
            cases = [c for c in cases if c["id"] in wanted]
        if o["category"]:
            cases = [c for c in cases if c.get("category") == o["category"]]
        if o["limit"]:
            cases = cases[: o["limit"]]
        if not cases:
            raise CommandError("실행할 문항이 없다")
        from llm.v2.agent.chain import get_graph
        graph = get_graph()
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / f"{o['label']}_{datetime.now():%m%d_%H%M%S}.jsonl"
        meta = {"label": o["label"], "llm_model": os.getenv("LLM_MODEL") or "gpt-6-luna",
                "rag_search_mode": os.getenv("RAG_SEARCH_MODE") or "rerank", "started": datetime.now().isoformat(timespec="seconds")}
        passed = 0
        with path.open("x", encoding="utf-8") as out:
            out.write(json.dumps({"meta": meta}, ensure_ascii=False) + "\n")
            for case in cases:
                turns = case.get("turns") or [case["question"]]
                history, record = [], {"id": case["id"], "category": case.get("category"), "case": case}
                try:
                    for question in turns:
                        turn, added = run_turn(graph, history, question)
                        history += added
                    record["turn"] = turn
                    record["fails"] = grade(case, turn)
                    record["status"] = "failed" if record["fails"] else "passed"
                except Exception as exc:  # 평가 실행 오류는 제품 실패와 구분한다
                    record.update(status="error", error=f"{type(exc).__name__}: {exc}"[:500])
                passed += record["status"] == "passed"
                out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                out.flush()
                mark = {"passed": "OK ", "failed": "NG ", "error": "ERR"}[record["status"]]
                detail = "; ".join(record.get("fails", [])) or record.get("error", "")
                tools = ",".join(dict.fromkeys(c["name"] for c in record.get("turn", {}).get("tool_calls", [])))
                self.stdout.write(f"{mark} {case['id']:<8} {record.get('turn', {}).get('latency_s', '-'):>6}s  [{tools}]  {detail}")
                if case.get("note"):
                    self.stdout.write(f"      확인: {case['note']}")
        self.stdout.write(f"\n{o['label']}: {passed}/{len(cases)} 통과 (규칙 채점만, note 항목은 사람 확인) -> {path}")
