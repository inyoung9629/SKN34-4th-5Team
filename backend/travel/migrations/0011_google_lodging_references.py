from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("travel", "0010_merge_content_doc_tourismplace")]
    operations = [
        migrations.AlterField(model_name="coursestop", name="lat", field=models.FloatField(blank=True, null=True)),
        migrations.AlterField(model_name="coursestop", name="lng", field=models.FloatField(blank=True, null=True)),
        migrations.AddConstraint(
            model_name="coursestop",
            constraint=models.CheckConstraint(
                condition=(models.Q(place_id__startswith="google-ui-kit:") & models.Q(lat__isnull=True) & models.Q(lng__isnull=True))
                | ((models.Q(place_id__isnull=True) | ~models.Q(place_id__startswith="google-ui-kit:")) & models.Q(lat__isnull=False) & models.Q(lng__isnull=False)),
                name="course_stop_reference_coordinates",
            ),
        ),
    ]
