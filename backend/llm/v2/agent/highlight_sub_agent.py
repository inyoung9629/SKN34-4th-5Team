"""하이라이트 전문 Agent: 장면 RAG(Qdrant 의미 검색) + 장면표(DB 조건 검색)를 맞춰 보고 질문과 맞는 장면만 고른다.

고른 장면의 영상 카드(videoId·시작 시각)는 select_highlight_scenes 결과로 서버가 만든다. 모델은 URL 을 쓰지 않는다."""
from .common import build_agent

HIGHLIGHT_RULES = """역할: KBO 하이라이트 장면 전문 에이전트. 받은 작업에 맞는 장면을 찾아 검증하고 고른다.
1. 선수·팀·상대 팀·날짜·결과(홈런·안타·호수비 등)를 말했으면 search_highlight_scenes 로 조건 검색한다.
2. 장면을 말로 묘사했거나(역전, 끝내기, 다이빙 캐치, 3점 홈런 등) 조건 검색이 비거나 애매하면 search_highlight_rag 로 의미 검색한다.
   두 도구는 한 번에 함께 불러도 된다. 같은 도구를 같은 조건으로 다시 부르지 않는다.
   '최근·요즘·지난 경기·마지막'처럼 날짜 없이 최근을 물으면 search_highlight_scenes 는 최신 경기부터 돌려주고,
   search_highlight_rag 는 recent_first=true 로 부른다. 고를 때도 game_date 가 가장 최근인 장면을 먼저 고른다.
3. 검증: 후보마다 날짜·대진(matchup)·타자·결과(event)·타점(rbi)·이닝이 작업과 맞는지 본다. 두 도구에 같은 scene_id 가 있으면 더 믿을 만하다.
   작업에 선수 이름이 있으면 batter 나 description 에 그 이름이 있는 장면만 남긴다. 결과를 말했으면 event 가 같은 장면만 남긴다.
   '3점 홈런'처럼 타점을 말했으면 rbi 가 같은 장면을 우선한다. 날짜를 말했는데 그날 장면이 없으면 relaxed·requested_date 를 보고
   가장 가까운 경기를 고르고 날짜가 다르다고 적는다.
4. 맞는 장면이 있으면 select_highlight_scenes(scene_ids=[...], note=불확실한 점)를 한 번만 부른다. 검색 결과에 있던 scene_id 만 쓴다.
   날짜를 특정하지 않은 질문은 가장 최근 장면 하나, 여러 개·전부를 원하면 최대 5개를 고른다.
5. 맞는 장면이 없으면 select_highlight_scenes 를 부르지 않고, 아직 분석된 영상에 없다고 analyzed_videos·latest_game_date 와 함께 돌려준다.
최종 답은 사실만 짧게: 고른 장면마다 날짜·대진·이닝·타자·결과(타점)·한 줄 설명, pitcher 가 있으면 상대 투수도 적는다.
불확실한 점은 해당될 때만: 날짜를 바로잡았을 때, 작업이 투수를 물었는데 pitcher 가 비어 있을 때(투수를 묻지 않았으면 투수 얘기를 꺼내지 않는다).
URL·영상 ID·재생 시각은 만들거나 적지 않는다(영상 카드는 서버가 붙인다)."""

TOOLS = ("search_highlight_scenes", "search_highlight_rag", "select_highlight_scenes")


class MultimodalAgent:
    """Existing ask_highlight delegation/card contract, without another model tool loop."""
    def stream(self, inputs, **kwargs):
        import fcntl
        import json
        import math
        from pathlib import Path
        from django.conf import settings
        from langchain_core.messages import AIMessage, ToolMessage
        from llm.highlight_multimodal import Google, Specialist

        root = Path(settings.HIGHLIGHT_MULTIMODAL_CORPUS)
        # ponytail: serialize local Qdrant across workers; use a server if throughput matters.
        with (root / "web.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            specialist = Specialist(root, Google())
            try:
                try:
                    result = specialist.ask(inputs["messages"][-1].text, candidate_limit=3)
                except (RuntimeError, ValueError) as exc:
                    result = {"complete": False, "no_match": None, "matches": [],
                              "decisions": [{"provider_error": str(exc)}]}
            finally:
                specialist.close()
        failures = [d.get("provider_error") or d.get("rejected") for d in result["decisions"]
                    if d.get("provider_error") or d.get("rejected")]
        if not result["complete"]:
            text = "[조회 실패] 멀티모달 검증 미완료: " + "; ".join(failures) + ". 장면 없음으로 판단할 수 없습니다."
            yield {"tools": {"messages": [ToolMessage(text, name="select_highlight_scenes",
                   tool_call_id="multimodal-selection", status="error", artifact={"multimodal": result})]}}
        else:
            videos = [{"videoId": m["video_id"], "start": math.floor(m["playback_seconds"]),
                       "label": m["evidence"][:120]} for m in result["matches"]]
            text = "\n".join(m["evidence"] for m in result["matches"]) or "샘플링된 멀티모달 후보에서 요청 장면을 확인하지 못했습니다(전체 경기 검색 아님)."
            yield {"tools": {"messages": [ToolMessage(json.dumps(result, ensure_ascii=False),
                   name="select_highlight_scenes", tool_call_id="multimodal-selection",
                   artifact={"videos": videos, "multimodal": result})]}}
        yield {"model": {"messages": [AIMessage(text)]}}


def build(model, tools_by_name):
    from django.conf import settings
    if getattr(settings, "HIGHLIGHT_MULTIMODAL_CORPUS", ""):
        return MultimodalAgent()
    return build_agent(model, [tools_by_name[n] for n in TOOLS], HIGHLIGHT_RULES)
