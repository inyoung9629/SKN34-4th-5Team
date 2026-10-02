from unittest.mock import Mock
from django.test import SimpleTestCase
from llm.v2.course.itinerary_planner import ItineraryPlanner, evidence_quality
from .test_itinerary_planner import NOW, GAME, STADIUM, COVERAGE, place, request, route


class CourseRankingTests(SimpleTestCase):
    def test_no_origin_uses_actual_stadium_trip_not_straight_line(self):
        near, farther = place('near',20), place('farther',80)
        def directions(mode,points):
            value=route(mode,points)
            value['seconds']=1200 if points[0]['lat']==near['lat'] else 180
            return value
        router=Mock(side_effect=directions)
        result=ItineraryPlanner([near,farther],STADIUM,COVERAGE,router).plan(request(),GAME,now=NOW)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['stops'][0]['name'],'farther')
        self.assertEqual(router.call_count,2)  # Final validation reuses onward route.
        self.assertFalse(result['origin_included'])

    def test_preference_never_overrides_deadline(self):
        near,preferred = place('near',20),place('preferred',80)
        def directions(mode,points):
            value=route(mode,points)
            value['seconds']=3600 if points[0]['lat']==preferred['lat'] else 180
            return value
        req=request(stops=[{'kind':'food','preferred_keywords':['preferred']}],
                    start_at=GAME.replace(hour=17,minute=0))
        result=ItineraryPlanner([near,preferred],STADIUM,COVERAGE,directions).plan(req,GAME,now=NOW)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['stops'][0]['name'],'near')

    def test_unknown_or_many_duplicate_sources_do_not_inflate_evidence(self):
        self.assertEqual(evidence_quality({'status':'unknown','sources':[{'url':'x'}]*20},None),0)
        self.assertEqual(evidence_quality({'status':'pass','sources':[{'url':'x'}]*20},None),1)
