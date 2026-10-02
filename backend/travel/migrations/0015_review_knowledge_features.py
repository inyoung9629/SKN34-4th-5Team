"""Open-ended review keywords; preserve legacy observations without a data rewrite."""
from django.db import migrations, models


ATTRIBUTE_CHOICES = [(v, v) for v in (
    "cuisine", "menu", "quietness", "cleanliness", "review_feature",
)]


class Migration(migrations.Migration):
    dependencies = [("travel", "0014_place_knowledge_storage")]

    operations = [
        migrations.AddField(
            model_name="placeknowledgeobservation", name="review_category",
            field=models.CharField(blank=True, max_length=16, choices=[(v, v) for v in (
                "atmosphere", "cleanliness", "space", "service", "value", "food",
                "facilities", "access", "suitability", "other",
            )]),
        ),
        migrations.AlterField(
            model_name="placeknowledgeobservation", name="attribute",
            field=models.CharField(choices=ATTRIBUTE_CHOICES, max_length=16),
        ),
        migrations.AlterField(
            model_name="placeenrichmentattempt", name="attribute",
            field=models.CharField(choices=ATTRIBUTE_CHOICES, max_length=16),
        ),
    ]
