"""Category expansion: same evidence bar and shared budget, no network."""
from unittest.mock import Mock
from django.test import SimpleTestCase
from pydantic import ValidationError
from llm.v2.course.itinerary_request import StopRequest
from llm.v2.course.itinerary_planner import ItineraryPlanner
from llm.v2.course.knowledge_verification import KnowledgeVerification, KnowledgeFoodVerifier, KnowledgeReviewVerifier
from llm.v2.course.review_verification import evaluate_reviews
from llm.v2.course.serper_evidence import SerperResearch
from .test_itinerary_planner import NOW, GAME, STADIUM, COVERAGE, place, request, route
from .test_knowledge_verification import fact, review, memory


class CategoryExpansionTests(SimpleTestCase):
    def test_cafe_menu_and_subjective_preference_use_same_evidence(self):
        req = request(stops=[{'kind':'cafe', 'food':{'all_of':[{'any_of':[
            {'kind':'menu','name':'카페라떼'}]}]}, 'reviews':{'all_of':[{'aspect':'cozy'}]}}])
        facts = [fact(term='카페라떼'), *[dict(review(i), attribute='cozy') for i in (1,2)]]
        research = SerperResearch(searcher=Mock(side_effect=AssertionError('no paid search')), now=NOW)
        session = KnowledgeVerification(req, memory=memory(facts), research=research, now=NOW)
        planner = ItineraryPlanner([place('cafe',50,'cafe')],STADIUM,COVERAGE,route,
            food_verifier=KnowledgeFoodVerifier(session),review_verifier=KnowledgeReviewVerifier(session))
        result = planner.plan(req,GAME,now=NOW)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['stops'][0]['food_verification']['status'],'pass')
        self.assertEqual(result['stops'][0]['review_verification']['status'],'pass')
        research.searcher.assert_not_called()

    def test_walk_comfort_is_not_an_access_guarantee(self):
        req = request(stops=[{'kind':'walk','reviews':{'all_of':[{'aspect':'walking_comfort'}]},
                             'unverified_requirements':['야간 출입 가능']}])
        session = KnowledgeVerification(req,memory=memory([dict(review(i),attribute='walking_comfort') for i in (1,2)]),now=NOW)
        planner = ItineraryPlanner([place('park',50,'walk')],STADIUM,COVERAGE,route,
            review_verifier=KnowledgeReviewVerifier(session))
        self.assertEqual(planner.plan(req,GAME,now=NOW)['status'],'constraints_unverified')
        req.stops[0].unverified_requirements=[]
        self.assertEqual(planner.plan(req,GAME,now=NOW)['status'],'ok')

    def test_schema_does_not_allow_cafe_cuisine_or_walk_menu(self):
        for args in ({'kind':'cafe','cuisine':'일식'}, {'kind':'walk','food':{
                'all_of':[{'any_of':[{'kind':'menu','name':'커피'}]}]}}):
            with self.assertRaises(ValidationError):
                StopRequest.model_validate(args)

    def test_all_categories_share_four_searches(self):
        searcher=Mock(return_value=[])
        research=SerperResearch(searcher=searcher,now=NOW)
        for kind in ('food','cafe','walk'):
            research.run(place(kind,50,kind),{},['scenic_view'],sufficient=lambda _:False)
        self.assertEqual(searcher.call_count,4)
        self.assertEqual(research.audit()['openai_web_search_calls'],0)
