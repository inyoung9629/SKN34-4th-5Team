from unittest.mock import patch
from django.test import SimpleTestCase
from baseball.stadium_locations import reviewed_venue
from llm.v1.rag.course import agent


class StadiumLocationTests(SimpleTestCase):
    def test_old_rag_address_coordinate_cannot_move_the_first_team_venue(self):
        with patch.object(agent, "connection") as connection:
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = ({"lat_y":"37.51619878","lng_x":"127.07594059"},)
            for code in agent.STADIUM_KO:
                anchor = agent.stadium_anchor(code)
                point = reviewed_venue(code)
                self.assertEqual((anchor["lat"], anchor["lng"]), (point["lat"], point["lng"]))

    def test_unknown_venue_is_not_replaced_by_the_nearest_registered_venue(self):
        self.assertIsNone(reviewed_venue("YOUTH_UNKNOWN"))
