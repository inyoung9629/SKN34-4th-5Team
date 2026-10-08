from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("travel", "0015_review_knowledge_features")]

    operations = [
        migrations.AddField(
            model_name="course", name="travel_mode",
            field=models.CharField(choices=[("walk", "Walk"), ("car", "Car"), ("transit", "Transit")], default="walk", max_length=10),
        ),
        migrations.AddField(model_name="course", name="leg_modes", field=models.JSONField(blank=True, default=dict)),
    ]
