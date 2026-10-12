from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [("baseball", "0001_initial"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="FantasyWeek",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week_start", models.DateField()), ("week_end", models.DateField()), ("settlement_date", models.DateField()),
                ("status", models.CharField(choices=[("OPEN", "Open"), ("SETTLED", "Settled")], default="OPEN", max_length=8)),
                ("settled_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ], options={"ordering": ("-week_start",)},
        ),
        migrations.CreateModel(
            name="FantasySettlement",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("settled_at", models.DateTimeField()), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("week", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="settlement", to="fantasy.fantasyweek"))],
        ),
        migrations.CreateModel(
            name="FantasySelection",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("stat_weights", models.JSONField(default=dict)), ("is_confirmed", models.BooleanField(default=False)), ("confirmed_at", models.DateTimeField(blank=True, null=True)), ("selected_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("player", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="fantasy_selections", to="baseball.player")), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fantasy_selections", to=settings.AUTH_USER_MODEL)), ("week", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="selections", to="fantasy.fantasyweek"))],
        ),
        migrations.CreateModel(
            name="FantasyBattingGameStat",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("at_bats", models.PositiveIntegerField(default=0)), ("hits", models.PositiveIntegerField(default=0)), ("rbi", models.PositiveIntegerField(default=0)), ("runs", models.PositiveIntegerField(default=0)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("game", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="baseball.game")), ("player", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="baseball.player")), ("team", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="baseball.team"))],
        ),
        migrations.CreateModel(
            name="FantasyPitchingGameStat",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("saves", models.PositiveIntegerField(default=0)), ("innings_outs", models.PositiveIntegerField(default=0)), ("strikeouts", models.PositiveIntegerField(default=0)), ("pitch_count", models.PositiveIntegerField(default=0)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("game", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="baseball.game")), ("player", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="baseball.player")), ("team", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="baseball.team"))],
        ),
        migrations.CreateModel(
            name="FantasyUserResult",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("final_score", models.DecimalField(decimal_places=2, default=0, max_digits=14)), ("point_amount", models.PositiveIntegerField(default=0)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("settlement", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="results", to="fantasy.fantasysettlement")), ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="fantasy_results", to=settings.AUTH_USER_MODEL))],
        ),
        migrations.CreateModel(
            name="FantasyPlayerScore",
            fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("score", models.DecimalField(decimal_places=2, default=0, max_digits=14)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)), ("player", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="baseball.player")), ("user_result", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="player_scores", to="fantasy.fantasyuserresult"))],
        ),
        migrations.AddConstraint(model_name="fantasyweek", constraint=models.UniqueConstraint(fields=("week_start", "week_end"), name="uq_fantasy_week")),
        migrations.AddConstraint(model_name="fantasyselection", constraint=models.UniqueConstraint(fields=("week", "user", "player"), name="uq_fantasy_selection")),
        migrations.AddConstraint(model_name="fantasybattinggamestat", constraint=models.UniqueConstraint(fields=("game", "player"), name="uq_fantasy_batting_stat")),
        migrations.AddConstraint(model_name="fantasypitchinggamestat", constraint=models.UniqueConstraint(fields=("game", "player"), name="uq_fantasy_pitching_stat")),
        migrations.AddConstraint(model_name="fantasyuserresult", constraint=models.UniqueConstraint(fields=("settlement", "user"), name="uq_fantasy_result")),
        migrations.AddConstraint(model_name="fantasyplayerscore", constraint=models.UniqueConstraint(fields=("user_result", "player"), name="uq_fantasy_player_score")),
    ]
