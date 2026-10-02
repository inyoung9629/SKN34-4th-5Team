"""travel domain tools."""
import math
from uuid import UUID
from langchain_core.tools import ToolException
from pydantic import Field, StrictFloat, StrictInt, model_validator
from django.db.models import Q
from travel.models import Course

from .common import LimitInput, ToolInput, _result, _tool

KAKAO_CATEGORIES = ("FD6", "CE7", "AT4", "CT1", "CS2", "AD5")

class PlacesInput(ToolInput):
    method: str = Field(pattern="^(keyword|category)$")
    query: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = None
    latitude: StrictFloat = Field(ge=-90, le=90)
    longitude: StrictFloat = Field(ge=-180, le=180)
    radius: StrictInt | None = Field(default=None, ge=1, le=20_000)
    page: StrictInt = Field(default=1, ge=1, le=3)
    limit: StrictInt = Field(default=15, ge=1, le=15)
    sort: str = Field(default="distance", pattern="^(accuracy|distance)$")
    stadium_code: str | None = Field(default=None, pattern="^(JAMSIL|GOCHEOK|MUNHAK|SUWON|DAEJEON|DAEGU|GWANGJU|SAJIK|CHANGWON)$")

    @model_validator(mode="after")
    def validate_search(self):
        if not math.isfinite(self.latitude) or not math.isfinite(self.longitude):
            raise ValueError("좌표는 유한한 숫자여야 합니다.")
        if self.category is not None and self.category not in KAKAO_CATEGORIES:
            raise ValueError("허용되지 않은 카테고리입니다.")
        if self.method == "keyword" and not self.query:
            raise ValueError("키워드 검색에는 query가 필요합니다.")
        if self.method == "category" and self.category is None:
            raise ValueError("카테고리 검색에는 category가 필요합니다.")
        return self

class CourseSearchInput(LimitInput):
    query: str | None = Field(default=None, min_length=1, max_length=100)
    stadium: str | None = Field(default=None, min_length=1, max_length=120)
    tag: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def any_filter(self):
        if not any((self.query, self.stadium, self.tag)):
            raise ValueError("검색 조건이 하나 이상 필요합니다.")
        return self

class CourseInput(ToolInput):
    course_id: UUID

class DirectionsInput(ToolInput):
    mode: str = Field(pattern="^(car|walk|transit)$")
    points: list[dict[str, StrictFloat]] = Field(min_length=2, max_length=13)

    @model_validator(mode="after")
    def validate_points(self):
        for point in self.points:
            if set(point) != {"lat", "lng"} or not math.isfinite(point["lat"]) or not math.isfinite(point["lng"]):
                raise ValueError("각 지점에는 유한한 lat, lng만 있어야 합니다.")
            if abs(point["lat"]) > 90 or abs(point["lng"]) > 180:
                raise ValueError("좌표 범위를 확인하세요.")
        return self

class TourismInput(ToolInput):
    stadium_code: str = Field(pattern="^(JAMSIL|GOCHEOK|MUNHAK|SUWON|DAEJEON|DAEGU|GWANGJU|SAJIK|CHANGWON)$")
    latitude: StrictFloat = Field(ge=-90, le=90)
    longitude: StrictFloat = Field(ge=-180, le=180)

def _course_item(course, include_stops=False):
    item = {
        "id": str(course.pk), "route_number": course.route_number, "title": course.title,
        "stadium": course.stadium, "description": course.description, "content": course.content,
        "content_format": course.content_format, "duration": course.duration, "cover": course.cover,
        "tags": course.tags, "start_lat": course.start_lat, "start_lng": course.start_lng,
        "author": course.author, "likes": course.likes, "views": course.views,
        "is_sample": course.is_sample, "created_at": course.created_at.isoformat(),
        "updated_at": course.updated_at.isoformat(),
    }
    if include_stops:
        item["stops"] = list(course.stops.order_by("position").values(
            "position", "name", "lat", "lng", "category", "place_id", "address",
            "tour_content_id", "is_map_point", "is_drawn_point",
        ))
    return item

