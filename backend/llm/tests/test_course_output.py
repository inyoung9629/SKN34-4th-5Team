from unittest.mock import patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_from_dict, messages_to_dict

from llm.serializer.message import done_payload, project_history, public_frame
from llm.v2.agent.course_output import public_course
from llm.v1.rag.course import agent as course


COURSE = {"stadiumCode": "JAMSIL", "places": [
    {"name": "잠실 식당", "phase": "BEFORE", "category": "FOOD", "lat": 37.511, "lng": 127.075, "placeId": "1"},
    {"name": "잠실야구장", "phase": "GAME", "category": "STADIUM", "lat": 37.512, "lng": 127.071},
]}


class CourseOutputTest(SimpleTestCase):
    def test_only_actual_safe_source_urls_are_published(self):
        for url, expected in (("http://place.map.kakao.com/123", "https://place.map.kakao.com/123"),
                              ("https://example.org/place/1", "https://example.org/place/1"),
                              ("javascript:alert(1)", None), ("//example.org/1", None),
                              ("https://secret@example.org/1", None), ("https://example.org/\n1", None), (None, None)):
            with self.subTest(url=url):
                raw = {**COURSE, "places": [{**COURSE["places"][0], "placeUrl": url}, COURSE["places"][1]]}
                actual = public_course(raw)
                self.assertEqual(actual["places"][0].get("placeUrl"), expected)
                self.assertNotIn("placeUrl", actual["places"][1])

    def test_origin_and_entry_are_public_coordinates_without_raw_routing_data(self):
        public = public_course({**COURSE,
            "origin": {"lat": 37.55, "lng": 126.97, "name": "서울역", "private": "secret"},
            "entryPoint": {"lat": 37.512, "lng": 127.044, "paths": ["private"]},
            "approachNotice": "서쪽 진입점부터 코스를 골랐어요.",
        })
        self.assertEqual(public["origin"], {"lat": 37.55, "lng": 126.97, "name": "서울역"})
        self.assertEqual(public["entryPoint"], {"lat": 37.512, "lng": 127.044})
        self.assertNotIn("private", str(public))
        self.assertEqual(public_course({**COURSE, "origin": {"lat": float("nan"), "lng": 127}}), COURSE)

    def test_persisted_course_reaches_done_and_history_without_private_tool_data(self):
        human, answer = HumanMessage("코스", id="h"), AIMessage("완성", id="a")
        call = AIMessage("", tool_calls=[{"name": "plan_course", "id": "c", "args": {"request": "PRIVATE"}}])
        tool = ToolMessage("PRIVATE TOOL RESULT", name="plan_course", tool_call_id="c",
                           artifact={"course": {**COURSE, "sources": ["PRIVATE"], "timing": {"secret": 1}}})
        messages = messages_from_dict(messages_to_dict([human, call, tool, answer]))
        turns = {"h": {"status": "completed", "answer_id": "a"}}
        self.assertEqual(project_history(messages, turns)[-1]["course"], COURSE)
        public = public_frame("done", done_payload(messages, turns), False)[1]
        self.assertEqual(public["course"], COURSE)
        self.assertNotIn("PRIVATE", str(public))

    def test_invalid_optional_coordinates_do_not_break_answer(self):
        for changes in ({"lat": float("nan")}, {"lng": float("inf")}, {"lat": True}, {"lat": 100}, {"phase": []}):
            self.assertIsNone(public_course({"places": [{**COURSE["places"][0], **changes}]}))
        self.assertIsNone(public_course({"places": [COURSE["places"][1]]}))

    def test_time_warning_is_optional_and_does_not_drop_map_places(self):
        warning = "방문 순서 2번째 장소(카페)부터는 경기 전에 방문하기 어려워요."
        self.assertEqual(public_course({**COURSE, "timeWarning": warning}), {**COURSE, "timeWarning": warning})
        self.assertEqual(public_course({**COURSE, "timeWarning": {"private": "bad"}}), COURSE)

    def test_selected_stadium_beats_history_but_current_question_still_wins(self):
        history = [{"role": "user", "content": "창원에서 코스 짜줘"}, {"role": "assistant", "content": "창원 코스"}]
        for question, hint, expected in (("코스 짜줘", "JAMSIL", "JAMSIL"),
                                         ("고척 코스 짜줘", "JAMSIL", "GOCHEOK"),
                                         ("이번엔 롯데로 짜줘", "JAMSIL", "SAJIK"),
                                         ("잠실 말고 사직으로 바꿔줘", "JAMSIL", "SAJIK"),
                                         ("롯데 경기지만 잠실에서 볼 거야", "SAJIK", "JAMSIL"),
                                         ("삼성 대신 한화 코스로", "DAEGU", "DAEJEON"),
                                         ("엔씨 보러 갈 거야", "JAMSIL", "CHANGWON"),
                                         ("케이티 코스", "JAMSIL", "SUWON"),
                                         ("쓱 코스", "JAMSIL", "MUNHAK"),
                                         ("코스 짜줘", None, "CHANGWON")):
            with self.subTest(expected=expected), patch.object(course, "load_schedule", return_value=({"items": []}, 1)) as load:
                course.answer(question, history=history, hint_stadium=hint)
            self.assertEqual(load.call_args.args[0], expected)

    def test_other_stadium_date_is_not_carried_to_selected_stadium(self):
        history = [{"role": "user", "content": "10월 7일 창원 코스"}]
        self.assertIsNone(course.requested_date("코스", history, "2026-10-04", "JAMSIL"))
