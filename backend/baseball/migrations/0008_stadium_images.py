import csv
from pathlib import Path

from django.db import migrations, models, transaction


IMAGE_FIELDS = ("image_url", "image_source_url", "image_credit", "image_credit_url", "image_license_url")


def seed_images(apps, schema_editor):
    relative = "preprocessed/stadium_coordinates.csv"
    roots = (Path("/data"), Path(__file__).resolve().parents[3] / "data")
    path = next((root / relative for root in roots if (root / relative).is_file()), None)
    if path is None:
        raise FileNotFoundError(f"Stadium image CSV not found in {roots}")
    alias = schema_editor.connection.alias
    Stadium = apps.get_model("baseball", "Stadium")
    with path.open(encoding="utf-8-sig", newline="") as stream, transaction.atomic(using=alias):
        for row in csv.DictReader(stream):
            Stadium.objects.using(alias).filter(stadium_code=row["stadium_code"]).update(
                **{name: row[name].strip() or None for name in IMAGE_FIELDS}
            )


class Migration(migrations.Migration):
    dependencies = [("baseball", "0007_reviewed_stadium_locations")]
    operations = [
        migrations.AddField(model_name="stadium", name="image_url", field=models.CharField(max_length=500, null=True, blank=True)),
        migrations.AddField(model_name="stadium", name="image_source_url", field=models.URLField(max_length=500, null=True, blank=True)),
        migrations.AddField(model_name="stadium", name="image_credit", field=models.CharField(max_length=300, null=True, blank=True)),
        migrations.AddField(model_name="stadium", name="image_credit_url", field=models.URLField(max_length=500, null=True, blank=True)),
        migrations.AddField(model_name="stadium", name="image_license_url", field=models.URLField(max_length=500, null=True, blank=True)),
        migrations.RunPython(seed_images, migrations.RunPython.noop),
    ]
