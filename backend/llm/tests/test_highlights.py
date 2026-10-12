"""하이라이트 장면 수집·검색 테스트 (YouTube·OpenRouter 호출은 가짜 응답)."""
import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase

from baseball.models import Player, Team
from llm import highlights
from llm.models import HighlightScene, HighlightVideo
from llm.tools.highlight import create_highlight_tools

TITLE = "[LG트윈스 vs KIA타이거즈] 10.6(화) 야구 하이라이트｜2026 KBO 리그｜KBO X TVING"


class FillPlayersTest(TestCase):
    """영상 인식이 비워 둔 타자·투수 칸을 설명 속 로스터 이름으로 채운다 (포지션으로 투수·야수 구분)."""

    def setUp(self):
        self.kia, _ = Team.objects.get_or_create(team_code="KIA", defaults={"id": 990001, "team_name_ko": "KIA 타이거즈"})
        self.lg, _ = Team.objects.get_or_create(team_code="LG", defaults={"id": 990002, "team_name_ko": "LG 트윈스"})
        for code, team, name, positions in (
                ("t-kdy", self.kia, "김도영", ["infielder"]), ("t-chw", self.kia, "최형우", ["outfielder"]),
                ("t-jhy", self.kia, "정해영", ["pitcher"]), ("t-mbk", self.lg, "문보경", ["infielder"]),
                ("t-gws", self.lg, "고우석", ["pitcher"]), ("t-kjs", self.lg, "김진성", ["투수"]),
                ("t-unk", self.lg, "오지환", [])):
            Player.objects.update_or_create(external_code=code, defaults={"team": team, "name": name, "positions": positions})

    def scene(self, event, description, team="KIA", batter="", pitcher="", inning=9, half="초", start=100):
        return {"start_sec": start, "inning": inning, "half": half, "event": event, "description": description,
                "team_code": team, "batter_name": batter, "pitcher_name": pitcher}

    def test_roles_split_by_position_and_skip_unknown(self):
        roles = highlights.roster_roles(["KIA", "LG"])
        self.assertEqual(roles["LG"], {"pitchers": {"고우석", "김진성"}, "hitters": {"문보경"}})  # 포지션 모르는 오지환은 뺀다
        self.assertEqual(highlights.mentioned_names("이재원이 안타, 이재는 대주자", {"이재", "이재원"}), ["이재원", "이재"])

    def test_fill_batter_and_pitcher_from_description(self):
        roles = highlights.roster_roles(["KIA", "LG"])
        scenes = highlights.fill_from_description([
            self.scene("삼진", "고우석이 김도영을 삼진으로 잡아낸다."),
            self.scene("도루", "최형우가 2루를 훔친다."),  # 주자는 타자로 쓰지 않는다
            self.scene("아웃", "문보경이 유격수 땅볼로 물러난다.", team=None, half="말"),  # 공격팀도 정한다
            self.scene("안타", "김도영이 안타를 친다.", batter="최형우", pitcher="김진성"),  # 있는 값은 그대로
            self.scene("홈런", "고우석과 김진성이 차례로 던진 뒤 김도영이 홈런."),  # 투수 이름이 둘이면 비워 둔다
            self.scene("아웃", "3루수 김도영이 땅볼을 잡아 병살타를 완성한다.", team="LG", half="말"),  # 수비수는 타자 아님
            self.scene("아웃", "최형우의 유격수 땅볼 때 2루 주자 김도영이 3루로 간다."),  # 주자는 타자 아님
            self.scene("투수 교체", "LG 투수가 김진성으로 교체됩니다."),
        ], roles)
        self.assertEqual([(s["event"], s["team_code"], s["batter_name"], s["pitcher_name"]) for s in scenes], [
            ("삼진", "KIA", "김도영", "고우석"), ("도루", "KIA", "", ""), ("아웃", "LG", "문보경", ""),
            ("안타", "KIA", "최형우", "김진성"), ("홈런", "KIA", "김도영", ""), ("병살타", "LG", "", ""),
            ("아웃", "KIA", "최형우", ""), ("투수 교체", "KIA", "", "김진성")])

    def test_first_inning_uses_starting_pitcher_only(self):
        roles = highlights.roster_roles(["KIA", "LG"])
        scenes = highlights.fill_pitchers(highlights.fill_from_description([
            self.scene("안타", "김도영이 안타.", inning=1, start=10),
            self.scene("홈런", "최형우가 홈런.", inning=1, start=20),
            self.scene("삼진", "문보경이 삼진.", team="LG", inning=1, half="말", start=30),
            self.scene("안타", "김도영이 안타.", inning=2, start=40),  # 2회부터는 선발을 이어 붙이지 않는다
        ], roles, {"LG": "톨허스트", "KIA": "올러"}))
        self.assertEqual([s["pitcher_name"] for s in scenes], ["톨허스트", "톨허스트", "올러", ""])

    @patch("llm.highlight_vectors.index_video", return_value=2)
    def test_refill_players_dry_run_then_apply(self, index_video):
        video = HighlightVideo.objects.create(video_id="vfill", title=TITLE, status="analyzed", home_team=self.kia, away_team=self.lg)
        HighlightScene.objects.create(video=video, start_sec=10, inning=9, half="초", batting_team=self.kia, event="삼진",
                                      description="고우석이 김도영을 삼진으로 잡아낸다.")
        HighlightScene.objects.create(video=video, start_sec=40, inning=9, half="초", batting_team=self.kia, event="홈런",
                                      batter_name="김도영", description="역전 3점 홈런.")
        dry = highlights.refill_players(dry_run=True, report=lambda _: None)
        self.assertEqual((dry["b0"], dry["b1"], dry["p0"], dry["p1"], dry["changed"]), (1, 0, 2, 0, 2))
        self.assertFalse(HighlightScene.objects.exclude(pitcher_name="").exists())
        highlights.refill_players(report=lambda _: None)
        rows = list(video.scenes.order_by("start_sec"))
        self.assertEqual([(r.batter_name, r.pitcher_name) for r in rows], [("김도영", "고우석"), ("김도영", "고우석")])
        self.assertEqual(rows[0].batter.external_code, "t-kdy")
        index_video.assert_called_once_with(video)


