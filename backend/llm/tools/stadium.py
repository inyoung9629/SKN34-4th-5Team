"""stadium domain tools."""
from datetime import date
from pydantic import Field, StrictBool, StrictInt, model_validator
from django.db.models import Q
from baseball.models import Facility, FoodStore, HomeContext, SeatMap, SeatScope, SeatZone, Stadium, StadiumContent, TicketPolicy, TicketPrice, Transport

from baseball.stadium_guides import GUIDE_PREFETCH, parking_map, seating_map

from .common import LimitInput, ToolInput, _json, _result, _rows, _tool, db_team_code, is_team_code

IMAGE_FIELDS = ("image_url", "image_source_url", "image_credit", "image_credit_url", "image_license_url")


def _stadium_image(item):
    values = {name: item.pop(name) for name in IMAGE_FIELDS}
    return {"imageUrl": values["image_url"], "sourceUrl": values["image_source_url"],
            "credit": values["image_credit"], "creditUrl": values["image_credit_url"],
            "licenseUrl": values["image_license_url"], "collected_at": None} if values["image_url"] else None

class StadiumInput(ToolInput):
    stadium_id: StrictInt | None = Field(default=None, ge=1)
    stadium_code: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.stadium_id is None) == (self.stadium_code is None):
            raise ValueError("stadium_id와 stadium_code 중 하나만 입력하세요.")
        return self

class ContextInput(LimitInput):
    season: StrictInt = Field(ge=1982, le=2100)
    team_code: str = Field(pattern="^[A-Z]{2,7}$")
    stadium_id: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_team(self):
        if not is_team_code(self.team_code):
            raise ValueError("올바른 팀 코드가 아닙니다.")
        return self

class TicketPricesInput(ContextInput):
    as_of: date | None = None
    zone_code: str | None = Field(default=None, min_length=1, max_length=80)

class TicketPoliciesInput(LimitInput):
    team_code: str = Field(pattern="^[A-Z]{2,7}$")
    game_id: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_team(self):
        if not is_team_code(self.team_code):
            raise ValueError("올바른 팀 코드가 아닙니다.")
        return self

class StadiumListInput(LimitInput):
    stadium_id: StrictInt = Field(ge=1)

class FacilityInput(StadiumListInput):
    facility_type: str | None = Field(default=None, min_length=1, max_length=80)
    include_locations: StrictBool = Field(default=False, description="별도 catalogue의 근사 좌표·불확실성·검증 출처 포함. ORM 시설과 ID/분류를 합치지 않는다.")

class ContentInput(StadiumListInput):
    content_type: str | None = Field(default=None, min_length=1, max_length=80)

def _context(season, team_code, stadium_id=None):
    query = HomeContext.objects.filter(season=season, team__team_code=db_team_code(team_code))
    return query.filter(stadium_id=stadium_id) if stadium_id is not None else query

