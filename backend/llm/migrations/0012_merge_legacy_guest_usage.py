from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("llm", "0011_answerfeedback"),
        ("llm", "0006_guestchatusage"),
    ]

    # Join the legacy quota and v2 histories without deleting existing records.
    operations = []
