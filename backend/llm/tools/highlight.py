"""highlight domain tools: collect_highlights 배치가 저장한 하이라이트 장면을 찾고(조건 검색·RAG 의미 검색) 검증된 장면을 고른다.

모델에게는 URL 을 주지 않는다. 고른 장면의 영상 카드(videoId·시작 시각)는 select_highlight_scenes 가 DB 에서 다시 읽어 만든다."""
import json
from datetime import date

from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce
from langchain_core.tools import StructuredTool
from pydantic import Field, StrictInt, model_validator

from .common import INVALID_INPUT, ToolInput, _json, _result, _tool, db_team_code, is_team_code

# 이벤트를 정하지 않은 "하이라이트" 질문에서 먼저 보여 줄 장면: 타점 1점당 10 + 이벤트 가중치(나머지 5)
RECENT_DAYS = 7  # recent_first: 최근 경기 날짜를 며칠까지 거슬러 찾을지
EVENT_WEIGHT = {"홈런": 50, "3루타": 30, "2루타": 25, "호수비": 25, "안타": 20, "득점": 20, "실책": 10, "도루": 10}


def importance():
    weight = Case(*(When(event=event, then=Value(score)) for event, score in EVENT_WEIGHT.items()),
                  default=Value(5), output_field=IntegerField())
    return Coalesce("rbi", Value(0)) * 10 + weight


class HighlightInput(ToolInput):
    player_name: str | None = Field(default=None, min_length=2, max_length=40, description="선수 이름(예: 김도영)")
    pitcher_name: str | None = Field(default=None, min_length=2, max_length=40,
                                     description="상대 투수 이름. 'A가 B 상대로 친'이면 player_name=A, pitcher_name=B")
    team_code: str | None = Field(default=None, description="공격 팀 코드(예: KIA, LG, DOOSAN)")
    opponent_team_code: str | None = Field(default=None, description="상대 팀 코드. 'LG전'처럼 상대를 말하면 LG")
    event: str | None = Field(default=None, description="홈런·안타·2루타·3루타·볼넷·삼진·병살타·희생타·호수비·도루·득점 등. 모르면 비운다")
    date_from: date | None = None
    date_to: date | None = None
    limit: StrictInt | None = Field(default=None, ge=1, le=10,
                                    description="사용자가 여러 개·전부를 달라고 할 때만 넣는다. 비우면 날짜를 특정하지 않은 질문은 가장 최근 1개")

    @model_validator(mode="after")
    def validate_filters(self):
        from llm.highlights import EVENTS
        if any(code and not is_team_code(code) for code in (self.team_code, self.opponent_team_code)):
            raise ValueError("올바른 팀 코드가 아닙니다.")
        if self.event and self.event not in EVENTS:
            raise ValueError(f"event 는 {', '.join(EVENTS)} 중 하나입니다.")
        return self


def _check_codes(model):
    from llm.highlights import EVENTS
    if any(code and not is_team_code(code) for code in (model.team_code, model.opponent_team_code)):
        raise ValueError("올바른 팀 코드가 아닙니다.")
    if model.event and model.event not in EVENTS:
        raise ValueError(f"event 는 {', '.join(EVENTS)} 중 하나입니다.")
    return model


class HighlightRagInput(ToolInput):
    query: str = Field(min_length=2, max_length=120,
                       description="찾을 장면을 말로 묘사(예: 김도영 3점 홈런, 9회 끝내기 안타, 다이빙 캐치, 역전 홈런)")
    team_code: str | None = Field(default=None, description="경기에 나온 팀 코드(예: KIA). 모르면 비운다")
    opponent_team_code: str | None = Field(default=None, description="상대 팀 코드. 'LG전'이면 LG")
    event: str | None = Field(default=None, description="홈런·안타·호수비 등으로 확실할 때만. 모르면 비운다")
    date_from: date | None = None
    date_to: date | None = None
    limit: StrictInt | None = Field(default=None, ge=1, le=10, description="후보 수(기본 5)")
    recent_first: bool = Field(default=False, description="'최근·요즘·지난 경기'처럼 날짜 없이 최근을 물었으면 true: "
                                                          "가장 최근 경기부터 날짜순으로 찾는다")

    @model_validator(mode="after")
    def validate_filters(self):
        return _check_codes(self)


