"""community domain tools."""
from datetime import date
from pydantic import Field, model_validator
from django.db.models import Q
from community.models import FREE_CATEGORIES, TEAM_CATEGORIES, TEAM_CODES, CommunityPost, PredictionGame

from .common import LimitInput, _json, _result, _rows, _tool

class CommunitySearchInput(LimitInput):
    query: str = Field(min_length=1, max_length=100)
    board: str | None = Field(default=None, pattern="^(free|teams)$")
    team_code: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    category: str | None = None

    @model_validator(mode="after")
    def validate_filters(self):
        if self.team_code and self.team_code not in TEAM_CODES:
            raise ValueError("올바른 팀 코드가 아닙니다.")
        if self.board == "free" and self.team_code:
            raise ValueError("자유게시판에는 팀 필터를 쓸 수 없습니다.")
        allowed_categories = FREE_CATEGORIES if self.board == "free" else TEAM_CATEGORIES
        if self.category and self.category not in allowed_categories:
            raise ValueError("올바른 카테고리가 아닙니다.")
        return self

class PredictionInput(LimitInput):
    game_date: date
    team_code: str | None = Field(default=None, pattern="^[A-Z]{2}$")
    status: str | None = Field(default=None, pattern="^(scheduled|live|final|cancelled|postponed|suspended|unknown)$")

    @model_validator(mode="after")
    def validate_team(self):
        if self.team_code and self.team_code not in TEAM_CODES:
            raise ValueError("올바른 팀 코드가 아닙니다.")
        return self

def create_community_tools():

    def search_community_posts(query, board=None, team_code=None, category=None, limit=20):
        """공개 커뮤니티 글을 제목·본문으로 검색한다."""
        posts = CommunityPost.objects.filter(Q(title__icontains=query) | Q(content__icontains=query))
        if board:
            posts = posts.filter(board=board)
        if team_code:
            posts = posts.filter(team_code=team_code)
        if category:
            posts = posts.filter(category=category)
        return _result(_rows(posts.order_by("-created_at", "post_number"), ("post_number", "board", "team_code", "author", "title", "content", "category", "created_at", "views", "recommendations", "comment_count", "is_sample"), limit))

    def get_prediction_games(game_date, team_code=None, status=None, limit=20):
        """저장된 승부예측 대상 경기와 익명 팬 투표 집계를 조회한다(개인 선택 제외)."""
        from community.predictions import _counts

        games = PredictionGame.objects.filter(game_date=game_date)
        if team_code:
            games = games.filter(Q(home_team_code=team_code) | Q(away_team_code=team_code))
        if status:
            games = games.filter(status=status)
        items = [{
            "game_id": game.source_id, "date": game.game_date.isoformat(),
            "starts_at": _json(game.starts_at), "stadium": game.stadium,
            "away": {"code": game.away_team_code, "name": game.away_team_name, "score": game.away_score},
            "home": {"code": game.home_team_code, "name": game.home_team_name, "score": game.home_score},
            "status": game.status, "result": game.result or None,
            "locked": game.locked_at is not None, "voided": game.voided_at is not None,
            "source_fetched_at": _json(game.source_fetched_at),
            "fan_votes": _counts(game),
            "fan_vote_notice": "이용자 팬 투표 집계이며 실제 승리 확률이나 경기 결과 예측이 아닙니다.",
        } for game in games.order_by("starts_at", "source_id")[:limit]]
        return _result(items)

    specs = (
        (search_community_posts, 'search_community_posts', '공개 커뮤니티 글을 검색한다.', CommunitySearchInput),
        (get_prediction_games, 'get_prediction_games', '승부예측 대상 경기와 실제 확률이 아닌 익명 팬 투표 집계를 조회한다.', PredictionInput),
    )
    return tuple(_tool(*spec) for spec in specs)
