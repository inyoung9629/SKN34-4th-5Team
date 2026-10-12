from django.db import migrations


def repair_selection_type(apps, schema_editor):
    Selection = apps.get_model("fantasy", "FantasySelection")
    alias = schema_editor.connection.alias
    batter_keys = {"at_bats", "hits", "rbi", "runs"}
    pitcher_keys = {"saves", "batters_faced", "strikeouts", "pitch_count"}
    for selection in Selection.objects.using(alias).iterator():
        keys = set(selection.stat_weights or {})
        if batter_keys <= keys and not keys & pitcher_keys:
            fantasy_type = "BATTER"
        elif pitcher_keys <= keys and not keys & batter_keys:
            fantasy_type = "PITCHER"
        else:
            continue
        if selection.fantasy_type != fantasy_type:
            Selection.objects.using(alias).filter(pk=selection.pk).update(fantasy_type=fantasy_type)


class Migration(migrations.Migration):
    dependencies = [("fantasy", "0004_fantasyselection_type_snapshot")]
    operations = [migrations.RunPython(repair_selection_type, migrations.RunPython.noop)]
