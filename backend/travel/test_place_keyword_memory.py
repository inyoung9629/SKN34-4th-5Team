"""Fictional keyword memory round trips. No crawling or paid API calls."""
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.test import TestCase
from django.utils import timezone

from .place_keyword_memory import record_keyword_memory, retrieve_keyword_memory
from .place_knowledge_models import KST, PlaceKnowledge, PlaceKnowledgeObservation, PlaceKnowledgeSource


GENERAL = {"scope": "general", "weekdays": [], "start_minute": None, "end_minute": None}


class KeywordMemoryTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        clock = patch("travel.place_keyword_memory.timezone.now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.place = PlaceKnowledge.objects.create(
            place_id="fixture:one", name="가상 식당", kind="food", base_source="fictional_fixture",
            base_checked_at=self.now, stadium_scope="external", stadium_code="JAMSIL")
        self.source = self.new_source("fixture/menu/v1")

    def new_source(self, key, **changes):
        return PlaceKnowledgeSource.objects.create(**{
            "source_key": key, "provider": "fictional_fixture", "url": "https://example.com/menu",
            "kind": "menu_listing", "access_method": "web", "fetched_at": self.now-timedelta(minutes=1),
            "storage_policy": "allowed", "allowed_attributes": ["menu", "cuisine", "review_feature"],
            "policy_reference": "직접 작성한 가상 테스트 데이터", "policy_checked_at": self.now,
            **changes})

    def keyword(self, **changes):
        return {"attribute": "menu", "term": "돈까스", "polarity": "positive", "basis": "menu_listing",
                "same_place_verified": True, "evidence_verified": True, "body_read": True,
                "context": dict(GENERAL), "checked_at": self.now,
                "valid_until": self.now+timedelta(days=30), "extractor_version": "fixture-v1", **changes}

    def feature(self, **changes):
        return self.keyword(attribute="review_feature", term="조용함", review_category="atmosphere",
                            basis="customer_experience", experience_key="opaque-review-1",
                            promotion="not_disclosed", observed_on=self.now.astimezone(KST).date(),
                            observation_date_kind="published", **changes)

    def store(self, keywords=None, source=None, place=None):
        return record_keyword_memory(place_id=(place or self.place).pk,
                                     source_id=(source or self.source).pk,
                                     keywords=keywords if keywords is not None else [self.keyword()])

    def read(self, term="돈까스", **kwargs):
        return retrieve_keyword_memory(term, now=self.now, **kwargs)

    def test_write_then_retrieve_without_reindexing_or_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            saved = self.store()
            result = self.read()
        self.assertEqual(saved["created"], 1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["term"], "돈까스")
        self.assertFalse(result["network_used"])
        self.assertEqual((saved["search_calls"], saved["model_calls"]), (0, 0))
        row = PlaceKnowledgeObservation.objects.get()
        self.assertEqual(row.summary, "menu:돈까스 [positive]")
        self.assertEqual(row.observed_label, "")
        self.assertNotIn("summary", result["items"][0])
        self.assertEqual(PlaceKnowledge.objects.count(), 1)

    def test_red_only_memory_is_hidden_and_green_is_internal_at_read_time(self):
        from .stadium_facilities import _root
        import json
        locations = json.loads((_root() / "stadium_locations.json").read_text(encoding="utf-8"))["stadiums"]
        main = locations["CHANGWON"]
        old = main["excluded"][0]
        self.store()
        self.place.lat, self.place.lng = old["lat"], old["lng"]
        self.place.save()
        self.assertEqual(self.read(scope="all")["count"], 0)
        self.place.lat, self.place.lng = main["lat"], main["lng"]
        self.place.save()
        self.assertEqual(self.read()["count"], 0)
        self.assertEqual(self.read(scope="internal")["count"], 1)
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 1)

    def test_repeat_is_deduplicated_but_new_source_revision_is_preserved(self):
        first = self.store()
        replay = self.store()
        self.assertEqual(replay["created"], 0)
        self.assertEqual(replay["observation_ids"], first["observation_ids"])
        second = self.new_source("fixture/menu/v2")
        self.store(source=second)
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 2)

    def test_repeated_read_does_not_renew_evidence_age(self):
        self.store()
        with self.assertRaises(ValidationError):
            self.store([self.keyword(valid_until=self.now+timedelta(days=31))])
        self.assertEqual(PlaceKnowledgeObservation.objects.get().valid_until, self.now+timedelta(days=30))

    def test_keyword_normalization_and_qualifier_order_are_idempotent(self):
        self.store([self.keyword(term="  돈까스  ", qualifiers=["등심", "튀김", "등심"])])
        result = self.store([self.keyword(qualifiers=["튀김", "등심"])])
        self.assertEqual(result["created"], 0)
        self.assertEqual(self.read(" 돈까스 ")["count"], 1)

    def test_raw_page_review_quote_photo_and_identity_fields_rejected(self):
        for key in ("raw_html", "body_text", "review_text", "summary", "quote", "photo_url",
                    "reviewer_name", "address", "lat", "api_response", "place_id", "source_id"):
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.store([self.keyword(**{key: "DO_NOT_STORE_RAW"})])
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 0)

    def test_batch_keeps_all_review_keywords_and_one_experience_identity(self):
        source = self.new_source("fixture/review/1", kind="customer_review")
        one = self.feature()
        two = {**one, "term": "매장 청결", "review_category": "cleanliness"}
        three = {**one, "term": "넓은 좌석", "review_category": "space"}
        saved = self.store([one, two, three], source=source)
        self.assertEqual(saved["created"], 3)
        for term in ("조용함", "매장 청결", "넓은 좌석"):
            self.assertEqual(self.read(term)["count"], 1)
        self.assertEqual(set(PlaceKnowledgeObservation.objects.values_list("experience_key", flat=True)),
                         {"opaque-review-1"})

    def test_wrong_branch_unverified_flags_and_ai_summary_cannot_pass(self):
        for change in ({"same_place_verified": False}, {"evidence_verified": False},
                       {"body_read": False}, {"same_place_verified": "true"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.store([self.keyword(**change)])
        summary = self.new_source("fixture/summary", kind="platform_summary")
        with self.assertRaises(ValidationError):
            self.store([self.feature()], source=summary)
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 0)

    def test_no_partial_write_if_one_keyword_is_invalid(self):
        with self.assertRaises(ValidationError):
            self.store([self.keyword(), self.keyword(term="카페라떼", body_read=False)])
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 0)

    def test_no_auto_approval_including_kakao_provider_or_api_source(self):
        for status in ("unreviewed", "blocked"):
            source = self.new_source("kakao/"+status, provider="kakao", storage_policy=status)
            with self.assertRaises(ValidationError):
                self.store(source=source)
        self.source.allowed_attributes = ["cuisine"]
        self.source.save()
        with self.assertRaises(ValidationError):
            self.store()
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 0)

    def test_revocation_hides_memory_and_blocks_even_replay(self):
        self.store()
        self.source.storage_policy = "blocked"
        self.source.save()
        self.assertEqual(self.read()["count"], 0)
        with self.assertRaises(ValidationError):
            self.store()

    def test_freshness_retraction_and_retention_checked_at_query_time(self):
        self.store()
        expired = retrieve_keyword_memory("돈까스", now=self.now+timedelta(days=30))
        self.assertEqual(expired["count"], 0)
        self.source.storage_policy = "temporary"
        self.source.retention_until = self.now+timedelta(hours=1)
        self.source.save()
        expired = retrieve_keyword_memory("돈까스", now=self.now+timedelta(hours=1))
        self.assertEqual(expired["count"], 0)
        row = PlaceKnowledgeObservation.objects.get()
        row.review_status = "retracted"
        row.save()
        self.assertEqual(self.read()["count"], 0)

    def test_negative_memory_not_lost_or_promoted_to_positive(self):
        self.store()
        source = self.new_source("fixture/menu/negative")
        self.store([self.keyword(polarity="negative", basis="explicit_non_sale")], source=source)
        result = self.read()
        self.assertEqual({row["polarity"] for row in result["items"]}, {"positive", "negative"})
        self.assertTrue(result["evidence_only"])
        self.assertNotIn("verdict", result)

    def test_same_name_different_place_and_stadium_scope_filters(self):
        self.store()
        other = PlaceKnowledge.objects.create(
            place_id="fixture:two", name=self.place.name, kind="cafe", base_source="fictional_fixture",
            base_checked_at=self.now, stadium_scope="stadium_internal", stadium_code="SUWON")
        self.store(place=other)
        self.assertEqual(self.read()["count"], 1)
        self.assertEqual(self.read(place_id=other.pk)["count"], 0)
        result = self.read(stadium_code="SUWON", scope="internal", kind="cafe")
        self.assertEqual([row["place_id"] for row in result["items"]], [other.pk])
        self.assertEqual(self.read(scope="all")["count"], 2)

    def test_limited_review_context_not_used_as_general_trait(self):
        source = self.new_source("fixture/review/limited", kind="customer_review")
        arrival = self.now.replace(hour=14, minute=0, second=0, microsecond=0).astimezone(KST)
        context = {"scope": "limited", "weekdays": [arrival.weekday()],
                   "start_minute": arrival.hour*60, "end_minute": arrival.hour*60+60}
        self.store([self.feature(context=context, qualifiers=["좌석:테라스"])], source=source)
        self.assertEqual(self.read("조용함")["count"], 0)
        args = {"arrival": arrival, "departure": arrival+timedelta(minutes=30)}
        self.assertEqual(self.read("조용함", **args)["count"], 0)
        self.assertEqual(self.read("조용함", review_conditions=["좌석:테라스"], **args)["count"], 1)

    def test_one_review_can_describe_opposing_time_contexts_without_overwriting(self):
        source = self.new_source("fixture/review/times", kind="customer_review")
        day = {"scope": "limited", "weekdays": [], "start_minute": 720, "end_minute": 900}
        night = {"scope": "limited", "weekdays": [], "start_minute": 1080, "end_minute": 1260}
        result = self.store([self.feature(context=day), self.feature(context=night, polarity="negative")],
                            source=source)
        self.assertEqual(result["created"], 2)
        self.assertEqual(self.read("조용함")["count"], 0)

    def test_old_ad_unknown_context_and_missing_menu_stay_unknown(self):
        source = self.new_source("fixture/review/old", kind="customer_review")
        for i, changes in enumerate((
            {"promotion": "disclosed"}, {"promotion": "unknown"},
            {"observed_on": self.now.astimezone(KST).date()-timedelta(days=181)},
            {"context": {**GENERAL, "scope": "unknown"}},
        )):
            self.store([{**self.feature(), "term": "조용함"+str(i), **changes}], source=source)
            self.assertEqual(self.read("조용함"+str(i))["count"], 0)
        self.assertEqual(self.read("치즈돈까스")["count"], 0)

    def test_invalid_bounds_strings_and_future_dates(self):
        for term in ("", "<html>menu</html>", "https://example.com", "ｈｔｔｐｓ：／／example.com", "원문\n여러 줄", "가"*81):
            with self.subTest(term=term), self.assertRaises(ValidationError):
                self.store([self.keyword(term=term)])
        for keywords in ([], {}, ["raw text"], [self.keyword()]*101):
            with self.subTest(keywords_type=type(keywords)), self.assertRaises(ValidationError):
                self.store(keywords)
        for changes in ({"checked_at": self.now+timedelta(seconds=1)},
                        {"valid_until": self.now}, {"checked_at": self.now.replace(tzinfo=None)}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.store([self.keyword(**changes)])
        for args in ({"limit": True}, {"limit": 21}, {"scope": "oops"}, {"kind": "oops"},
                     {"stadium_code": "oops"}, {"review_conditions": "테라스"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.read(**args)

    def test_truncation_and_storage_unavailability_are_explicit(self):
        self.store()
        self.store(source=self.new_source("fixture/menu/more"))
        result = self.read(limit=1)
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["truncated"])
        with patch("travel.place_keyword_memory.PlaceKnowledgeObservation.objects.filter",
                   side_effect=OperationalError("secret-db-address")):
            result = self.read()
        self.assertEqual(result["status"], "memory_unavailable")
        self.assertNotIn("secret-db-address", str(result))

    def test_model_facing_rag_can_read_memory_but_cannot_write_it(self):
        from llm.tools.place_rag import create_place_rag_tool
        self.store()
        # The catalogue can be unavailable; keyword memory remains independent.
        with patch("travel.place_rag.retrieve", return_value={"status": "index_unavailable", "items": []}):
            tool = create_place_rag_tool()
            result = tool.invoke({"keyword_term": "돈까스", "stadium_code": "JAMSIL"})
        self.assertEqual(result["keyword_memory"]["count"], 1)
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 1)
        self.assertNotIn("keywords", tool.args_schema.model_fields)

    def test_plain_catalogue_tool_does_not_query_memory(self):
        from llm.tools.place_rag import search_place_knowledge
        with patch("travel.place_rag.retrieve", return_value={"items": []}), patch(
                "travel.place_keyword_memory.retrieve_keyword_memory", side_effect=AssertionError("Not requested")):
            self.assertEqual(search_place_knowledge("카페"), {"items": []})