class HighlightSelectInput(ToolInput):
    scene_ids: list[StrictInt] = Field(min_length=1, max_length=5,
                                       description="검증을 통과한 장면의 scene_id. 이번 검색 결과에 있던 값만")
    note: str | None = Field(default=None, max_length=200, description="불확실한 점(예: 투수 미확인, 날짜를 바로잡음)")


def scene_item(s):
    """모델에게 보여 줄 장면 정보. URL 은 넣지 않는다(카드는 select_highlight_scenes 결과로 서버가 만든다)."""
    return {
        "scene_id": s.pk, "video_id": s.video_id, "title": s.video.title, "game_date": _json(s.video.game_date),
        "matchup": " vs ".join(t.team_code for t in (s.video.away_team, s.video.home_team) if t),
        "inning": s.inning, "half": s.half or None,
        "batting_team": s.batting_team.team_code if s.batting_team else None,
        "batter": s.batter_name or None, "pitcher": s.pitcher_name or None, "event": s.event, "rbi": s.rbi,
        "description": s.description, "start_sec": s.start_sec,
    }


def pitcher_team(name):
    """투수의 소속 팀 코드: 선수 DB → 그 투수가 나온 저장 장면(수비 팀 = 경기 두 팀 - 공격 팀) 순. 모르면 None.
    분석 모델은 투수 칸을 자주 비우고 설명에만 적어서("고우석이 커브로 양의지를 삼진") 설명도 본다."""
    from collections import Counter
    from baseball.models import Player
    from llm.models import HighlightScene
    codes = set(Player.objects.filter(name=name).values_list("team__team_code", flat=True))
    if len(codes) == 1:
        return codes.pop()
    votes = Counter()
    scenes = HighlightScene.objects.filter(Q(pitcher_name=name) | Q(description__contains=name)).exclude(batter_name=name)
    for scene in scenes.select_related("video__home_team", "video__away_team", "batting_team")[:50]:
        teams = {t.team_code for t in (scene.video.home_team, scene.video.away_team) if t}
        if scene.batting_team and len(teams) == 2:
            votes.update(teams - {scene.batting_team.team_code})
    top = votes.most_common(2)
    return top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None


