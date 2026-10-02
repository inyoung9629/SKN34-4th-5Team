from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("travel", "0011_google_lodging_references")]
    operations = [
        migrations.RemoveConstraint(model_name="coursestop", name="course_stop_reference_coordinates"),
        migrations.AddConstraint(
            model_name="coursestop",
            constraint=models.CheckConstraint(
                condition=(models.Q(place_id__isnull=False) & models.Q(place_id__startswith="google-ui-kit:"))
                & models.Q(lat__isnull=True) & models.Q(lng__isnull=True)
                | ((models.Q(place_id__isnull=True) | ~models.Q(place_id__startswith="google-ui-kit:"))
                   & models.Q(lat__isnull=False) & models.Q(lng__isnull=False)),
                name="course_stop_reference_coordinates",
            ),
        ),
    ]
