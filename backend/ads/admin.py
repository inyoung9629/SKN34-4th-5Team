from django.contrib import admin
from django.db.models import Count, Q
from .models import AdCreative, AdEvent


@admin.register(AdCreative)
class AdCreativeAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "placement", "active", "is_test", "starts_at", "ends_at", "impression_count", "click_count")
    list_filter = ("placement", "active", "is_test")
    search_fields = ("title", "advertiser")
    filter_horizontal = ("courses",)
    list_select_related = ("place",)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(counted_impressions=Count("events", filter=Q(events__kind="impression")), counted_clicks=Count("events", filter=Q(events__kind="click")))

    @admin.display(description="노출")
    def impression_count(self, obj):
        return obj.counted_impressions

    @admin.display(description="클릭")
    def click_count(self, obj):
        return obj.counted_clicks


@admin.register(AdEvent)
class AdEventAdmin(admin.ModelAdmin):
    list_display = ("event_id", "ad", "kind", "placement", "received_at")
    list_filter = ("kind", "placement")
    readonly_fields = ("event_id", "exposure_id", "ad", "kind", "placement", "received_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