def create_highlight_tools():
    from llm.models import HighlightScene, HighlightVideo

    def search_highlight_scenes(player_name=None, pitcher_name=None, team_code=None, opponent_team_code=None, event=None,
                                date_from=None, date_to=None, limit=None):
        """저장된 KBO 하이라이트 장면을 선수·팀·이벤트·날짜로 찾는다(최신 경기 우선).

        투수 칸은 분석 모델이 자주 비우고, 사용자가 기억하는 날짜는 틀리기도 한다. 그래서 결과가 없으면 투수 → 날짜
        조건 순으로 풀어서 다시 찾고, 푼 조건을 relaxed 에 남겨 답변이 그 사실을 밝히게 한다."""
        base = HighlightScene.objects.filter(video__status=HighlightVideo.ANALYZED).select_related(
            "video__home_team", "video__away_team", "batting_team")
        if player_name:
            # 모델이 타자 칸을 비워도 장면 설명에는 이름을 적는 경우가 있다("나승엽이 … 홈런")
            # 투수로 나온 장면("고우석 상대 홈런")도 찾는다
            base = base.filter(Q(batter_name__icontains=player_name) | Q(batter__name__icontains=player_name)
                               | Q(pitcher_name__icontains=player_name) | Q(description__icontains=player_name))
        if team_code:
            base = base.filter(batting_team__team_code=db_team_code(team_code))
        if opponent_team_code:  # 그 팀이 나온 경기에서, 그 팀이 공격하지 않은 장면
            code = db_team_code(opponent_team_code)
            base = base.filter(Q(video__home_team__team_code=code) | Q(video__away_team__team_code=code))
            base = base.exclude(batting_team__team_code=code)
        if event:
            base = base.filter(event=event)

        defending = pitcher_team(pitcher_name) if pitcher_name else None

        def narrowed(use_pitcher, use_date):
            scenes = base
            if use_pitcher and pitcher_name:
                scenes = scenes.filter(Q(pitcher_name__icontains=pitcher_name) | Q(description__icontains=pitcher_name))
            elif pitcher_name and defending:  # 투수 칸이 비었으면 그 투수 팀이 수비한 장면으로만 좁힌다
                scenes = scenes.filter(Q(video__home_team__team_code=defending) | Q(video__away_team__team_code=defending))
                scenes = scenes.exclude(batting_team__team_code=defending).exclude(batting_team__isnull=True)
            if use_date and date_from:
                scenes = scenes.filter(video__game_date__gte=date_from)
            if use_date and date_to:
                scenes = scenes.filter(video__game_date__lte=date_to)
            return scenes

        has_date = bool(date_from or date_to)
        # 투수만으로 장면을 특정한 질문에서 투수 팀도 모르면 투수 조건을 풀지 않는다(그날 다른 경기 홈런이 섞임)
        can_drop_pitcher = bool(pitcher_name) and bool(defending or player_name or team_code or opponent_team_code)
        attempts = [(True, True)]
        if can_drop_pitcher:
            attempts.append((False, True))
        if has_date:
            attempts.append((True, False))
        if can_drop_pitcher and has_date:
            attempts.append((False, False))
        scenes, relaxed = narrowed(True, True), []
        for use_pitcher, use_date in attempts:
            candidate = narrowed(use_pitcher, use_date)
            if candidate.exists():
                scenes = candidate
                relaxed = [name for name, dropped in (("pitcher", pitcher_name and not use_pitcher),
                                                      ("date", has_date and not use_date)) if dropped]
                break
        # "10월 6일 하이라이트"처럼 날짜만 말한 질문은 그날 경기마다 대표 장면 하나씩
        per_game = has_date and "date" not in relaxed and not any(
            (player_name, pitcher_name, team_code, opponent_team_code, event))
        if limit is None:  # 날짜를 특정한 질문은 그날 장면(최대 3개), 아니면 가장 최근 장면 하나
            limit = 10 if per_game else 3 if has_date and "date" not in relaxed else 1
        if event:
            scenes = scenes.order_by("-video__game_date", "-video__published_at", "start_sec")
        else:  # 이벤트를 말하지 않았으면 같은 날 안에서 득점·홈런 같은 주요 장면부터 고른다
            scenes = scenes.annotate(score=importance()).order_by(
                "-video__game_date", "-score", "-video__published_at", "start_sec")
        if per_game:
            best = {}
            for scene in scenes:
                best.setdefault(scene.video_id, scene)
            ordered = list(best.values())
        else:
            ordered = list(scenes[:max(limit, 50)])
        requested_date = None
        if "date" in relaxed:  # 날짜를 잘못 기억한 질문이면 요청한 날짜에 가장 가까운 장면 하나만 바로잡아 보여 준다
            target = requested_date = date_from or date_to
            ordered.sort(key=lambda s: abs((s.video.game_date - target).days) if s.video.game_date else 9999)
            limit = 1
        ordered = ordered[:limit]
        if not event:  # 고른 장면은 경기 흐름대로 보여 준다
            ordered.sort(key=lambda s: (-(s.video.game_date or date.min).toordinal(), s.video_id, s.start_sec))
        items = [scene_item(s) for s in ordered]
        analyzed = HighlightVideo.objects.filter(status=HighlightVideo.ANALYZED)
        extra = {"note": "조건을 풀어 찾은 장면이라 공식 기록과 다를 수 있다."} if relaxed else {}
        return _result(items, relaxed=relaxed, requested_date=_json(requested_date), analyzed_videos=analyzed.count(),
                       latest_game_date=_json(analyzed.order_by("-game_date").values_list("game_date", flat=True).first()),
                       **extra)

    def analyzed_scenes():
        return HighlightScene.objects.filter(video__status=HighlightVideo.ANALYZED).select_related(
            "video__home_team", "video__away_team", "batting_team")

    def search_highlight_rag(query, team_code=None, opponent_team_code=None, event=None, date_from=None, date_to=None,
                             limit=None, recent_first=False):
        """하이라이트 RAG: 질문을 임베딩해 Qdrant 장면 설명 색인에서 의미가 가까운 장면을 찾는다(가까운 순).
        recent_first 이고 날짜가 없으면 분석된 경기 날짜를 최근부터 하루씩 찾아, 처음 장면이 나온 날의 결과를 돌려준다."""
        from llm import highlight_vectors
        codes = [db_team_code(code) for code in (team_code, opponent_team_code) if code]
        try:
            if recent_first and not (date_from or date_to):
                days = (HighlightVideo.objects.filter(status=HighlightVideo.ANALYZED, game_date__isnull=False)
                        .order_by("-game_date").values_list("game_date", flat=True).distinct()[:RECENT_DAYS])
                hits = []
                for day in days:
                    hits = highlight_vectors.search(query, codes, day, day, event, k=limit or 5)
                    if hits is None or hits:
                        break
            else:
                hits = highlight_vectors.search(query, codes, date_from, date_to, event, k=limit or 5)
        except Exception as exc:  # noqa: BLE001 - 의미 검색은 보조: 실패를 알리고 조건 검색으로 답하게 한다
            return _result([], search_mode="rag", error=f"의미 검색 실패: {type(exc).__name__}")
        if hits is None:
            return _result([], search_mode="rag", error="하이라이트 RAG 색인이 아직 없다. search_highlight_scenes 결과로 답한다.")
        scores = dict(hits)
        by_id = analyzed_scenes().in_bulk(list(scores))
        items = [{**scene_item(by_id[pk]), "score": score} for pk, score in hits if pk in by_id]
        return _result(items, search_mode="rag")

    def select_highlight_scenes(scene_ids, note=None):
        """검증을 통과한 장면만 고른다. 고른 장면은 DB 에서 다시 읽어 화면 영상 카드로 보낸다."""
        from llm.highlights import LINK_LEAD_SEC
        ids = list(dict.fromkeys(scene_ids))[:5]
        by_id = analyzed_scenes().in_bulk(ids)
        chosen = [by_id[pk] for pk in ids if pk in by_id]
        missing = [pk for pk in ids if pk not in by_id]
        videos = [{
            "videoId": s.video_id, "start": max(0, s.start_sec - LINK_LEAD_SEC), "gameDate": _json(s.video.game_date),
            "label": " ".join(part for part in (
                str(s.video.game_date or ""), " vs ".join(t.team_code for t in (s.video.away_team, s.video.home_team) if t),
                f"{s.inning}회{s.half}" if s.inning else "", s.batter_name, s.event,
                f"{s.rbi}타점" if s.rbi else "") if part),
        } for s in chosen]
        content = {"selected": [scene_item(s) for s in chosen], "missing_scene_ids": missing,
                   "note": "선택한 장면은 화면에 영상 카드로 표시된다. URL·재생 시각을 답변에 쓰지 않는다."}
        if not chosen:
            content["note"] = "선택한 scene_id 가 없다(분석된 장면이 아님). 검색 결과의 scene_id 로 다시 고른다."
        return json.dumps(content, ensure_ascii=False, default=str), {"videos": videos, "note": note or ""}

    specs = (
        (search_highlight_scenes, "search_highlight_scenes",
         "KBO 경기 하이라이트 장면을 선수·투수·팀·상대·결과(홈런 등)·날짜 조건으로 DB 에서 찾는다(scene_id 포함).",
         HighlightInput),
        (search_highlight_rag, "search_highlight_rag",
         "KBO 하이라이트 장면을 말로 묘사한 질문(역전·끝내기·다이빙 캐치·3점 홈런 등)으로 의미 검색한다(RAG, scene_id 포함).",
         HighlightRagInput),
    )
    select = StructuredTool.from_function(
        select_highlight_scenes, name="select_highlight_scenes",
        description="검증을 마친 하이라이트 장면 scene_id 를 골라 화면 영상 카드로 보낸다. 검색 결과에 있던 scene_id 만, 한 번만 부른다.",
        args_schema=HighlightSelectInput, response_format="content_and_artifact",
        handle_tool_error=True, handle_validation_error=INVALID_INPUT)
    return (*(_tool(*spec) for spec in specs), select)
