"""Storage contract tests using fictional data; no API/model/browser calls."""
from datetime import datetime, timedelta
from decimal import Decimal
from importlib import import_module
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.state import ProjectState
from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from .place_knowledge import context_applies, record_observation, record_review_observations, reusable_observations
from .place_knowledge_models import (
    KST, PlaceEnrichmentAttempt, PlaceKnowledge, PlaceKnowledgeObservation,
    PlaceKnowledgeSource, validate_context, validate_reference_url,
)


GENERAL = {"scope": "general", "weekdays": [], "start_minute": None, "end_minute": None}
LIMITED = {"scope": "limited", "weekdays": [0, 1, 2, 3, 4], "start_minute": 720, "end_minute": 1080}


class PlaceKnowledgeTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.clock = patch("travel.place_knowledge_models.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.place = PlaceKnowledge.objects.create(
            place_id="collected:SBIZ:fictional-cafe", name="가상 테스트 카페", kind="cafe",
            base_source="fictional_fixture", base_version="v1", base_checked_at=self.now)
        self.source = self.make_source()

    def make_source(self, **changes):
        return PlaceKnowledgeSource.objects.create(**{
            "source_key": "fixture/menu/v1", "provider": "fictional_fixture",
            "url": "https://example.com/menu", "kind": "menu_listing", "access_method": "web",
            "fetched_at": self.now - timedelta(minutes=1), "storage_policy": "allowed",
            "allowed_attributes": ["menu", "cuisine", "quietness", "cleanliness", "review_feature"],
            "policy_reference": "테스트용으로 직접 작성한 가상 정보", "policy_checked_at": self.now,
            **changes})

    def payload(self, **changes):
        return {"ingest_key": "fixture/menu/1", "place": self.place, "source": self.source,
                "attribute": "menu", "term": "돈까스", "observed_label": "돈카츠", "qualifiers": ["등심"],
                "polarity": "positive", "basis": "menu_listing", "summary": "등심 돈카츠 메뉴 확인",
                "review_status": "accepted", "same_place_verified": True, "evidence_verified": True,
                "body_read": True, "context": dict(GENERAL), "checked_at": self.now,
                "valid_until": self.now + timedelta(days=30), "extractor_version": "fictional-fixture-v1", **changes}

    def review(self, **changes):
        source = self.make_source(source_key="fixture/review/1", kind="customer_review", url="https://example.com/review")
        return self.payload(source=source, attribute="quietness", term="", observed_label="", qualifiers=[],
                            basis="customer_experience", summary="평일 오후에 조용했다는 방문 경험",
                            context=dict(LIMITED), experience_key="opaque-experience-1",
                            promotion="not_disclosed", observed_on=self.now.astimezone(KST).date(),
                            observation_date_kind="published", **changes)

    def feature(self, **changes):
        return {**self.review(), "attribute": "review_feature", "term": "직원 친절",
                "review_category": "service", "summary": "직원 응대가 친절했다는 방문 경험",
                "context": dict(GENERAL), **changes}

    def batch_item(self, payload, **changes):
        return {**{k: v for k, v in payload.items() if k not in ("place", "source")}, **changes}

    def test_place_reuses_catalogue_id_and_nullable_unknowns(self):
        self.place.refresh_from_db()
        self.assertEqual(self.place.pk, "collected:SBIZ:fictional-cafe")
        self.assertIsNone(self.place.lat)
        self.assertIsNone(self.place.requires_ticket)
        self.assertEqual(self.place.stadium_scope, "unknown")

    def test_coordinate_pair_and_stadium_code_validation(self):
        self.place.lat = 37.5
        with self.assertRaises(ValidationError):
            self.place.save()
        self.place.lat = None
        self.place.stadium_scope = "stadium_internal"
        with self.assertRaises(ValidationError):
            self.place.save()

    def test_save_read_and_synonym_label_are_separate(self):
        row, created = record_observation(**self.payload())
        self.assertTrue(created)
        saved = reusable_observations(self.place.pk, attribute="menu", term="돈까스", now=self.now)
        self.assertEqual([v.pk for v in saved], [row.pk])
        self.assertEqual((saved[0].term, saved[0].observed_label, saved[0].qualifiers), ("돈까스", "돈카츠", ["등심"]))
        self.assertEqual(reusable_observations(self.place.pk, attribute="menu", term="치즈돈까스", now=self.now), [])

    def test_idempotent_insert_and_changed_payload_rejected(self):
        first, _ = record_observation(**self.payload())
        second, created = record_observation(**self.payload())
        self.assertEqual(first.pk, second.pk)
        self.assertFalse(created)
        with self.assertRaises(ValidationError):
            record_observation(**self.payload(summary="다른 내용"))
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 1)

    def test_direct_save_does_not_bypass_grounding_validation(self):
        for change in ({"same_place_verified": False}, {"evidence_verified": False}, {"body_read": False},
                       {"valid_until": None}, {"basis": "inference"}, {"basis": "insufficient"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                PlaceKnowledgeObservation(**self.payload(**change)).save()

    def test_missing_menu_is_unknown_not_non_sale(self):
        row, _ = record_observation(**self.payload(polarity="unknown", basis="insufficient", review_status="pending", summary="요청 메뉴 미확인"))
        self.assertEqual(row.polarity, "unknown")
        self.assertEqual(reusable_observations(self.place.pk, attribute="menu", term="돈까스", now=self.now), [])
        with self.assertRaises(ValidationError):
            record_observation(**self.payload(ingest_key="bad-negative", polarity="negative"))
        negative, _ = record_observation(**self.payload(ingest_key="direct-negative", polarity="negative",
                                                      basis="explicit_non_sale", summary="공식 판매 종료 안내 확인"))
        self.assertEqual(negative.polarity, "negative")

    def test_unreviewed_blocked_and_out_of_scope_sources_rejected(self):
        for status in ("unreviewed", "blocked"):
            self.source.storage_policy = status
            self.source.save()
            with self.assertRaises(ValidationError):
                record_observation(**self.payload(review_status="rejected"))
        self.source.storage_policy = "allowed"
        self.source.allowed_attributes = ["cuisine"]
        self.source.save()
        with self.assertRaises(ValidationError):
            record_observation(**self.payload())

    def test_temporary_requires_expiry_and_respects_retention(self):
        self.source.storage_policy = "temporary"
        with self.assertRaises(ValidationError):
            self.source.save()
        self.source.retention_until = self.now + timedelta(hours=1)
        self.source.save()
        record_observation(**self.payload())
        self.assertEqual(reusable_observations(self.place.pk, attribute="menu", term="돈까스", now=self.now + timedelta(hours=1)), [])

    def test_expired_evidence_not_reused(self):
        record_observation(**self.payload(valid_until=self.now+timedelta(minutes=1)))
        self.assertEqual(reusable_observations(self.place.pk, attribute="menu", term="돈까스", now=self.now+timedelta(minutes=1)), [])

    def test_policy_revocation_hides_evidence_and_allows_retraction(self):
        row, _ = record_observation(**self.payload())
        self.source.storage_policy = "blocked"
        self.source.save()
        self.assertEqual(reusable_observations(self.place.pk, attribute="menu", term="돈까스", now=self.now), [])
        row.source.refresh_from_db()
        row.review_status = "retracted"
        row.save()

    def test_stale_source_object_cannot_bypass_revocation(self):
        other = PlaceKnowledgeSource.objects.get(pk=self.source.pk)
        other.storage_policy = "blocked"
        other.save()
        with self.assertRaises(ValidationError):
            record_observation(**self.payload())

    def test_source_and_observation_content_are_versioned_not_overwritten(self):
        row, _ = record_observation(**self.payload())
        row.summary = "원래와 다른 근거"
        with self.assertRaises(ValidationError):
            row.save()
        self.source.url = "https://example.com/another-branch"
        with self.assertRaises(ValidationError):
            self.source.save()

    def test_review_context_not_promoted_to_general(self):
        record_observation(**self.review())
        self.assertEqual(reusable_observations(self.place.pk, attribute="quietness", now=self.now), [])
        arrival = datetime(2026, 10, 1, 14, tzinfo=KST)
        values = reusable_observations(self.place.pk, attribute="quietness", now=self.now,
                                      arrival=arrival, departure=arrival+timedelta(hours=1))
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0].context["scope"], "limited")
        # Returned data is a single observation, not a place-level positive verdict.
        self.assertEqual(values[0].basis, "customer_experience")

    def test_context_covers_complete_visit_and_handles_unknown(self):
        arrival = datetime(2026, 10, 1, 17, 30, tzinfo=KST)
        self.assertFalse(context_applies(LIMITED, arrival, arrival+timedelta(hours=1)))
        self.assertFalse(context_applies({**GENERAL, "scope": "unknown"}))
        self.assertFalse(context_applies(LIMITED, arrival, arrival))
        with self.assertRaises(ValidationError):
            context_applies(GENERAL, arrival, None)
        with self.assertRaises(ValidationError):
            context_applies(GENERAL, arrival.replace(tzinfo=None), arrival+timedelta(hours=1))

    def test_context_validation(self):
        for value in ({**GENERAL, "weekdays": [1]}, {**LIMITED, "weekdays": [True]},
                      {**LIMITED, "weekdays": [1, 1]}, {**LIMITED, "end_minute": 500},
                      {**GENERAL, "scope": "limited"}, {**GENERAL, "unexpected": "raw text"}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_context(value)

    def test_review_tags_wrong_branch_and_missing_date_rejected(self):
        payload = self.review()
        for change in ({"same_place_verified": False}, {"observed_on": None}, {"experience_key": ""}, {"observation_date_kind": "unknown"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                record_observation(**{**payload, **change})
        payload["source"] = self.source
        with self.assertRaises(ValidationError):
            record_observation(**payload)

    def test_sponsored_and_old_reviews_not_reused(self):
        payload = self.review()
        record_observation(**{**payload, "ingest_key": "ad", "context": GENERAL, "promotion": "disclosed"})
        record_observation(**{**payload, "ingest_key": "old", "context": GENERAL,
                              "observed_on": self.now.astimezone(KST).date()-timedelta(days=181)})
        self.assertEqual(reusable_observations(self.place.pk, attribute="quietness", now=self.now), [])

    def test_opposing_observations_are_preserved(self):
        record_observation(**self.payload())
        record_observation(**self.payload(ingest_key="opposing", polarity="negative", basis="explicit_non_sale"))
        values = reusable_observations(self.place.pk, attribute="menu", term="돈까스", now=self.now)
        self.assertEqual({v.polarity for v in values}, {"positive", "negative"})

    def test_freshness_and_timezone_validation(self):
        for change in ({"checked_at": self.now.replace(tzinfo=None)}, {"valid_until": self.now},
                       {"observed_on": self.now.astimezone(KST).date()+timedelta(days=1)}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                record_observation(**self.payload(**change))

    def test_no_raw_page_or_arbitrary_model_fields(self):
        with self.assertRaises(TypeError):
            record_observation(**self.payload(raw_html="<html>do something</html>"))
        names = {f.name for f in PlaceKnowledgeObservation._meta.fields}
        self.assertNotIn("embedding", names)
        self.assertNotIn("reviewer_name", names)

    def test_reference_url_not_a_fetch_permission(self):
        for url in ("file:///secret", "https://localhost/a", "https://127.0.0.1/a",
                    "https://[::1]/a", "https://name:secret@example.com/a"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                validate_reference_url(url)
        validate_reference_url("https://example.com/menu")

    def test_no_evidence_attempt_stores_unknown_cost_and_no_false_fact(self):
        attempt = PlaceEnrichmentAttempt.objects.create(
            attempt_key="fixture/attempt/1", place=self.place, attribute="menu", term="돈까스",
            status="no_evidence", reason_code="menu_not_found", search_calls=2, model_calls=1,
            search_credits=2, model_cost_usd=None, started_at=self.now-timedelta(seconds=5),
            finished_at=self.now, next_retry_at=self.now+timedelta(days=1))
        self.assertIsNone(attempt.model_cost_usd)
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 0)
        attempt.model_cost_usd = Decimal("-0.01")
        with self.assertRaises(ValidationError):
            attempt.save()
        attempt.model_cost_usd = Decimal("0")
        attempt.reason_code = "upstream error with secret token"
        with self.assertRaises(ValidationError):
            attempt.save()

    def test_database_checks_still_cover_coordinate_pair(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PlaceKnowledge.objects.filter(pk=self.place.pk).update(lat=37.5, lng=None)

    def test_one_review_keeps_all_features_not_only_requested_keyword(self):
        payload = self.feature()
        # Fictional, pre-verified extraction fixture, NOT a real review/model run.
        features = [
            ("atmosphere", "조용함", "negative", "소음이 커 대화하기 어려웠다"),
            ("cleanliness", "매장 청결", "positive", "테이블을 깨끗하게 관리했다"),
            ("space", "넓은 좌석", "positive", "좌석이 넓었다"),
            ("service", "직원 친절", "positive", "직원이 친절하게 응대했다"),
            ("value", "가성비", "negative", "가격에 비해 양이 적었다"),
            ("food", "담백한 맛", "positive", "국물 맛이 담백했다"),
            ("facilities", "콘센트", "positive", "좌석에서 콘센트를 이용했다"),
            ("suitability", "혼밥 적합", "positive", "혼자 식사하기 편했다"),
        ]
        observations = [self.batch_item(payload, ingest_key=f"feature/{i}", review_category=category,
                                       term=term, polarity=polarity, summary=summary)
                        for i, (category, term, polarity, summary) in enumerate(features)]
        saved = record_review_observations(place=self.place, source=payload["source"], observations=observations)
        self.assertEqual(len(saved), 8)
        values = reusable_observations(self.place.pk, attribute="review_feature", term=None, now=self.now)
        self.assertEqual({r.term for r in values}, {v[1] for v in features})
        self.assertEqual(len({r.experience_key for r in values}), 1)  # Not eight independent reviews.
        repeated = record_review_observations(place=self.place, source=payload["source"], observations=observations)
        self.assertTrue(all(not created for _, created in repeated))
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 8)
        selected = reusable_observations(self.place.pk, attribute="review_feature", term="혼밥 적합", now=self.now)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].polarity, "positive")

    def test_new_grounded_keyword_needs_no_enum_change(self):
        row, _ = record_observation(**self.feature(review_category="other", term="응원 유니폼 보관 편리",
                                                 summary="응원 유니폼을 넣을 공간이 편했다는 경험"))
        self.assertEqual(reusable_observations(self.place.pk, attribute="review_feature",
                                              term=row.term, now=self.now)[0].pk, row.pk)

    def test_feature_category_keyword_and_source_are_required(self):
        payload = self.feature()
        for change in ({"term": " "}, {"review_category": ""}, {"review_category": "invalid"},
                       {"source": self.source}, {"body_read": False}, {"observed_on": None}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                record_observation(**{**payload, **change})
        with self.assertRaises(ValidationError):
            record_observation(**self.payload(review_category="service"))

    def test_feature_approval_not_inherited_from_two_old_aspects(self):
        payload = self.feature()
        source = payload["source"]
        source.allowed_attributes = ["quietness", "cleanliness"]
        source.save()
        with self.assertRaises(ValidationError):
            record_observation(**payload)

    def test_review_batch_is_atomic_and_cannot_mix_places_or_experiences(self):
        payload = self.feature()
        first = self.batch_item(payload, ingest_key="first")
        for second in (
            self.batch_item(payload, ingest_key="second", review_category=""),
            self.batch_item(payload, ingest_key="second", experience_key="other-experience"),
            self.batch_item(payload, ingest_key="second", source_id=self.source.pk),
            self.batch_item(payload, ingest_key="second", attribute="menu"),
        ):
            with self.subTest(second=second), self.assertRaises(ValidationError):
                record_review_observations(place=self.place, source=payload["source"], observations=[first, second])
            self.assertEqual(PlaceKnowledgeObservation.objects.count(), 0)

    def test_feature_conditions_do_not_become_global_traits(self):
        row, _ = record_observation(**self.feature(qualifiers=["좌석:테라스", "동반:반려견"],
                                                 term="반려견 동반 편리", review_category="suitability"))
        args = {"attribute": "review_feature", "term": None, "now": self.now}
        self.assertEqual(reusable_observations(self.place.pk, **args), [])
        self.assertEqual(reusable_observations(self.place.pk, **args, review_conditions=["좌석:테라스"]), [])
        self.assertEqual([v.pk for v in reusable_observations(
            self.place.pk, **args, review_conditions=["좌석:테라스", "동반:반려견"])], [row.pk])

    def test_new_review_features_keep_time_and_negative_evidence(self):
        payload = self.feature(term="조용함", review_category="atmosphere", context=LIMITED)
        record_observation(**payload)
        record_observation(**{**payload, "ingest_key": "negative", "polarity": "negative",
                              "summary": "같은 시간대에 시끄러웠다는 반대 경험", "experience_key": "second"})
        args = {"attribute": "review_feature", "term": "조용함", "now": self.now}
        self.assertEqual(reusable_observations(self.place.pk, **args), [])
        arrival = datetime(2026, 10, 1, 14, tzinfo=KST)
        rows = reusable_observations(self.place.pk, **args, arrival=arrival, departure=arrival+timedelta(hours=1))
        self.assertEqual({v.polarity for v in rows}, {"positive", "negative"})

    def test_new_features_preserve_ad_freshness_pending_and_expiry_guards(self):
        payload = self.feature()
        for i, change in enumerate((
            {"promotion": "disclosed"}, {"promotion": "unknown"},
            {"observed_on": self.now.astimezone(KST).date()-timedelta(days=181)},
            {"review_status": "pending"}, {"polarity": "unknown", "review_status": "pending"},
            {"valid_until": self.now+timedelta(seconds=1)},
        )):
            record_observation(**{**payload, "ingest_key": f"not-reusable/{i}", **change})
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 6)
        self.assertEqual(reusable_observations(self.place.pk, attribute="review_feature", term=None,
                                              now=self.now+timedelta(seconds=1)), [])

    def test_review_feature_retrieval_and_batch_inputs_are_strict(self):
        for change in ({"term": ""}, {"term": 1}, {"term": None, "review_conditions": "테라스"},
                       {"term": None, "review_conditions": ["  "]}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                reusable_observations(self.place.pk, attribute="review_feature", **change)
        with self.assertRaises(ValidationError):
            reusable_observations(self.place.pk, attribute="menu", term=None)
        for observations in ({}, ["raw review"], [{"attribute": "review_feature", "experience_key": []}]):
            with self.subTest(observations=observations), self.assertRaises(ValidationError):
                record_review_observations(place=self.place, source=self.source, observations=observations)

    def test_feature_category_is_versioned(self):
        row, _ = record_observation(**self.feature())
        row.review_category = "other"
        with self.assertRaises(ValidationError):
            row.save()


class KnowledgeMigrationTests(SimpleTestCase):
    def test_new_migration_applies_and_reverses_on_isolated_sqlite(self):
        # Only the four new tables are created, on a separate in-memory connection.
        # The project's historical PostgreSQL migrations/data loaders do not run.
        alias = "knowledge_schema_probe"
        config = dict(connections["default"].settings_dict)
        config.update(NAME=":memory:", ENGINE="django.db.backends.sqlite3")
        probe_db = DatabaseWrapper(config, alias=alias)
        connections[alias] = probe_db
        try:
            migration = import_module("travel.migrations.0014_place_knowledge_storage").Migration(
                "0014_place_knowledge_storage", "travel")
            self.assertTrue(all(type(op).__name__ == "CreateModel" for op in migration.operations))
            with probe_db.schema_editor() as editor:
                original_state = migration.apply(ProjectState(), editor)
            self.assertEqual(set(probe_db.introspection.table_names()), {
                "travel_placeknowledge", "travel_placeknowledgesource",
                "travel_placeknowledgeobservation", "travel_placeenrichmentattempt"})
            feature_migration = import_module("travel.migrations.0015_review_knowledge_features").Migration(
                "0015_review_knowledge_features", "travel")
            with probe_db.schema_editor() as editor:
                feature_migration.apply(original_state, editor)
            with probe_db.cursor() as cursor:
                columns = probe_db.introspection.get_table_description(cursor, "travel_placeknowledgeobservation")
            self.assertIn("review_category", {v.name for v in columns})
            with probe_db.schema_editor() as editor:
                feature_migration.unapply(original_state, editor)
            with probe_db.cursor() as cursor:
                columns = probe_db.introspection.get_table_description(cursor, "travel_placeknowledgeobservation")
            self.assertNotIn("review_category", {v.name for v in columns})
            with probe_db.schema_editor() as editor:
                migration.unapply(ProjectState(), editor)
            self.assertEqual(probe_db.introspection.table_names(), [])
        finally:
            probe_db.close()
            del connections[alias]
