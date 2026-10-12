"""TVING /roaster API로 KBO 선수 명단을 수집해 기존 관계형 모델에 적재한다."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.db import transaction
from django.utils import timezone

from baseball.models import Player, Team, TeamRoster
from tving.parsers import POSITIONS, _roster
from tving.service import _provider_json


TEAM_NAMES = {
    "SS": "SAMSUNG", "KT": "KT", "LG": "LG", "HT": "KIA", "OB": "DOOSAN",
    "NC": "NC", "HH": "HANWHA", "LT": "LOTTE", "SK": "SSG", "WO": "KIWOOM",
}
TEAM_CODES = ("SS", "KT", "LG", "HT", "OB", "NC", "HH", "LT", "SK", "WO")


def persist_roster(team_code, position, athletes, now):
    """선수 기본 정보와 구단-포지션 관계를 중복 없이 갱신한다."""
    team = Team.objects.get(team_code=TEAM_NAMES[team_code])
    with transaction.atomic():
        for athlete in athletes:
            player, created = Player.objects.get_or_create(
                external_code=athlete["code"],
                defaults={"team": team, "name": athlete["name"], "image_url": athlete["imageUrl"], "positions": [position], "identity_source_fetched_at": now, "identity_last_synced_at": now},
            )
            if not created:
                player.team = team
                player.name = athlete["name"]
                if athlete["imageUrl"] is not None:
                    player.image_url = athlete["imageUrl"]
                player.identity_source_fetched_at = now
                player.identity_last_synced_at = now
                player.save(update_fields=("team", "name", "image_url", "identity_source_fetched_at", "identity_last_synced_at", "updated_at"))
            TeamRoster.objects.update_or_create(
                team=team, player=player,
                defaults={"position": position, "back_number": athlete["backNumber"], "source_fetched_at": now, "last_synced_at": now},
            )
            positions = list(TeamRoster.objects.filter(team=team, player=player).values_list("position", flat=True).distinct())
            player.positions = sorted(positions)
            player.save(update_fields=("positions", "updated_at"))


def main():
    now = timezone.now()
    total = 0
    for team_code in TEAM_CODES:
        for position in POSITIONS:
            payload = _provider_json("/roaster", {"sportsType": "kbo", "code": team_code, "position": position})
            athletes = _roster(payload)
            persist_roster(team_code, position, athletes, now)
            total += len(athletes)
            print(f"{team_code} {position}: {len(athletes)}")
    print(f"player roster synced: requests=40 players={total}")


if __name__ == "__main__":
    main()