class ParseTest(TestCase):
    def test_title_teams_and_date(self):
        self.assertEqual(highlights.parse_title(TITLE), ("LG", "KIA", date(2026, 10, 6)))
        self.assertEqual(highlights.parse_title("[삼성라이온즈 vs 키움히어로즈] 9.30(화) 야구 하이라이트｜2026 KBO 리그"),
                         ("SAMSUNG", "KIWOOM", date(2026, 9, 30)))
        self.assertTrue(highlights.is_highlight(TITLE))
        self.assertFalse(highlights.is_highlight("중요한 순간 역전을 만든 LG트윈스! | 두산 vs LG | 10.7 | 크보모먼트"))

    def test_time_helpers(self):
        self.assertEqual(highlights.seconds("03:07"), 187)
        self.assertEqual(highlights.seconds("1:02:03"), 3723)
        self.assertIsNone(highlights.seconds("3분"))
        self.assertEqual(highlights.iso_duration("PT14M35S"), 875)
        self.assertEqual(highlights.watch_url("abc", 187), "https://www.youtube.com/watch?v=abc&t=184s")
        self.assertEqual(highlights.watch_url("abc", 1), "https://www.youtube.com/watch?v=abc&t=0s")


def fake_http(scenes, cost=0.12):
    calls = []

    def http(url, body=None, headers=None, timeout=60):
        calls.append(url)
        if "playlistItems" in url:
            return {"items": [
                {"snippet": {"title": TITLE}, "contentDetails": {"videoId": "vid1", "videoPublishedAt": "2026-10-06T14:00:00Z"}},
                {"snippet": {"title": "크보모먼트 | 두산 vs LG"}, "contentDetails": {"videoId": "skip", "videoPublishedAt": "2026-10-06T13:00:00Z"}},
            ]}
        if "videos?" in url:
            return {"items": [{"id": "vid1", "contentDetails": {"duration": "PT10M0S"}}]}
        assert body["provider"] == {"only": ["google-ai-studio"], "allow_fallbacks": False}
        assert body["messages"][0]["content"][1]["video_url"]["url"] == "https://www.youtube.com/watch?v=vid1"
        return {"choices": [{"message": {"content": json.dumps({"scenes": scenes})}}], "usage": {"cost": cost},
                "provider": "Google AI Studio"}
    http.calls = calls
    return http


