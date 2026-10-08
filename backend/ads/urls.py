from django.urls import path
from .views import AdAdminDetailView, AdAdminListCreateView, AdEventView, AdSlotView

urlpatterns = [
    path("slots/", AdSlotView.as_view(), name="ad-slot"),
    path("events/", AdEventView.as_view(), name="ad-event"),
    path("admin/creatives/", AdAdminListCreateView.as_view(), name="ad-admin-list"),
    path("admin/creatives/<int:pk>/", AdAdminDetailView.as_view(), name="ad-admin-detail"),
]
