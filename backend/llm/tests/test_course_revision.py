from copy import deepcopy
from unittest.mock import Mock
from django.test import SimpleTestCase
from llm.v2.course.revision import prepare_revision, RevisionClarification
from llm.v2.course.itinerary_planner import ItineraryPlanner
from llm.v2.course.knowledge_verification import KnowledgeVerification, KnowledgeReviewVerifier
from .test_itinerary_planner import NOW, GAME, STADIUM, COVERAGE, place, request, route
from .test_knowledge_verification import memory


ANCHOR={'id':1,'stadium_code':'JAMSIL','starts_at':GAME.isoformat()}


class CourseRevisionTests(SimpleTestCase):
    def setUp(self):
        self.food=place('kept',30)
        self.cafe=place('old',50,'cafe')
        self.spec=request(stops=[{'kind':'food'},{'kind':'cafe'}])
        result=ItineraryPlanner([self.food,self.cafe],STADIUM,COVERAGE,route).plan(self.spec,GAME,now=NOW)
        self.previous={'selected_game':ANCHOR,'itinerary_request':self.spec.model_dump(mode='json'),
                       'itinerary_result':result}

    def test_replace_cafe_keeps_actual_food_and_excludes_old_cafe(self):
        req=self.spec.model_copy(update={'replace_stop_indices':[1]})
        req.stops[0].required_keywords=['kept']
        pinned,excluded=prepare_revision(req,self.previous,ANCHOR)
        candidate=Mock()
        candidate.policy.wall_seconds=0
        candidate.audit.return_value={}
        candidate.search.return_value=[self.cafe,place('new',70,'cafe')]
        result=ItineraryPlanner([],STADIUM,COVERAGE,route,candidate_source=candidate,
            pinned_stops=pinned,excluded_place_ids=excluded).plan(req,GAME,now=NOW)
        self.assertEqual(result['status'],'ok')
        self.assertEqual([s['name'] for s in result['stops']],['kept','new','stadium'])
        self.assertEqual({c.args[2] for c in candidate.search.call_args_list},{'cafe'})
        self.assertEqual(result['stadium_arrival_at'],GAME.replace(minute=10).isoformat())

    def test_ambiguous_structure_game_changes_and_failed_baseline_do_not_pin(self):
        req=self.spec.model_copy(update={'replace_stop_indices':[1]})
        variants=[({},ANCHOR), (self.previous,{**ANCHOR,'id':2}),
                  (dict(self.previous,itinerary_result={'status':'no_match'}),ANCHOR)]
        for previous,anchor in variants:
            with self.assertRaises(RevisionClarification):
                prepare_revision(req,previous,anchor)
        req.stops[0].excluded_keywords=['new-hard-condition']
        with self.assertRaises(RevisionClarification):
            prepare_revision(req,self.previous,ANCHOR)

    def test_time_edit_keeps_all_but_does_not_skip_total_timing_validation(self):
        req=self.spec.model_copy(update={'replace_stop_indices':[], 'start_at':GAME.replace(hour=18,minute=0)})
        pinned,excluded=prepare_revision(req,self.previous,ANCHOR)
        result=ItineraryPlanner([],STADIUM,COVERAGE,route,pinned_stops=pinned,
            excluded_place_ids=excluded).plan(req,GAME,now=NOW)
        self.assertEqual(result['status'],'time_infeasible')
        self.assertEqual(result['stops'],[])

    def test_pinned_unknown_review_does_not_repeat_paid_search(self):
        req=request(stops=[{'kind':'cafe','reviews':{'all_of':[{'aspect':'cozy','priority':'preferred'}]}}])
        session=KnowledgeVerification(req,web_enabled=True,memory=memory(),now=NOW)
        session.allow_web=True
        session.reuse_only_ids={self.cafe['placeId']}
        session.research.run=Mock(side_effect=AssertionError('no paid search for retained place'))
        KnowledgeReviewVerifier(session).verify(self.cafe,req.stops[0].reviews)
        session.research.run.assert_not_called()