SCENES = [
    {"start": "03:07", "end": "03:30", "inning": 3, "half": "말", "batting_team": "KIA", "batter": "김도영",
     "pitcher": "임찬규", "event": "홈런", "rbi": 2, "description": "김도영이 좌월 투런 홈런을 친다."},
    {"start": "05:00", "end": "05:10", "inning": 5, "half": "초", "batting_team": "LG트윈스", "batter": None,
     "pitcher": None, "event": "삼진", "rbi": None, "description": "삼진."},
    {"start": "99:00", "end": "99:10", "inning": 9, "half": "말", "batting_team": "KIA", "batter": "누군가",
     "pitcher": None, "event": "홈런", "rbi": 1, "description": "영상 길이 밖이라 버린다."},
    {"start": "06:00", "end": "06:05", "inning": 6, "half": "초", "batting_team": "KIA", "batter": "x",
     "pitcher": None, "event": "끝내기", "rbi": None, "description": "목록에 없는 이벤트라 버린다."},
]


@patch.dict("os.environ", {"YOUTUBE_API_KEY": "yt", "OPENROUTER_API_KEY": "or"})
@patch("llm.highlights.roster_names", return_value={"LG": [], "KIA": ["김도영"]})
class CollectTest(TestCase):
    def setUp(self):
        self.kia, _ = Team.objects.get_or_create(team_code="KIA", defaults={"id": 990001, "team_name_ko": "KIA 타이거즈"})
        self.lg, _ = Team.objects.get_or_create(team_code="LG", defaults={"id": 990002, "team_name_ko": "LG 트윈스"})
        Player.objects.update_or_create(external_code="test-kdy", defaults={"team": self.kia, "name": "김도영"})

    def test_dry_run_saves_nothing(self, _roster):
        http = fake_http(SCENES)
        summary = highlights.collect(limit=5, dry_run=True, report=lambda _: None, _http=http)
        self.assertEqual(summary["found"], 1)  # 크보모먼트는 제외
        self.assertFalse(HighlightVideo.objects.exists())
        self.assertFalse(any("openrouter" in url for url in http.calls))

    def test_collect_validates_and_skips_analyzed(self, _roster):
        summary = highlights.collect(limit=5, budget=1.0, report=lambda _: None, _http=fake_http(SCENES))
        self.assertEqual((summary["analyzed"], summary["cost"]), (1, 0.12))
        video = HighlightVideo.objects.get(video_id="vid1")
        self.assertEqual((video.status, video.duration_sec, video.home_team, video.away_team), ("analyzed", 600, self.kia, self.lg))
        scenes = list(video.scenes.order_by("start_sec"))
        self.assertEqual([s.event for s in scenes], ["홈런", "삼진"])  # 길이 밖·목록 밖 이벤트는 버림
        homer, strikeout = scenes
        self.assertEqual((homer.start_sec, homer.batter.external_code, homer.batting_team, homer.rbi), (187, "test-kdy", self.kia, 2))
        self.assertEqual(strikeout.batting_team, self.lg)  # 'LG트윈스' 표기도 팀 코드로
        again = highlights.collect(limit=5, report=lambda _: None, _http=fake_http(SCENES))
        self.assertEqual((again["analyzed"], again["skipped"]), (0, 1))

    def test_empty_batter_is_filled_from_description(self, _roster):
        scenes = highlights.clean_scenes([
            {"start": "01:00", "event": "홈런", "batter": None, "description": "나승엽이 우측 폴대를 맞히는 솔로 홈런을 터뜨린다."},
            {"start": "02:00", "event": "투수 교체", "batter": None, "description": "고우석이 마운드에 오른다."},
        ], 600, ["KIA", "LOTTE"])
        self.assertEqual([s["batter_name"] for s in scenes], ["나승엽", ""])

    def test_pitcher_carries_within_half_inning_until_change(self, _roster):
        scenes = highlights.clean_scenes([
            {"start": "01:00", "inning": 9, "half": "초", "event": "안타", "batter": "박정우", "pitcher": "고우석", "description": "안타."},
            {"start": "01:20", "inning": 9, "half": "초", "event": "안타", "batter": "박재현", "pitcher": None, "description": "안타."},
            {"start": "01:40", "inning": 9, "half": "초", "event": "홈런", "batter": "김도영", "pitcher": None, "description": "역전 홈런."},
            {"start": "02:00", "inning": 9, "half": "초", "event": "투수 교체", "batter": None, "pitcher": None, "description": "김진성이 마운드에 오른다."},
            {"start": "02:10", "inning": 9, "half": "초", "event": "삼진", "batter": "최형우", "pitcher": None, "description": "삼진."},
            {"start": "03:00", "inning": 9, "half": "말", "event": "아웃", "batter": "문성주", "pitcher": None, "description": "아웃."},
        ], 600, ["KIA", "LG"])
        self.assertEqual([s["pitcher_name"] for s in scenes], ["고우석", "고우석", "고우석", "김진성", "김진성", ""])

    def test_bad_model_output_marks_failed(self, _roster):
        http = fake_http(SCENES)

        def broken(url, body=None, headers=None, timeout=60):
            if "openrouter" in url:
                return {"choices": [{"message": {"content": "죄송합니다"}}]}
            return http(url, body, headers, timeout)
        summary = highlights.collect(limit=5, report=lambda _: None, _http=broken)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(HighlightVideo.objects.get(video_id="vid1").status, "failed")

    def test_network_error_or_error_payload_fails_only_that_video(self, _roster):
        http = fake_http(SCENES)
        for failure in (TimeoutError("read timed out"), {"error": {"message": "provider down"}}):
            def flaky(url, body=None, headers=None, timeout=60, failure=failure):
                if "openrouter" not in url:
                    return http(url, body, headers, timeout)
                if isinstance(failure, Exception):
                    raise failure
                return failure
            summary = highlights.collect(limit=5, report=lambda _: None, _http=flaky)
            self.assertEqual(summary["failed"], 1)
            self.assertIn("OpenRouter", HighlightVideo.objects.get(video_id="vid1").error)


