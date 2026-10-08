from django.urls import include, path


urlpatterns = [
    path("", include("travel.urls")),
    path("community/", include("community.urls")),
    path("ads/", include("ads.urls")),
    path("auth/", include("accounts.urls")),
    path("baseball/", include("baseball.urls")),
    path("tving/", include("tving.urls")),
]