def create_stadium_tools():

    def get_stadiums(limit=20):
        """공개 구장 목록을 코드순으로 조회한다."""
        items = _rows(Stadium.objects.order_by("stadium_code", "id"), ("id", "stadium_code", "stadium_name_ko", "address", "longitude", "latitude", "geocode_source", "collected_at", *IMAGE_FIELDS), limit)
        for item in items:
            item["detailPath"] = f"/stadiums/{item['stadium_code']}"
            item["image"] = _stadium_image(item)
        return _result(items)

    def get_stadium(stadium_id=None, stadium_code=None):
        """ID 또는 코드로 공개 구장 정보를 조회한다."""
        query = Stadium.objects.filter(id=stadium_id) if stadium_id is not None else Stadium.objects.filter(stadium_code=stadium_code)
        item = _rows(query, ("id", "stadium_code", "stadium_name_ko", "address", "longitude", "latitude", "facility_manager", "game_operator", "phone_general", "phone_facility", "phone_ticket", "geocode_source", "collected_at", *IMAGE_FIELDS), 1)
        if item:
            item[0]["detailPath"] = f"/stadiums/{item[0]['stadium_code']}"
            item[0]["image"] = _stadium_image(item[0])
            stadium = query.prefetch_related(*GUIDE_PREFETCH).get()
            item[0]["parkingMap"] = parking_map(stadium)
            item[0]["seatingMap"] = seating_map(stadium)
        return {"item": item[0] if item else None}

    def get_seat_zones(season, team_code, stadium_id=None, limit=20):
        """팀·시즌 홈 컨텍스트의 좌석 구역을 조회한다."""
        query = SeatZone.objects.filter(home_context__in=_context(season, team_code, stadium_id)).order_by("home_context__stadium_id", "zone_code")
        return _result(_rows(query, ("id", "home_context_id", "home_context__stadium_id", "zone_code", "zone_name_ko", "level", "side", "seat_type", "group_size", "accessible"), limit))

    def get_seat_views(season, team_code, stadium_id=None, limit=20):
        """팀·시즌 홈 컨텍스트의 좌석 시야 특성을 조회한다."""
        query = SeatScope.objects.filter(home_context__in=_context(season, team_code, stadium_id)).order_by("home_context__stadium_id", "scope_code", "seat_views__id")
        return _result(_rows(query, ("id", "home_context_id", "home_context__stadium_id", "scope_code", "scope_name", "seat_views__view_characteristic", "seat_views__roof_coverage", "seat_views__evidence_scope"), limit))

    def get_ticket_prices(season, team_code, stadium_id=None, limit=20, as_of=None, zone_code=None):
        """팀·시즌 좌석 가격을 선택한 유효일 기준으로 조회한다."""
        query = TicketPrice.objects.filter(seat_zone__home_context__in=_context(season, team_code, stadium_id))
        if as_of:
            query = query.filter(Q(valid_from__isnull=True) | Q(valid_from__lte=as_of), Q(valid_to__isnull=True) | Q(valid_to__gte=as_of))
        if zone_code:
            query = query.filter(seat_zone__zone_code=zone_code)
        rows = _rows(query.order_by("seat_zone__zone_code", "price_krw", "id"), ("id", "seat_zone__zone_code", "seat_zone__zone_name_ko", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition"), limit)
        return _result(rows, as_of=_json(as_of))

    def get_ticket_policies(team_code, game_id=None, limit=20):
        """팀과 선택한 경기의 공개 예매 정책을 조회한다."""
        query = TicketPolicy.objects.filter(team__team_code=db_team_code(team_code))
        if game_id is not None:
            query = query.filter(Q(game_id=game_id) | Q(game_id__isnull=True))
        return _result(_rows(query.order_by("policy_code", "channel_no", "id"), ("policy_code", "team__team_code", "game_id", "policy_type", "subtype", "open_at", "max_tickets", "channel_no", "booking_channel", "channel_condition"), limit))

    def get_transport(stadium_id, limit=20):
        """구장의 교통·주차 정보를 조회한다."""
        return _result(_rows(Transport.objects.filter(stadium_id=stadium_id).order_by("mode", "access_code"), ("access_code", "mode", "title", "details", "parking_spaces", "reservation_required", "collected_at"), limit), parkingMap=parking_map(Stadium.objects.filter(pk=stadium_id).first()))

    def get_food_stores(stadium_id, limit=20):
        """구장 공식 매점과 위치·메뉴 분류를 조회한다."""
        items = []
        for store in FoodStore.objects.filter(stadium_id=stadium_id).select_related("stadium").prefetch_related("locations", "menus").order_by("record_code")[:limit]:
            items.append({"record_code": store.record_code, "store_facility": store.store_facility, "location_qty": store.location_qty, "collected_at": _json(store.collected_at), "detailPath": f"/stadiums/{store.stadium.stadium_code}", "locations": list(store.locations.order_by("location_no").values("location_no", "floor", "zone_location")), "menus": list(store.menus.order_by("menu_category_official").values_list("menu_category_official", flat=True))})
        return _result(items)

    def get_facilities(stadium_id, limit=20, facility_type=None, include_locations=False):
        """구장의 편의시설을 조회한다."""
        query = Facility.objects.filter(stadium_id=stadium_id)
        if facility_type:
            query = query.filter(facility_type=facility_type)
        items = _rows(query.order_by("facility_type", "record_code"), ("record_code", "facility_type", "floor", "side", "nearby_section", "gate", "gender", "indoor_outdoor", "location_detail", "collected_at"), limit)
        stadium = Stadium.objects.filter(pk=stadium_id).first()
        path = f"/stadiums/{stadium.stadium_code}" if stadium else None
        result = _result(items, detailPath=path)
        if include_locations:
            from travel.stadium_facilities import facility_catalogue
            from travel.collected_places import CatalogueQueryError, CatalogueUnavailable
            try:
                catalogue = facility_catalogue(stadium.stadium_code) if stadium else None
                if catalogue:
                    records = [row for row in catalogue["records"] if row["kind"] == "facility"]
                    result["catalogue"] = {**catalogue, "records": records[:limit], "count": min(len(records), limit),
                                           "total_count": len(records), "has_more": len(records) > limit,
                                           "pinCount": sum(len(row["pins"]) for row in records[:limit]),
                                           "filter_scope": "facility_type applies only to ORM items; catalogue IDs are separate"}
                else:
                    result["catalogue"] = None
            except (CatalogueQueryError, CatalogueUnavailable):
                result.update(catalogue=None, catalogue_warning="구장 위치 catalogue가 미확보되어 ORM 시설만 반환합니다.")
        return result

    def get_stadium_contents(stadium_id, limit=20, content_type=None):
        """구장의 공개 부가 콘텐츠를 조회한다."""
        query = StadiumContent.objects.filter(stadium_id=stadium_id)
        if content_type:
            query = query.filter(content_type=content_type)
        return _result(_rows(query.order_by("content_type", "record_code"), ("record_code", "content_type", "name", "floor", "location", "official_description", "operating_condition", "collected_at"), limit))

    def get_seat_maps(season, team_code, stadium_id=None, limit=20):
        """팀·시즌 홈 컨텍스트의 공식 좌석도와 자산을 조회한다."""
        items = []
        for seat_map in SeatMap.objects.filter(home_context__in=_context(season, team_code, stadium_id)).select_related("home_context__stadium").prefetch_related("assets").order_by("home_context__stadium_id", "id")[:limit]:
            items.append({"home_context_id": seat_map.home_context_id, "stadium_id": seat_map.home_context.stadium_id, "map_title": seat_map.map_title, "page_url": seat_map.page_url, "sourceUrl": seat_map.page_url, "collected_at": None, "detailPath": f"/stadiums/{seat_map.home_context.stadium.stadium_code}", "assets": list(seat_map.assets.order_by("asset_no").values("asset_no", "asset_url", "asset_role", "source_url"))})
        return _result(items)

    specs = (
        (get_stadiums, 'get_stadiums', '공개 구장 목록(ID·코드·이름·주소)을 조회한다. items에 detailPath와 외관 사진 image(imageUrl·sourceUrl·credit·creditUrl·licenseUrl)가 있으면 함께 반환한다. 한 구장 소개는 get_stadium으로 상세를 확인한다.', LimitInput),
        (get_stadium, 'get_stadium', '구장 ID 또는 코드로 주소·운영·시설 관리·연락처 등 공개 상세를 조회한다. item의 detailPath와 중첩된 image.imageUrl은 소개용 Markdown 링크·사진으로 쓴다. 사진을 보여줄 때 제공된 image.credit·creditUrl·sourceUrl·licenseUrl로 저작자·출처·라이선스를 함께 안내하고 없는 값은 만들지 않는다. 외관 사진은 좌석도·주차 지도가 아니다.', StadiumInput),
        (get_seat_zones, 'get_seat_zones', '팀·시즌·선택 구장의 좌석 구역을 조회한다.', ContextInput),
        (get_seat_views, 'get_seat_views', '팀·시즌·선택 구장의 좌석 시야를 조회한다.', ContextInput),
        (get_ticket_prices, 'get_ticket_prices', '팀·시즌 좌석 가격을 선택 유효일 기준으로 조회한다.', TicketPricesInput),
        (get_ticket_policies, 'get_ticket_policies', '팀과 선택 경기의 예매 정책을 조회한다.', TicketPoliciesInput),
        (get_transport, 'get_transport', '구장의 교통·주차 items와 선택적 parkingMap 안내 이미지를 조회한다. parkingMap.imageUrl·sourceUrl·credit은 제공된 값만 안내하며 실시간 주차 현황이나 지도 좌표가 아니다.', StadiumListInput),
        (get_food_stores, 'get_food_stores', '구장 공식 매점, 위치와 메뉴를 조회한다.', StadiumListInput),
        (get_facilities, 'get_facilities', '구장 편의시설을 조회한다.', FacilityInput),
        (get_stadium_contents, 'get_stadium_contents', '구장 부가 콘텐츠를 조회한다.', ContentInput),
        (get_seat_maps, 'get_seat_maps', '팀·시즌·선택 구장의 좌석도를 조회한다.', ContextInput),
    )
    return tuple(_tool(*spec) for spec in specs)
