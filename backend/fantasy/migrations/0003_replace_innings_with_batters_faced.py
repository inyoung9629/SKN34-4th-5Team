from django.db import migrations, models


def migrate_pitcher_weights_to_batters_faced(apps, schema_editor):
    FantasySelection = apps.get_model("fantasy", "FantasySelection")
    database = schema_editor.connection.alias
    for selection in FantasySelection.objects.using(database).iterator():
        weights = selection.stat_weights
        if isinstance(weights, dict) and "innings" in weights:
            weights["batters_faced"] = weights.pop("innings")
            FantasySelection.objects.using(database).filter(pk=selection.pk).update(
                stat_weights=weights
            )


def migrate_pitcher_weights_to_innings(apps, schema_editor):
    FantasySelection = apps.get_model("fantasy", "FantasySelection")
    database = schema_editor.connection.alias
    for selection in FantasySelection.objects.using(database).iterator():
        weights = selection.stat_weights
        if isinstance(weights, dict) and "batters_faced" in weights:
            weights["innings"] = weights.pop("batters_faced")
            FantasySelection.objects.using(database).filter(pk=selection.pk).update(
                stat_weights=weights
            )


class Migration(migrations.Migration):
    dependencies = [("fantasy", "0002_fantasyweek_stats_finalized_at")]

    operations = [
        migrations.AddField(
            model_name="fantasypitchinggamestat",
            name="batters_faced",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(
            migrate_pitcher_weights_to_batters_faced,
            migrate_pitcher_weights_to_innings,
        ),
        migrations.RemoveField(
            model_name="fantasypitchinggamestat",
            name="innings_outs",
        ),
    ]
