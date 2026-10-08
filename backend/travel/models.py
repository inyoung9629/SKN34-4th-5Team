import uuid

from django.conf import settings
from django.db import connection
from django.db import models
from django.db.models import Q


def next_route_number():
    with connection.cursor() as cursor:
        cursor.execute("SELECT nextval('travel_route_number_seq')")
        return f"{cursor.fetchone()[0]:06d}"


class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_id = models.CharField(max_length=80, null=True, blank=True, unique=True)
    route_number = models.CharField(max_length=6, unique=True, default=next_route_number, editable=False)
    title = models.CharField(max_length=80)
    stadium = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    content = models.TextField(blank=True)
    content_doc = models.JSONField(null=True, blank=True)
    content_format = models.CharField(max_length=16, blank=True)
    duration = models.CharField(max_length=80)
    cover = models.CharField(max_length=255, blank=True)
    tags = models.JSONField(default=list)
    start_lat = models.FloatField(null=True, blank=True)
    start_lng = models.FloatField(null=True, blank=True)
    travel_mode = models.CharField(max_length=10, choices=(("walk", "Walk"), ("car", "Car"), ("transit", "Transit")), default="walk")
    leg_modes = models.JSONField(default=dict, blank=True)
    author = models.CharField(max_length=80, default="익명")
    likes = models.PositiveIntegerField(default=0)
    views = models.PositiveIntegerField(default=0)
    is_sample = models.BooleanField(default=False)
    edit_token_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = (
            models.CheckConstraint(
                condition=(Q(start_lat__isnull=True) & Q(start_lng__isnull=True)) | (Q(start_lat__isnull=False) & Q(start_lng__isnull=False)),
                name="course_start_coordinates_paired",
            ),
            models.CheckConstraint(condition=Q(start_lat__isnull=True) | Q(start_lat__range=(-90, 90)), name="course_start_lat_bounds"),
            models.CheckConstraint(condition=Q(start_lng__isnull=True) | Q(start_lng__range=(-180, 180)), name="course_start_lng_bounds"),
        )


class CourseStop(models.Model):
    course = models.ForeignKey(Course, related_name="stops", on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=255)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    category = models.CharField(max_length=120)
    place_id = models.CharField(max_length=255, null=True, blank=True)
    visit_id = models.CharField(max_length=255, null=True, blank=True)
    address = models.CharField(max_length=500, null=True, blank=True)
    tour_content_id = models.CharField(max_length=255, null=True, blank=True)
    is_map_point = models.BooleanField(null=True, blank=True)
    is_drawn_point = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ("position",)
        constraints = (
            models.UniqueConstraint(fields=("course", "position"), name="unique_course_stop_position"),
            models.CheckConstraint(
                condition=(Q(place_id__isnull=False) & (Q(place_id__startswith="google-ui-kit:") | Q(place_id__startswith="kakao-lodging:")) & Q(lat__isnull=True) & Q(lng__isnull=True))
                | ((Q(place_id__isnull=True) | (~Q(place_id__startswith="google-ui-kit:") & ~Q(place_id__startswith="kakao-lodging:"))) & Q(lat__isnull=False) & Q(lng__isnull=False)),
                name="course_stop_reference_coordinates",
            ),
            models.CheckConstraint(condition=Q(lat__range=(-90, 90)), name="course_stop_lat_bounds"),
            models.CheckConstraint(condition=Q(lng__range=(-180, 180)), name="course_stop_lng_bounds"),
        )


class Place(models.Model):
    kakao_place_id = models.CharField(max_length=100, null=True, blank=True, unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500, blank=True)
    road_address = models.CharField(max_length=500, blank=True)
    category_group_code = models.CharField(max_length=20, blank=True)
    category_group_name = models.CharField(max_length=100, blank=True)
    category_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=120, blank=True)
    lat = models.FloatField()
    lng = models.FloatField()
    url = models.URLField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("id",)
        constraints = (
            models.CheckConstraint(condition=Q(lat__range=(-90, 90)), name="place_lat_bounds"),
            models.CheckConstraint(condition=Q(lng__range=(-180, 180)), name="place_lng_bounds"),
        )


class CourseReaction(models.Model):
    course = models.ForeignKey(Course, related_name="reactions", on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="course_reactions", on_delete=models.CASCADE)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("course", "user"), name="course_reaction_user_unique"),
        )


class CourseView(models.Model):
    course = models.ForeignKey(Course, related_name="viewer_records", on_delete=models.CASCADE)
    actor_digest = models.CharField(max_length=64)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("course", "actor_digest"), name="course_view_actor_unique"),
        )


class ExternalProviderSnapshot(models.Model):
    class Kind(models.TextChoices):
        DIRECTIONS = "directions"
        TOURISM = "tourism"

    kind = models.CharField(max_length=16, choices=Kind)
    key = models.CharField(max_length=255)
    request = models.JSONField(default=dict)
    payload = models.JSONField(default=dict)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("kind", "key")
        constraints = (
            models.UniqueConstraint(fields=("kind", "key"), name="travel_external_snapshot_unique"),
        )


from .directions_models import DirectionsRoute  # noqa: E402,F401
from .tourism_models import TourismPlace  # noqa: E402,F401
from .place_knowledge_models import (  # noqa: E402,F401
    PlaceKnowledge, PlaceKnowledgeSource, PlaceKnowledgeObservation, PlaceEnrichmentAttempt,
)
