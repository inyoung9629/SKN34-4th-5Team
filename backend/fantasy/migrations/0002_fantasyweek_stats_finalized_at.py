from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("fantasy", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="fantasyweek",
            name="stats_finalized_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
