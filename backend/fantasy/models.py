from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from baseball.models import Game, Player, Team


class FantasyWeek(models.Model):
    OPEN = "OPEN"
    SETTLED = "SETTLED"
    STATUS_CHOICES = ((OPEN, "Open"), (SETTLED, "Settled"))

    week_start = models.DateField()
    week_end = models.DateField()
    settlement_date = models.DateField()
    stats_finalized_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=OPEN)
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("week_start", "week_end"), name="uq_fantasy_week")
        ]
        ordering = ("-week_start",)


class FantasySelection(models.Model):
    BATTER = "BATTER"
    PITCHER = "PITCHER"
    FANTASY_TYPE_CHOICES = ((BATTER, "Batter"), (PITCHER, "Pitcher"))

    week = models.ForeignKey(FantasyWeek, on_delete=models.CASCADE, related_name="selections")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="fantasy_selections")
    player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="fantasy_selections")
    fantasy_type = models.CharField(max_length=8, choices=FANTASY_TYPE_CHOICES, default=BATTER)
    stat_weights = models.JSONField(default=dict)
    is_confirmed = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    selected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("week", "user", "player"), name="uq_fantasy_selection")]


class _GameStatBase(models.Model):
    game = models.ForeignKey(Game, on_delete=models.PROTECT)
    player = models.ForeignKey(Player, on_delete=models.PROTECT)
    team = models.ForeignKey(Team, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class FantasyBattingGameStat(_GameStatBase):
    at_bats = models.PositiveIntegerField(default=0)
    hits = models.PositiveIntegerField(default=0)
    rbi = models.PositiveIntegerField(default=0)
    runs = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("game", "player"), name="uq_fantasy_batting_stat")]


class FantasyPitchingGameStat(_GameStatBase):
    saves = models.PositiveIntegerField(default=0)
    batters_faced = models.PositiveIntegerField(default=0)
    strikeouts = models.PositiveIntegerField(default=0)
    pitch_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("game", "player"), name="uq_fantasy_pitching_stat")]


class FantasySettlement(models.Model):
    week = models.OneToOneField(FantasyWeek, on_delete=models.PROTECT, related_name="settlement")
    settled_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FantasyUserResult(models.Model):
    settlement = models.ForeignKey(FantasySettlement, on_delete=models.CASCADE, related_name="results")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fantasy_results")
    final_score = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    point_amount = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("settlement", "user"), name="uq_fantasy_result")]


class FantasyPlayerScore(models.Model):
    user_result = models.ForeignKey(FantasyUserResult, on_delete=models.CASCADE, related_name="player_scores")
    player = models.ForeignKey(Player, on_delete=models.PROTECT)
    score = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user_result", "player"), name="uq_fantasy_player_score")]
