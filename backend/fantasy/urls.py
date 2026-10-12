from django.urls import path

from fantasy.admin_views import (
    FantasyAdminContextView,
    FantasyAdminGameListView,
    FantasyAdminStatImportView,
    FantasyAdminTestSettlementView,
)
from fantasy.views import CurrentScoreView, PlayerListView, PointWalletView, SelectionDetailView, SelectionListCreateView, WeekView

urlpatterns = [
    path("admin/context/", FantasyAdminContextView.as_view()),
    path("admin/games/", FantasyAdminGameListView.as_view()),
    path("admin/stats/", FantasyAdminStatImportView.as_view()),
    path("admin/test-settlement/", FantasyAdminTestSettlementView.as_view()),
    path("week/<str:which>/", WeekView.as_view()),
    path("players/", PlayerListView.as_view()),
    path("selections/", SelectionListCreateView.as_view()),
    path("selections/<int:pk>/", SelectionDetailView.as_view()),
    path("score/current/", CurrentScoreView.as_view()),
    path("points/", PointWalletView.as_view()),
]
