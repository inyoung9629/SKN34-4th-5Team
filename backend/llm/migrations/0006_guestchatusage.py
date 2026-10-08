from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("llm", "0005_chat_progress_event")]
    operations = [
        migrations.CreateModel(
            name="GuestChatUsage",
            fields=[
                ("identity", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("used", models.PositiveSmallIntegerField(default=0)),
            ],
        ),
    ]
