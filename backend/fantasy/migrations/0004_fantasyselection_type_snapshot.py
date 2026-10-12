from django.db import migrations, models


def populate_selection_type(apps, schema_editor):
    FantasySelection = apps.get_model("fantasy", "FantasySelection")
    alias = schema_editor.connection.alias
    selections = FantasySelection.objects.using(alias).select_related("player").iterator()
    for selection in selections:
        positions = {str(value).strip().lower() for value in (selection.player.positions or [])}
        selection.fantasy_type = "PITCHER" if positions & {"pitcher", "투수"} else "BATTER"
        selection.save(update_fields=("fantasy_type",), using=alias)


class Migration(migrations.Migration):
    dependencies = [("fantasy", "0003_replace_innings_with_batters_faced")]

    operations = [
        migrations.AddField(
            model_name="fantasyselection",
            name="fantasy_type",
            field=models.CharField(
                choices=[("BATTER", "Batter"), ("PITCHER", "Pitcher")],
                default="BATTER",
                max_length=8,
            ),
        ),
        migrations.RunPython(populate_selection_type, migrations.RunPython.noop),
    ]
