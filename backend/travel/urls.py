from django.urls import path

from .views import CourseDetailView, CourseListCreateView, CourseReactionView, CourseViewView, DirectionsView
from .weather_views import StadiumWeatherView
from .place_views import LodgingSearchView, PlaceDetailView, PlaceListCreateView, PlaceSearchView, PlaceLiveSearchView
from .tourism_views import TourismSearchView
from .collected_place_views import CollectedPlaceListView
from .stadium_facility_views import StadiumFacilityListView

urlpatterns = [
    path("weather/", StadiumWeatherView.as_view(), name="stadium-weather"),
    path("travel/directions/", DirectionsView.as_view(), name="travel-directions"),
    path("places/search/", PlaceSearchView.as_view(), name="place-search"),
    path("places/live/", PlaceLiveSearchView.as_view(), name="place-live-search"),
    path("places/lodging/", LodgingSearchView.as_view(), name="lodging-search"),
    path("places/collected/", CollectedPlaceListView.as_view(), name="collected-places"),
    path("places/stadium-facilities/", StadiumFacilityListView.as_view(), name="stadium-facilities"),
    path("places/", PlaceListCreateView.as_view(), name="place-list"),
    path("places/<int:pk>/", PlaceDetailView.as_view(), name="place-detail"),
    path("tourism/", TourismSearchView.as_view(), name="tourism-search"),
    path("courses/", CourseListCreateView.as_view(), name="course-list"),
    path("courses/<uuid:pk>/", CourseDetailView.as_view(), name="course-detail"),
    path("courses/<uuid:pk>/reaction/", CourseReactionView.as_view(), name="course-reaction"),
    path("courses/<uuid:pk>/view/", CourseViewView.as_view(), name="course-view"),
]