def create_travel_tools():

    def search_places(method, latitude, longitude, query=None, category=None, radius=None, page=1, limit=15, sort="distance", stadium_code=None):
        """수집본에서만 검색한다. 카카오 등 외부 장소 API로 대체하지 않는다."""
        try:
            from travel.collected_places import CatalogueQueryError, CatalogueUnavailable, search_collected_places
        except ModuleNotFoundError as error:
            if error.name != "travel.collected_places":
                raise
            raise ToolException("장소 검색 서비스 통합이 필요합니다.") from None
        payload = {
            "method": method, "lat": latitude, "lng": longitude,
            "page": page, "size": limit, "sort": sort,
        }
        if query is not None:
            payload["keyword"] = query
        if category is not None:
            payload["category"] = category
        if radius is not None:
            payload["radius"] = radius
        if stadium_code is not None:
            payload["stadium"] = stadium_code
        try:
            return search_collected_places(payload)
        except CatalogueQueryError as error:
            raise ToolException(str(error)) from None
        except CatalogueUnavailable as error:
            raise ToolException(error.message) from None

    def search_courses(query=None, stadium=None, tag=None, limit=20):
        """공개 코스를 제목·설명·구장·태그로 검색한다."""
        courses = Course.objects.all()
        if query:
            courses = courses.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(content__icontains=query))
        if stadium:
            courses = courses.filter(stadium__icontains=stadium)
        if tag:
            courses = courses.filter(tags__contains=[tag])
        items = [_course_item(course) for course in courses.order_by("-created_at", "id")[:limit]]
        return _result(items)

    def get_course(course_id):
        """UUID로 공개 코스와 방문 장소를 조회한다."""
        course = Course.objects.prefetch_related("stops").filter(pk=course_id).first()
        return {"item": _course_item(course, True) if course else None}

    def get_directions(mode, points):
        """기존 공개 길찾기 서비스로 선택 지점 사이 경로를 조회한다."""
        from travel.directions_provider import DirectionsError, fetch_directions
        try:
            return fetch_directions(mode, points)
        except DirectionsError as error:
            message = "길찾기 요청이 많아요. 잠시 후 다시 시도해 주세요." if error.status == 429 else "길찾기 정보를 불러오지 못했습니다."
            raise ToolException(message) from None

    def search_tourism(stadium_code, latitude, longitude):
        """저장된 공원·산책 후보만 조회한다. 관광공사 실시간 검색은 없다."""
        from travel.collected_places import CatalogueQueryError, CatalogueUnavailable, search_collected_tourism
        try:
            return search_collected_tourism({"stadium": stadium_code, "lat": latitude, "lng": longitude})
        except CatalogueQueryError:
            raise ToolException("관광지 검색 위치를 확인해 주세요.") from None
        except CatalogueUnavailable as error:
            raise ToolException(error.message) from None

    specs = (
        (search_places, 'search_places', '저장된 수집본에서만 주변 장소를 검색한다. stadium_code를 함께 지정한다. category는 FD6 음식, CE7 카페·디저트, CS2 편의점, CT1 놀이시설, AT4 산책이다. category만으로도 검색 가능하며 query는 이름·주소·업종에 실제 포함된 단어를 짧게 사용한다. 숙박(AD5)은 ID만 있어 상세 미연결이다. 현재 영업·메뉴는 미확인이다.', PlacesInput),
        (search_courses, 'search_courses', '공개 코스를 검색한다.', CourseSearchInput),
        (get_course, 'get_course', 'UUID로 공개 코스 상세를 조회한다.', CourseInput),
        (get_directions, 'get_directions', '기존 길찾기 서비스로 2~13개 지점의 경로를 조회한다.', DirectionsInput),
        (search_tourism, 'search_tourism', '저장된 공원·산책 후보를 조회한다. 실제 산책 경로·입구·접근성은 미확인이다. 외부 관광 API는 호출하지 않는다.', TourismInput),
    )
    return tuple(_tool(*spec) for spec in specs)
