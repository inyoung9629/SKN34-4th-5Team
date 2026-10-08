"""되돌림으로 빠진 좌표 보호: 현행 DB 우선 경로를 유지하고 오래된 fallback만 보정한다."""
from unittest.mock import patch
from django.test import SimpleTestCase
from baseball.stadium_locations import reviewed_venue
from llm.v1.rag.course import agent
from llm.v1.rag.nearby import kakao


class StadiumLocationTests(SimpleTestCase):
    def test_old_rag_address_coordinate_cannot_move_first_team_venue(self):
        with patch.object(agent, "invoke_domain_tool", return_value={}), patch(
            "llm.vector_store.iter_documents",
            side_effect=lambda **_kwargs: iter([{"metadata": {"lat_y": "37.51619878", "lng_x": "127.07594059"}}]),
        ):
            for code in agent.STADIUM_KO:
                anchor = agent.stadium_anchor(code)
                point = reviewed_venue(code)
                self.assertEqual((anchor["lat"], anchor["lng"]), (point["lat"], point["lng"]))

    def test_database_stadium_remains_primary(self):
        stadium = {"stadium_name_ko": "정식 구장", "latitude": 37.5, "longitude": 127.0}
        with patch.object(agent, "invoke_domain_tool", return_value={"item": stadium}):
            anchor = agent.stadium_anchor("JAMSIL")
        self.assertEqual((anchor["lat"], anchor["lng"]), (37.5, 127.0))

    def test_stale_cached_kakao_address_is_corrected(self):
        with patch.object(kakao, "_cache") as cache:
            cache.return_value.get.return_value = {"name": "잠실", "lat": 0, "lng": 0, "address": ""}
            point = kakao.resolve_stadium("JAMSIL")
        self.assertEqual((point["lat"], point["lng"]), (reviewed_venue("JAMSIL")["lat"], reviewed_venue("JAMSIL")["lng"]))

    def test_unknown_venue_is_not_replaced_by_nearest_registered_venue(self):
        self.assertIsNone(reviewed_venue("YOUTH_UNKNOWN"))