class SearchToolTest(TestCase):
    def setUp(self):
        kia, _ = Team.objects.get_or_create(team_code="KIA", defaults={"id": 990001, "team_name_ko": "KIA 타이거즈"})
        video = HighlightVideo.objects.create(video_id="vid1", title=TITLE, game_date=date(2026, 10, 6), status="analyzed")
        self.homer = HighlightScene.objects.create(video=video, start_sec=187, inning=3, half="말", batting_team=kia,
                                                   batter_name="김도영", event="홈런", rbi=2, description="좌월 투런 홈런")
        HighlightScene.objects.create(video=video, start_sec=300, batting_team=kia, batter_name="김도영", event="안타")
        HighlightScene.objects.create(video=video, start_sec=420, batting_team=kia, event="홈런", description="나성범이 솔로 홈런을 친다.")
        HighlightVideo.objects.create(video_id="vid2", title=TITLE, status="failed")
        self.tool = create_highlight_tools()[0]

    def test_finds_player_homer_with_start_link(self):
        result = self.tool.invoke({"player_name": "김도영", "event": "홈런"})
        self.assertEqual(result["count"], 1)
        item = result["items"][0]
        self.assertEqual((item["start_sec"], item["scene_id"]), (187, self.homer.pk))
        self.assertNotIn("url", item)  # 모델에게 URL 을 주지 않는다(영상 카드는 select_highlight_scenes 가 만든다)
        self.assertEqual((item["inning"], item["half"], item["batting_team"]), (3, "말", "KIA"))
        self.assertEqual((result["analyzed_videos"], result["latest_game_date"]), (1, "2026-10-06"))
        by_description = self.tool.invoke({"player_name": "나성범", "event": "홈런"})  # 타자 칸이 비어도 설명으로 찾는다
        self.assertEqual([i["start_sec"] for i in by_description["items"]], [420])

    def test_opponent_and_pitcher_filters(self):
        lg, _ = Team.objects.get_or_create(team_code="LG", defaults={"id": 990002, "team_name_ko": "LG 트윈스"})
        kia = Team.objects.get(team_code="KIA")
        video = HighlightVideo.objects.create(video_id="vid3", title=TITLE, game_date=date(2026, 10, 5), status="analyzed",
                                              away_team=lg, home_team=kia)
        HighlightScene.objects.create(video=video, start_sec=90, batting_team=kia, batter_name="김도영",
                                      pitcher_name="고우석", event="홈런")
        HighlightScene.objects.create(video=video, start_sec=200, batting_team=lg, batter_name="문보경", event="홈런")
        vs_lg = self.tool.invoke({"player_name": "김도영", "opponent_team_code": "LG", "event": "홈런"})
        self.assertEqual([(i["video_id"], i["matchup"]) for i in vs_lg["items"]], [("vid3", "LG vs KIA")])
        self.assertEqual([i["batter"] for i in self.tool.invoke({"player_name": "고우석"})["items"]], ["김도영"])
        both = self.tool.invoke({"player_name": "김도영", "pitcher_name": "고우석", "event": "홈런"})
        self.assertEqual([i["start_sec"] for i in both["items"]], [90])
        # 투수 칸이 비어 있거나 다른 이름이면 투수 조건을 풀고 relaxed 로 알린다
        loose = self.tool.invoke({"player_name": "김도영", "pitcher_name": "다른투수", "event": "홈런"})
        self.assertEqual((loose["relaxed"], [i["video_id"] for i in loose["items"]]), (["pitcher"], ["vid1"]))  # 최근 1개
        many = self.tool.invoke({"player_name": "김도영", "event": "홈런", "limit": 5})  # 여러 개를 달라고 할 때만
        self.assertEqual([i["video_id"] for i in many["items"]], ["vid1", "vid3"])
        # 사용자가 날짜를 틀리게 기억해도(10/3) 그 경기 장면(10/5)을 찾고 date 를 알린다
        wrong_day = self.tool.invoke({"player_name": "김도영", "opponent_team_code": "LG", "event": "홈런",
                                      "date_from": "2026-10-03", "date_to": "2026-10-03"})
        self.assertEqual((wrong_day["relaxed"], wrong_day["items"][0]["game_date"]), (["date"], "2026-10-05"))
        # 상대 팀을 안 넘겨도 요청 날짜(10/3)에 가까운 10/5 가 최신 10/6 보다 먼저
        nearest = self.tool.invoke({"player_name": "김도영", "event": "홈런", "date_from": "2026-10-03", "date_to": "2026-10-03"})
        self.assertEqual([i["game_date"] for i in nearest["items"]], ["2026-10-05"])  # 바로잡을 때는 하나만
        self.assertEqual(nearest["requested_date"], "2026-10-03")
        exact = self.tool.invoke({"player_name": "김도영", "event": "홈런"})
        self.assertEqual((exact["relaxed"], "note" in exact, "note" in loose), ([], False, True))  # 안내는 조건을 풀 때만
        self.assertEqual(self.tool.invoke({"player_name": "없는선수", "pitcher_name": "고우석"})["relaxed"], [])
        self.assertEqual([i["batter"] for i in self.tool.invoke({"opponent_team_code": "KIA", "event": "홈런"})["items"]], ["문보경"])

    def test_pitcher_only_question_stays_in_pitchers_games(self):
        lg, _ = Team.objects.get_or_create(team_code="LG", defaults={"id": 990002, "team_name_ko": "LG 트윈스"})
        kt, _ = Team.objects.get_or_create(team_code="KT", defaults={"id": 990003, "team_name_ko": "KT 위즈"})
        kia = Team.objects.get(team_code="KIA")
        game = HighlightVideo.objects.create(video_id="vlg", title=TITLE, game_date=date(2026, 10, 5), status="analyzed", away_team=kia, home_team=lg)
        other = HighlightVideo.objects.create(video_id="vkt", title=TITLE, game_date=date(2026, 10, 5), status="analyzed", away_team=kt, home_team=kia)
        later = HighlightVideo.objects.create(video_id="vlg2", title=TITLE, game_date=date(2026, 10, 6), status="analyzed", away_team=kt, home_team=lg)
        HighlightScene.objects.create(video=later, start_sec=10, batting_team=kt, event="삼진",
                                      description="고우석이 커브로 강백호를 삼진으로 잡는다.")  # 설명만 있어도 고우석 → LG
        HighlightScene.objects.create(video=game, start_sec=800, batting_team=kia, batter_name="김도영", event="홈런")
        HighlightScene.objects.create(video=game, start_sec=100, batting_team=lg, batter_name="문보경", event="홈런")
        HighlightScene.objects.create(video=other, start_sec=50, batting_team=kt, batter_name="데이비슨", event="홈런")
        found = self.tool.invoke({"pitcher_name": "고우석", "event": "홈런", "date_from": "2026-10-05", "date_to": "2026-10-05"})
        self.assertEqual((found["relaxed"], [i["batter"] for i in found["items"]]), (["pitcher"], ["김도영"]))
        unknown = self.tool.invoke({"pitcher_name": "모르는투수", "event": "홈런", "date_from": "2026-10-05", "date_to": "2026-10-05"})
        self.assertEqual(unknown["count"], 0)  # 팀도 모르면 그날 아무 홈런이나 보여 주지 않는다

    def test_unspecified_question_returns_latest_one_and_exact_date_returns_that_day(self):
        latest = self.tool.invoke({"player_name": "김도영"})
        self.assertEqual([(i["video_id"], i["start_sec"]) for i in latest["items"]], [("vid1", 187)])
        that_day = self.tool.invoke({"player_name": "김도영", "date_from": "2026-10-06", "date_to": "2026-10-06"})
        self.assertEqual([i["start_sec"] for i in that_day["items"]], [187, 300])

    def test_unspecified_event_prefers_key_plays_in_game_order(self):
        video = HighlightVideo.objects.get(video_id="vid1")
        HighlightScene.objects.create(video=video, start_sec=30, event="삼진", description="1회 삼진")
        HighlightScene.objects.create(video=video, start_sec=60, event="아웃", description="1회 땅볼 아웃")
        self.assertEqual([i["start_sec"] for i in self.tool.invoke({"team_code": "KIA"})["items"]], [187])
        same_day = self.tool.invoke({"team_code": "KIA", "date_from": "2026-10-06", "date_to": "2026-10-06"})
        self.assertEqual([i["start_sec"] for i in same_day["items"]], [187, 300, 420])  # 1회 삼진·땅볼보다 홈런·안타

    def test_date_only_question_returns_one_key_play_per_game(self):
        other = HighlightVideo.objects.create(video_id="vid9", title=TITLE, game_date=date(2026, 10, 6), status="analyzed")
        HighlightScene.objects.create(video=other, start_sec=40, event="삼진")
        HighlightScene.objects.create(video=other, start_sec=500, event="2루타", rbi=2, description="2타점 적시 2루타")
        day = self.tool.invoke({"date_from": "2026-10-06", "date_to": "2026-10-06"})
        self.assertEqual([(i["video_id"], i["start_sec"]) for i in day["items"]], [("vid1", 187), ("vid9", 500)])

    def test_invalid_event_is_rejected_and_empty_result_keeps_metadata(self):
        self.assertIn("올바르지 않습니다", str(self.tool.invoke({"event": "끝내기"})))
        empty = self.tool.invoke({"player_name": "없는선수"})
        self.assertEqual((empty["count"], empty["analyzed_videos"]), (0, 1))


