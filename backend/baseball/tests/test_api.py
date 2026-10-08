import hashlib
import json
from datetime import UTC, datetime
from threading import Barrier, Thread

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from drf_spectacular.generators import SchemaGenerator
from rest_framework_simplejwt.tokens import RefreshToken

from baseball.models import FoodStore, FoodStoreLocation, FoodStoreMenu, Game, HomeContext, SeatZone, Stadium, Team, TicketPolicy, TicketPrice, Transport
from baseball.serializers import RESOURCE_DETAIL_SERIALIZERS, RESOURCE_FIELDS, RESOURCE_MODELS, RESOURCE_SERIALIZERS


class BaseballApiTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="staff", password="pass", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_all_19_resources_are_explicit_and_listable(self):
        self.assertEqual(19, len(RESOURCE_MODELS))
        self.assertEqual(set(RESOURCE_MODELS), set(RESOURCE_FIELDS) | set(RESOURCE_SERIALIZERS))
        for resource in RESOURCE_MODELS:
            self.assertEqual(200, self.client.get(f"/api/v1/baseball/manage/{resource}/").status_code, resource)

    def test_openapi_has_one_exact_admin_schema_per_resource(self):
        schemas = SchemaGenerator().get_schema(request=None, public=True)["components"]["schemas"]
        self.assertEqual(set(RESOURCE_MODELS), set(RESOURCE_FIELDS) | set(RESOURCE_SERIALIZERS) | set(RESOURCE_DETAIL_SERIALIZERS))
        for resource, model in RESOURCE_MODELS.items():
            name = model.__name__
            self.assertEqual(set(RESOURCE_FIELDS[resource]), set(schemas[name]["properties"]), name)
            self.assertEqual(
                {*RESOURCE_FIELDS[resource], "_etag"},
                set(schemas[f"{name}Detail"]["properties"]),
                name,
            )

    def test_staff_crud_etag_and_protect(self):
        team = {"id": 10, "team_code": "TS", "team_name_ko": "테스트"}
        self.assertEqual(201, self.client.post("/api/v1/baseball/manage/teams/", team, format="json").status_code)
        detail = self.client.get("/api/v1/baseball/manage/teams/10/")
        etag = detail["ETag"]
        patched = self.client.patch("/api/v1/baseball/manage/teams/10/", {"team_name_ko": "변경"}, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(200, patched.status_code)
        self.assertEqual(412, self.client.patch("/api/v1/baseball/manage/teams/10/", {"team_name_ko": "충돌"}, format="json", HTTP_IF_MATCH=etag).status_code)
        stadium = Stadium.objects.create(id=10, stadium_code="TEST", stadium_name_ko="테스트구장", address="서울", longitude=127, latitude=37, geocode_source="TEST", collected_at=datetime.now(UTC))
        HomeContext.objects.create(id=10, season=2026, team=Team.objects.get(pk=10), stadium=stadium)
        current = self.client.get("/api/v1/baseball/manage/teams/10/")["ETag"]
        self.assertEqual(409, self.client.delete("/api/v1/baseball/manage/teams/10/", HTTP_IF_MATCH=current).status_code)

    def test_etag_matches_the_returned_record_snapshot(self):
        Team.objects.create(id=1, team_code="ONE", team_name_ko="하나")
        response = self.client.get("/api/v1/baseball/manage/teams/1/")
        snapshot = {key: value for key, value in response.data.items() if key != "_etag"}
        expected = '"' + hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest() + '"'
        self.assertEqual(expected, response["ETag"])
        self.assertEqual(expected, response.data["_etag"])

    def test_anonymous_and_non_staff_cannot_manage_but_can_read(self):
        self.client.force_authenticate(None)
        self.assertEqual(401, self.client.get("/api/v1/baseball/manage/teams/").status_code)
        self.assertEqual(200, self.client.get("/api/v1/baseball/teams/").status_code)
        user = get_user_model().objects.create_user(username="member", password="pass")
        self.client.force_authenticate(user)
        self.assertEqual(403, self.client.post("/api/v1/baseball/manage/teams/", {"id": 1}, format="json").status_code)

    def test_active_staff_bearer_jwt_reaches_manage_api(self):
        user = get_user_model().objects.create_user(username="jwt-staff", password="pass", is_staff=True)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
        self.assertEqual(200, client.get("/api/v1/baseball/manage/teams/").status_code)
        client.credentials(HTTP_AUTHORIZATION="Bearer invalid")
        self.assertEqual(401, client.get("/api/v1/baseball/manage/teams/").status_code)

    def test_validation(self):
        payload = {"id": 1, "stadium_code": "BAD", "stadium_name_ko": "오류", "address": "주소", "longitude": "181", "latitude": "91", "geocode_source": "TEST", "collected_at": "2026-09-08T00:00:00Z"}
        response = self.client.post("/api/v1/baseball/manage/stadiums/", payload, format="json")
        self.assertEqual(400, response.status_code)
        self.assertIn("longitude", response.data["field_errors"])

    def test_patch_compares_changed_team_with_unchanged_team(self):
        first = Team.objects.create(id=1, team_code="ONE", team_name_ko="하나")
        second = Team.objects.create(id=2, team_code="TWO", team_name_ko="둘")
        game = Game.objects.create(id=1, game_code="G1", home_team=first, away_team=second, game_date="2026-09-14", game_time="18:30", status_code="PREV", game_type="REGULAR", collected_at=datetime.now(UTC))
        etag = self.client.get(f"/api/v1/baseball/manage/games/{game.pk}/")["ETag"]
        response = self.client.patch(f"/api/v1/baseball/manage/games/{game.pk}/", {"home_team_id": second.pk}, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(400, response.status_code)
        game.refresh_from_db()
        self.assertEqual(first, game.home_team)

    def test_inactive_staff_is_denied(self):
        user = get_user_model().objects.create_user(username="inactive", password="pass", is_staff=True, is_active=False)
        self.client.force_authenticate(user)
        self.assertEqual(403, self.client.get("/api/v1/baseball/manage/teams/").status_code)

    def test_invalid_public_filters_return_400(self):
        for path in (
            "/api/v1/baseball/games/?team=abc", "/api/v1/baseball/games/?date_from=bad",
            "/api/v1/baseball/games/?team=999999999999999999999999999999",
            "/api/v1/baseball/games/?date_from=20260914",
            "/api/v1/baseball/games/?date_from=2026-09-15&date_to=2026-09-14",
            "/api/v1/baseball/standings/?snapshot_date=bad",
            "/api/v1/baseball/stadiums/X/seat-zones/?season=x",
            "/api/v1/baseball/ticket-prices/?seat_zone=0",
        ):
            self.assertEqual(400, self.client.get(path).status_code, path)

    def test_urls_require_an_http_host(self):
        Team.objects.create(id=1, team_code="HOME", team_name_ko="홈팀")
        stadium = Stadium.objects.create(id=1, stadium_code="HOME", stadium_name_ko="홈구장", address="서울", longitude=127, latitude=37, geocode_source="TEST", collected_at=datetime.now(UTC))
        context = HomeContext.objects.create(id=1, season=2026, team_id=1, stadium=stadium)
        response = self.client.post("/api/v1/baseball/manage/seat-maps/", {"id": 1, "home_context_id": context.pk, "map_title": "좌석도", "page_url": "https:relative"}, format="json")
        self.assertEqual(400, response.status_code)
        self.assertIn("page_url", response.data["field_errors"])

    def test_id_unique_and_foreign_key_validation(self):
        Team.objects.create(id=1, team_code="ONE", team_name_ko="하나")
        detail = self.client.get("/api/v1/baseball/manage/teams/1/")
        changed_id = self.client.patch("/api/v1/baseball/manage/teams/1/", {"id": 2}, format="json", HTTP_IF_MATCH=detail["ETag"])
        duplicate = self.client.post("/api/v1/baseball/manage/teams/", {"id": 2, "team_code": "ONE", "team_name_ko": "중복"}, format="json")
        missing_fk = self.client.post("/api/v1/baseball/manage/home-contexts/", {"id": 1, "season": 2026, "team_id": 999, "stadium_id": 999}, format="json")
        self.assertEqual((400, 400, 400), (changed_id.status_code, duplicate.status_code, missing_fk.status_code))
        self.assertEqual(1, Team.objects.get(pk=1).pk)

    def test_public_context_filters_pagination_and_food_children(self):
        first, second = Team.objects.bulk_create([
            Team(id=1, team_code="ONE", team_name_ko="하나"),
            Team(id=2, team_code="TWO", team_name_ko="둘"),
        ])
        stadium = Stadium.objects.create(id=1, stadium_code="SHARED", stadium_name_ko="공유구장", address="서울", longitude=127, latitude=37, geocode_source="TEST", collected_at=datetime.now(UTC))
        old = HomeContext.objects.create(id=1, season=2025, team=first, stadium=stadium)
        current = HomeContext.objects.create(id=2, season=2026, team=second, stadium=stadium)
        SeatZone.objects.create(id=1, home_context=old, zone_code="OLD", zone_name_ko="이전", level="1", side="1루", seat_type="일반")
        current_zone = SeatZone.objects.create(id=2, home_context=current, zone_code="NEW", zone_name_ko="현재", level="1", side="3루", seat_type="일반")
        for query, expected in (("home_context=1", [1]), ("team=2", [2]), ("season=2026", [2])):
            response = self.client.get(f"/api/v1/baseball/stadiums/SHARED/seat-zones/?{query}")
            self.assertEqual(expected, [item["id"] for item in response.data["results"]])
        Transport.objects.bulk_create([Transport(id=index, stadium=stadium, access_code=f"A{index}", mode="BUS", title=str(index), details="", collected_at=datetime.now(UTC)) for index in range(1, 102)])
        page = self.client.get("/api/v1/baseball/stadiums/SHARED/transports/?page_size=100&page=2")
        self.assertEqual((101, [101]), (page.data["count"], [item["id"] for item in page.data["results"]]))
        store = FoodStore.objects.create(id=1, record_code="STORE", stadium=stadium, store_facility="매점", collected_at=datetime.now(UTC))
        FoodStoreLocation.objects.create(id=1, food_store=store, location_no=1, floor="1", zone_location="1루")
        FoodStoreMenu.objects.create(id=1, food_store=store, menu_category_official="간식")
        item = self.client.get("/api/v1/baseball/stadiums/SHARED/food-stores/").data["results"][0]
        self.assertEqual(([1], [1]), ([row["id"] for row in item["locations"]], [row["id"] for row in item["menus"]]))
        TicketPrice.objects.create(id=1, seat_zone=current_zone, price_tier="일반", day_type="BLUE", customer_type="성인", price_krw=22000, collected_at=datetime.now(UTC))
        price = self.client.get("/api/v1/baseball/stadiums/SHARED/ticket-prices/?home_context=2").data["results"][0]
        self.assertEqual(("NEW", "현재"), (price["seat_zone_code"], price["seat_zone_name"]))
        TicketPolicy.objects.create(id=1, policy_code="POLICY", team=second, policy_type="일반", subtype="예매", channel_no=1, booking_channel="공식", channel_condition="원문 조건", collected_at=datetime.now(UTC))
        self.assertEqual([1], [row["id"] for row in self.client.get("/api/v1/baseball/ticket-policies/?team=2").data["results"]])

    def test_management_pagination_reaches_rows_beyond_first_100(self):
        existing = Team.objects.count()
        Team.objects.bulk_create([Team(id=index, team_code=f"T{index}", team_name_ko=f"구단 {index}") for index in range(1, 102)])
        first = self.client.get("/api/v1/baseball/manage/teams/?page_size=100")
        second = self.client.get("/api/v1/baseball/manage/teams/?page_size=100&page=2")
        self.assertEqual((existing + 101, 100, existing + 1), (first.data["count"], len(first.data["results"]), len(second.data["results"])))

    def test_every_resource_supports_create_read_patch_delete(self):
        Team.objects.create(id=102, team_code="AWAY", team_name_ko="원정팀")
        now = "2026-09-14T00:00:00Z"
        payloads = [
            ("teams", {"id": 101, "team_code": "HOME", "team_name_ko": "홈팀"}, "team_name_ko", "홈팀 수정"),
            ("stadiums", {"id": 201, "stadium_code": "ROUND", "stadium_name_ko": "왕복구장", "address": "서울", "longitude": "127", "latitude": "37", "geocode_source": "TEST", "collected_at": now}, "address", "서울 수정"),
            ("home-contexts", {"id": 301, "season": 2026, "team_id": 101, "stadium_id": 201}, "season", 2027),
            ("postseason-stages", {"id": 401, "stage_code": "ROUND", "stage_name": "라운드", "start_date": "2026-10-01", "end_date": "2026-10-02", "matchup_description": "대진", "status_tag": "OPEN", "collected_at": now}, "stage_name", "수정 라운드"),
            ("games", {"id": 501, "game_code": "GAME", "home_team_id": 101, "away_team_id": 102, "stadium_id": 201, "postseason_stage_id": 401, "game_date": "2026-10-01", "game_time": "18:30", "home_score": 1, "away_score": 0, "status_code": "END", "game_type": "POSTSEASON", "collected_at": now}, "status_code", "FINAL"),
            ("standing-histories", {"id": 601, "team_id": 101, "snapshot_date": "2026-09-14", "rank": 1, "wins": 1, "losses": 0, "draws": 0, "games_behind": "0", "collected_at": now}, "wins", 2),
            ("seat-zones", {"id": 701, "home_context_id": 301, "zone_code": "Z", "zone_name_ko": "좌석", "level": "1F", "side": "1루", "seat_type": "일반", "group_size": 1, "accessible": True}, "zone_name_ko", "수정 좌석"),
            ("ticket-prices", {"id": 801, "seat_zone_id": 701, "price_tier": "STANDARD", "day_type": "WEEKDAY", "customer_type": "GENERAL", "group_size": 1, "price_krw": 10000, "valid_from": "2026-01-01", "valid_to": "2026-12-31", "discount_condition": "없음", "collected_at": now}, "price_krw", 11000),
            ("ticket-policies", {"id": 901, "policy_code": "POLICY", "team_id": 101, "game_id": 501, "policy_type": "일반", "subtype": "예매", "open_at": now, "max_tickets": 4, "channel_no": 1, "booking_channel": "공식", "channel_condition": "조건", "collected_at": now}, "max_tickets", 5),
            ("seat-maps", {"id": 1001, "home_context_id": 301, "map_title": "좌석도", "page_url": "https://example.com/map"}, "map_title", "수정 좌석도"),
            ("seat-map-assets", {"id": 1101, "seat_map_id": 1001, "asset_no": 1, "asset_url": "https://example.com/map.png", "asset_role": "PRIMARY"}, "asset_role", "SECONDARY"),
            ("seat-scopes", {"id": 1201, "home_context_id": 301, "scope_code": "ALL", "scope_name": "전체"}, "scope_name", "전체 좌석"),
            ("seat-views", {"id": 1301, "seat_scope_id": 1201, "view_characteristic": "좋음", "roof_coverage": "UNKNOWN", "evidence_scope": "GENERAL"}, "view_characteristic", "보통"),
            ("food-stores", {"id": 1401, "record_code": "FOOD", "stadium_id": 201, "store_facility": "매점", "location_qty": 1, "collected_at": now}, "store_facility", "수정 매점"),
            ("food-store-locations", {"id": 1501, "food_store_id": 1401, "location_no": 1, "floor": "1F", "zone_location": "1루"}, "floor", "2F"),
            ("food-store-menus", {"id": 1601, "food_store_id": 1401, "menu_category_official": "간식"}, "menu_category_official", "식사"),
            ("transports", {"id": 1701, "stadium_id": 201, "access_code": "SUBWAY", "mode": "SUBWAY", "title": "역", "details": "1번 출구", "parking_spaces": 1, "reservation_required": False, "collected_at": now}, "title", "수정 역"),
            ("stadium-contents", {"id": 1801, "record_code": "CONTENT", "stadium_id": 201, "content_type": "TOUR", "name": "투어", "floor": "1F", "location": "중앙", "official_description": "설명", "operating_condition": "경기일", "collected_at": now}, "name", "수정 투어"),
            ("facilities", {"id": 1901, "record_code": "FACILITY", "stadium_id": 201, "facility_type": "화장실", "floor": "1F", "side": "1루", "nearby_section": "101", "gate": "1", "gender": "공용", "indoor_outdoor": "실내", "location_detail": "상세", "collected_at": now}, "location_detail", "수정 상세"),
        ]
        for resource, payload, field, changed in payloads:
            created = self.client.post(f"/api/v1/baseball/manage/{resource}/", payload, format="json")
            self.assertEqual(201, created.status_code, (resource, created.data))
            detail = self.client.get(f"/api/v1/baseball/manage/{resource}/{payload['id']}/")
            self.assertEqual(200, detail.status_code, resource)
            updated = self.client.patch(f"/api/v1/baseball/manage/{resource}/{payload['id']}/", {field: changed}, format="json", HTTP_IF_MATCH=detail["ETag"])
            self.assertEqual(200, updated.status_code, (resource, updated.data))
            self.assertEqual(changed, updated.data[field], resource)
        for resource, payload, _field, _changed in reversed(payloads):
            detail = self.client.get(f"/api/v1/baseball/manage/{resource}/{payload['id']}/")
            deleted = self.client.delete(f"/api/v1/baseball/manage/{resource}/{payload['id']}/", HTTP_IF_MATCH=detail["ETag"])
            self.assertEqual(204, deleted.status_code, (resource, deleted.data))


class PostgreSqlConcurrencyTests(TransactionTestCase):
    databases = {"default"}

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL only")
        self.user = get_user_model().objects.create_user(username="staff", password="pass", is_staff=True)
        Team.objects.create(id=1, team_code="ONE", team_name_ko="하나")

    def test_one_of_two_concurrent_writes_is_rejected(self):
        client = APIClient()
        client.force_authenticate(self.user)
        etag = client.get("/api/v1/baseball/manage/teams/1/")["ETag"]
        barrier, statuses, errors = Barrier(2), [], []

        def patch(name):
            close_old_connections()
            try:
                worker = APIClient()
                worker.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
                barrier.wait()
                statuses.append(worker.patch("/api/v1/baseball/manage/teams/1/", {"team_name_ko": name}, format="json", HTTP_IF_MATCH=etag).status_code)
            except Exception as error:
                errors.append(error)
            finally:
                close_old_connections()

        threads = [Thread(target=patch, args=(name,)) for name in ("첫째", "둘째")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)
        self.assertEqual([200, 412], sorted(statuses))

    def test_nullable_foreign_keys_do_not_break_row_lock(self):
        game = Game.objects.create(id=1, game_code="NULLS", game_date="2026-09-14", game_time="18:30", status_code="PREV", game_type="REGULAR", collected_at=datetime.now(UTC))
        client = APIClient()
        client.force_authenticate(self.user)
        detail = client.get(f"/api/v1/baseball/manage/games/{game.pk}/")
        response = client.patch(f"/api/v1/baseball/manage/games/{game.pk}/", {"status_code": "END"}, format="json", HTTP_IF_MATCH=detail["ETag"])
        self.assertEqual(200, response.status_code)
