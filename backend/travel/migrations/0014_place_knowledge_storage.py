"""Empty knowledge tables only; no source data copy, crawler or model invocation."""
import uuid

import django.db.models.deletion
import travel.place_knowledge_models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("travel", "0013_kakao_lodging_references")]

    operations = [
        migrations.CreateModel(
            name="PlaceKnowledge",
            fields=[
                ("place_id", models.CharField(max_length=255, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("branch_name", models.CharField(blank=True, max_length=120)),
                ("address", models.CharField(blank=True, max_length=500)),
                ("lat", models.FloatField(blank=True, null=True)),
                ("lng", models.FloatField(blank=True, null=True)),
                ("kind", models.CharField(choices=[("food", "food"), ("cafe", "cafe"), ("lodging", "lodging"), ("walk", "walk"), ("indoor", "indoor"), ("store", "store"), ("facility", "facility"), ("unknown", "unknown")], default="unknown", max_length=16)),
                ("base_source", models.CharField(max_length=120)),
                ("base_version", models.CharField(blank=True, max_length=120)),
                ("base_checked_at", models.DateTimeField()),
                ("stadium_scope", models.CharField(choices=[("unknown", "unknown"), ("external", "external"), ("stadium_internal", "stadium_internal"), ("stadium_external", "stadium_external")], default="unknown", max_length=24)),
                ("stadium_code", models.CharField(blank=True, max_length=30)),
                ("floor", models.CharField(blank=True, max_length=40)),
                ("zone", models.CharField(blank=True, max_length=100)),
                ("requires_ticket", models.BooleanField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "indexes": [models.Index(fields=["kind", "stadium_scope"], name="pk_kind_scope_idx")],
                "constraints": [
                    models.CheckConstraint(condition=(models.Q(lat__isnull=True) & models.Q(lng__isnull=True)) | (models.Q(lat__isnull=False) & models.Q(lng__isnull=False)), name="pk_coordinates_paired"),
                    models.CheckConstraint(condition=models.Q(lat__isnull=True) | models.Q(lat__range=(-90, 90)), name="pk_lat_bounds"),
                    models.CheckConstraint(condition=models.Q(lng__isnull=True) | models.Q(lng__range=(-180, 180)), name="pk_lng_bounds"),
                ],
            },
        ),
        migrations.CreateModel(
            name="PlaceKnowledgeSource",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_key", models.CharField(max_length=255, unique=True)),
                ("provider", models.CharField(max_length=80)),
                ("url", models.URLField(blank=True, max_length=2000, validators=[travel.place_knowledge_models.validate_reference_url])),
                ("kind", models.CharField(choices=[("public_dataset", "public_dataset"), ("official", "official"), ("menu_listing", "menu_listing"), ("customer_review", "customer_review"), ("platform_summary", "platform_summary"), ("other", "other")], max_length=24)),
                ("access_method", models.CharField(choices=[("api", "api"), ("web", "web"), ("manual", "manual")], max_length=10)),
                ("fetched_at", models.DateTimeField()),
                ("published_on", models.DateField(blank=True, null=True)),
                ("storage_policy", models.CharField(choices=[("unreviewed", "unreviewed"), ("allowed", "allowed"), ("temporary", "temporary"), ("blocked", "blocked")], default="unreviewed", max_length=12)),
                ("allowed_attributes", models.JSONField(blank=True, default=list)),
                ("policy_reference", models.CharField(blank=True, max_length=500)),
                ("policy_checked_at", models.DateTimeField(blank=True, null=True)),
                ("retention_until", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"constraints": [models.CheckConstraint(condition=~models.Q(storage_policy="temporary") | models.Q(retention_until__isnull=False), name="pks_temporary_expiry")]},
        ),
        migrations.CreateModel(
            name="PlaceEnrichmentAttempt",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("attempt_key", models.CharField(max_length=120, unique=True)),
                ("attribute", models.CharField(choices=[("cuisine", "cuisine"), ("menu", "menu"), ("quietness", "quietness"), ("cleanliness", "cleanliness")], max_length=16)),
                ("term", models.CharField(blank=True, max_length=100)),
                ("status", models.CharField(choices=[("completed", "completed"), ("no_evidence", "no_evidence"), ("blocked", "blocked"), ("api_error", "api_error")], max_length=16)),
                ("reason_code", models.CharField(max_length=64)),
                ("search_calls", models.PositiveIntegerField(default=0)),
                ("model_calls", models.PositiveIntegerField(default=0)),
                ("search_credits", models.PositiveIntegerField(blank=True, null=True)),
                ("model_cost_usd", models.DecimalField(blank=True, decimal_places=8, max_digits=12, null=True)),
                ("started_at", models.DateTimeField()),
                ("finished_at", models.DateTimeField()),
                ("next_retry_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("place", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="enrichment_attempts", to="travel.placeknowledge")),
            ],
            options={
                "indexes": [models.Index(fields=["place", "attribute", "term", "finished_at"], name="pka_lookup_idx")],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(finished_at__gte=models.F("started_at")), name="pka_time_order"),
                    models.CheckConstraint(condition=models.Q(model_cost_usd__isnull=True) | models.Q(model_cost_usd__gte=0), name="pka_cost_nonnegative"),
                    models.CheckConstraint(condition=models.Q(next_retry_at__isnull=True) | models.Q(next_retry_at__gte=models.F("finished_at")), name="pka_retry_order"),
                ],
            },
        ),
        migrations.CreateModel(
            name="PlaceKnowledgeObservation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("ingest_key", models.CharField(max_length=120, unique=True)),
                ("attribute", models.CharField(choices=[("cuisine", "cuisine"), ("menu", "menu"), ("quietness", "quietness"), ("cleanliness", "cleanliness")], max_length=16)),
                ("term", models.CharField(blank=True, max_length=100)),
                ("observed_label", models.CharField(blank=True, max_length=100)),
                ("qualifiers", models.JSONField(blank=True, default=list)),
                ("polarity", models.CharField(choices=[("positive", "positive"), ("negative", "negative"), ("neutral", "neutral"), ("unknown", "unknown")], default="unknown", max_length=10)),
                ("basis", models.CharField(choices=[("menu_listing", "menu_listing"), ("official_statement", "official_statement"), ("public_classification", "public_classification"), ("customer_experience", "customer_experience"), ("explicit_non_sale", "explicit_non_sale"), ("inference", "inference"), ("insufficient", "insufficient")], default="insufficient", max_length=24)),
                ("summary", models.CharField(blank=True, max_length=400)),
                ("review_status", models.CharField(choices=[("pending", "pending"), ("accepted", "accepted"), ("rejected", "rejected"), ("retracted", "retracted")], default="pending", max_length=12)),
                ("same_place_verified", models.BooleanField(default=False)),
                ("evidence_verified", models.BooleanField(default=False)),
                ("body_read", models.BooleanField(default=False)),
                ("experience_key", models.CharField(blank=True, max_length=120)),
                ("promotion", models.CharField(choices=[("disclosed", "disclosed"), ("not_disclosed", "not_disclosed"), ("unknown", "unknown")], default="unknown", max_length=16)),
                ("observed_on", models.DateField(blank=True, null=True)),
                ("observation_date_kind", models.CharField(choices=[("visited", "visited"), ("published", "published"), ("unknown", "unknown")], default="unknown", max_length=10)),
                ("context", models.JSONField(default=travel.place_knowledge_models.unknown_context, validators=[travel.place_knowledge_models.validate_context])),
                ("checked_at", models.DateTimeField()),
                ("valid_until", models.DateTimeField(blank=True, null=True)),
                ("extractor_version", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("place", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="observations", to="travel.placeknowledge")),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="observations", to="travel.placeknowledgesource")),
            ],
            options={
                "indexes": [models.Index(fields=["place", "attribute", "term", "review_status"], name="pko_lookup_idx")],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(valid_until__isnull=True) | models.Q(valid_until__gt=models.F("checked_at")), name="pko_validity_order"),
                    models.CheckConstraint(condition=~models.Q(review_status="accepted") | (models.Q(same_place_verified=True) & models.Q(evidence_verified=True) & models.Q(valid_until__isnull=False)), name="pko_accepted_evidence"),
                ],
            },
        ),
    ]