class HighlightAgentToolsTest(TestCase):
    """ask_highlight 하위 에이전트 도구: RAG 의미 검색, 검증된 장면 선택 → 서버가 만든 영상 카드."""

    def setUp(self):
        kia, _ = Team.objects.get_or_create(team_code="KIA", defaults={"id": 990001, "team_name_ko": "KIA 타이거즈"})
        video = HighlightVideo.objects.create(video_id="vid1AAAAAAA", title=TITLE, game_date=date(2026, 10, 5), status="analyzed",
                                              home_team=kia)
        self.homer = HighlightScene.objects.create(video=video, start_sec=187, inning=6, half="말", batting_team=kia,
                                                   batter_name="김도영", event="홈런", rbi=3, description="역전 3점 홈런")
        failed = HighlightVideo.objects.create(video_id="vid2BBBBBBB", title=TITLE, status="failed")
        self.hidden = HighlightScene.objects.create(video=failed, start_sec=10, event="홈런")
        tools = {tool.name: tool for tool in create_highlight_tools()}
        self.rag, self.select = tools["search_highlight_rag"], tools["select_highlight_scenes"]

    def test_rag_returns_analyzed_scenes_in_rank_order_with_score(self):
        with patch("llm.highlight_vectors.search", return_value=[(self.hidden.pk, 0.9), (self.homer.pk, 0.8)]) as search:
            result = self.rag.invoke({"query": "김도영 역전 홈런", "team_code": "KIA", "date_from": "2026-10-05"})
        self.assertEqual([(i["scene_id"], i["score"]) for i in result["items"]], [(self.homer.pk, 0.8)])
        self.assertNotIn("url", result["items"][0])
        self.assertEqual(search.call_args.args[1], ["KIA"])

    def test_rag_recent_first_searches_latest_game_day_first(self):
        newer = HighlightVideo.objects.create(video_id="vid3CCCCCCC", title=TITLE, game_date=date(2026, 10, 11), status="analyzed")
        catch = HighlightScene.objects.create(video=newer, start_sec=477, event="호수비", description="펜스 앞 점프 캐치")

        def fake(query, codes, date_from, date_to, event, k):  # 10/11 에만 장면이 있고, 날짜 없이 찾으면 10/5 가 더 가깝다
            return [(catch.pk, 0.7)] if date_from == date(2026, 10, 11) else [(self.homer.pk, 0.9)] if date_from is None else []
        with patch("llm.highlight_vectors.search", side_effect=fake) as search:
            plain = self.rag.invoke({"query": "멋진 수비", "event": "호수비"})
            recent = self.rag.invoke({"query": "멋진 수비", "event": "호수비", "recent_first": True})
        self.assertEqual(plain["items"][0]["scene_id"], self.homer.pk)
        self.assertEqual(recent["items"][0]["scene_id"], catch.pk)          # 가장 최근 경기(10/11)부터 찾는다
        self.assertEqual(search.call_args.args[2:4], (date(2026, 10, 11), date(2026, 10, 11)))

    def test_rag_without_index_or_with_error_tells_the_agent(self):
        with patch("llm.highlight_vectors.search", return_value=None):
            self.assertIn("색인", self.rag.invoke({"query": "다이빙 캐치"})["error"])
        with patch("llm.highlight_vectors.search", side_effect=RuntimeError("down")):
            self.assertIn("RuntimeError", self.rag.invoke({"query": "다이빙 캐치"})["error"])

    def test_select_builds_cards_from_db_not_from_model(self):
        message = self.select.invoke({"type": "tool_call", "id": "c1", "name": "select_highlight_scenes",
                                      "args": {"scene_ids": [self.homer.pk, self.hidden.pk, 999999], "note": "투수 미확인"}})
        self.assertEqual(message.artifact["videos"], [{
            "videoId": "vid1AAAAAAA", "start": 184, "gameDate": "2026-10-05",
            "label": "2026-10-05 KIA 6회말 김도영 홈런 3타점"}])
        content = json.loads(message.content)
        self.assertEqual(content["missing_scene_ids"], [self.hidden.pk, 999999])

    def test_highlight_videos_reads_nested_agent_records_and_rejects_bad_ids(self):
        from langchain_core.messages import ToolMessage
        from llm.v2.agent.chat_ui import highlight_videos
        good = {"videos": [{"videoId": "vid1AAAAAAA", "start": 184, "label": "x"},
                           {"videoId": "javascript:", "start": 1}, {"videoId": "vid1AAAAAAA", "start": -5}]}
        inner = ToolMessage("{}", name="select_highlight_scenes", tool_call_id="i1", artifact=good)
        outer = ToolMessage("요약", name="ask_highlight", tool_call_id="o1", artifact=[inner])
        self.assertEqual(highlight_videos([outer]), [{"videoId": "vid1AAAAAAA", "start": 184, "label": "x"}])
        failed = ToolMessage("err", name="ask_highlight", tool_call_id="o2", artifact=[inner], status="error")
        self.assertEqual(highlight_videos([failed]), [])

    def test_ask_highlight_is_a_fixed_specialist_with_three_tools(self):
        from llm.v2.agent import highlight_sub_agent, sub_agents
        self.assertIn("ask_highlight", sub_agents.SPECIALISTS)
        self.assertEqual(set(highlight_sub_agent.TOOLS),
                         {"search_highlight_scenes", "search_highlight_rag", "select_highlight_scenes"})


class DailyCrawlerTest(TestCase):
    def test_off_by_default_and_runs_recent_days_when_enabled(self):
        from crawling import kbo_highlights
        with patch("crawling.kbo_highlights._setup"), patch("llm.highlights.collect") as collect:
            with patch.dict("os.environ", {"HIGHLIGHT_AUTO_COLLECT": "0"}):
                self.assertIsNone(kbo_highlights.main(today=date(2026, 10, 9)))
            collect.assert_not_called()
            collect.return_value = {"found": 5, "analyzed": 5, "skipped": 0, "failed": 0, "cost": 0.5}
            with patch.dict("os.environ", {"HIGHLIGHT_AUTO_COLLECT": "1", "HIGHLIGHT_DAILY_BUDGET": "1.5"}):
                kbo_highlights.main(today=date(2026, 10, 9))
            collect.assert_called_once_with(since=date(2026, 10, 6), limit=20, budget=1.5, index_rag=True)

    def test_missing_key_does_not_fail_daily_run(self):
        from crawling import kbo_highlights
        with patch("crawling.kbo_highlights._setup"), patch.dict("os.environ", {"HIGHLIGHT_AUTO_COLLECT": "1"}), \
                patch("llm.highlights.collect", side_effect=highlights.HighlightError("YOUTUBE_API_KEY 가 없습니다.")):
            self.assertIsNone(kbo_highlights.main(today=date(2026, 10, 9)))
        import urllib.error
        with patch("crawling.kbo_highlights._setup"), patch.dict("os.environ", {"HIGHLIGHT_AUTO_COLLECT": "1"}), \
                patch("llm.highlights.collect", side_effect=urllib.error.URLError("YouTube 접속 실패")):
            self.assertIsNone(kbo_highlights.main(today=date(2026, 10, 9)))
