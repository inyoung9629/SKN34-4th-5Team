import csv
import hashlib
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from .stadium_locations import reviewed_locations


def stable_id(model, natural_key):
    digest = hashlib.blake2s(f"{model._meta.label_lower}:{natural_key}".encode(), digest_size=4).digest()
    return int.from_bytes(digest, "big") % 2_000_000_000 + 1


def nullable(value):
    value = (value or "").strip()
    return value or None


def number(value, allow_text=False):
    value = nullable(value)
    if value is None:
        return None
    if re.fullmatch(r"\d+", value):
        return int(value)
    if re.fullmatch(r"\d+\.0", value):
        return int(float(value))
    if allow_text and (value == "무료 대상 증빙 필요" or re.fullmatch(r"\d+(?:·\d+)+인", value)):
        return None
    raise ValueError(f"정수가 아닌 값: {value}")


def boolean(value):
    value = (value or "").strip().upper()
    if not value:
        return None
    try:
        return {"Y": True, "N": False, "TRUE": True, "FALSE": False}[value]
    except KeyError:
        raise ValueError(f"불리언이 아닌 값: {value}") from None


def timestamp(value):
    parsed = datetime.fromisoformat(value.strip())
    return parsed.replace(tzinfo=ZoneInfo("Asia/Seoul")) if parsed.tzinfo is None else parsed


NATURAL_FIELDS = {
    "Team": ("team_code",), "Stadium": ("stadium_code",), "HomeContext": ("season", "team", "stadium"),
    "PostseasonStage": ("stage_code",), "Game": ("game_code",), "StandingHistory": ("team", "snapshot_date"),
    "SeatZone": ("home_context", "zone_code"), "TicketPolicy": ("policy_code", "channel_no"),
    "TicketPrice": ("seat_zone", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition"),
    "SeatMap": ("home_context", "map_title"), "SeatMapAsset": ("seat_map", "asset_no"),
    "SeatScope": ("home_context", "scope_code"), "SeatView": ("seat_scope",),
    "Transport": ("stadium", "access_code"), "FoodStore": ("record_code",),
    "FoodStoreLocation": ("food_store", "location_no"), "FoodStoreMenu": ("food_store", "menu_category_official"),
    "StadiumContent": ("record_code",), "Facility": ("record_code",),
}


class BaseballDataLoaderV1:
    def __init__(self, apps, root, alias="default"):
        self.apps = apps
        self.root = Path(root)
        self.alias = alias
        self.counts = Counter()
        self.sources = {}
        self.rejections = []
        self.provenance = []
        if not (self.root / "raw/team_stadium_code_map.csv").is_file():
            raise CommandError(f"데이터 디렉터리를 찾을 수 없습니다: {self.root}")

    def model(self, name):
        return self.apps.get_model("baseball", name)

    def load(self):
        with transaction.atomic(using=self.alias):
            self.import_all()
        return self.report()

    def report(self):
        imported = sum(value for key, value in self.counts.items() if key.endswith(".imported"))
        skipped = sum(value for key, value in self.counts.items() if key.endswith(".skipped"))
        return {
            "source_rows": self.sources,
            "totals": {"source": sum(self.sources.values()), "imported": imported, "skipped": skipped},
            "results": dict(sorted(self.counts.items())),
            "rejected": self.rejections,
            "provenance": self.provenance,
            "unsupported": [
                "비정수 group_size는 모델이 IntegerField라 null로 적재하고 원문은 provenance.raw_values에 기록",
                "kbo_standing.csv는 표현 필드가 부족한 최신 요약이므로 standing_history만 적재",
                "kbo_schedule_postseason_tbd.csv는 확정 경기 아닌 단계 일정으로만 적재",
                "external_places.csv는 현재 19개 모델에 대응하지 않아 미적재",
                "구장먹거리_위치_자리어때.csv와 구장편의시설_위치_자리어때.csv는 비공식 자료라 미적재",
                "3차_신규확보데이터.csv의 PENDING_NEW_SCHEMA 필드는 모델 밖이므로 미적재",
            ],
        }

    def rows(self, relative):
        path = self.root / relative
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.sources[relative] = len(rows)
        for index, row in enumerate(rows, 1):
            provenance = {
                "source_file": relative, "row": index,
                "source_key": row.get("id") or row.get("record_id") or "|".join(str(row.get(key, "")) for key in ("season", "team_code", "stadium_code", "zone_code", "access_code", "snapshot_date")),
                "status": row.get("status") or row.get("status_tag"), "evidence": row.get("evidence_type"),
                "source_url": row.get("source_url") or row.get("source"), "source_time": row.get("verified_at") or row.get("updated_at") or row.get("collected_at"),
                "raw_values": {name: row[name] for name in ("group_size", "accessible", "reservation_required") if name in row},
            }
            self.provenance.append(provenance)
            validators = {
                "group_size": lambda value: number(value, allow_text=True),
                "accessible": boolean,
                "reservation_required": boolean,
            }
            for field, validator in validators.items():
                if field not in row:
                    continue
                try:
                    validator(row[field])
                except ValueError as error:
                    rejection = {"source_file": relative, "row": index, "field": field, "value": row[field], "error": str(error)}
                    self.rejections.append(rejection)
                    provenance["import_status"] = "rejected"
                    raise CommandError(f"{relative}:{index} {error}") from error
        return rows

    def add(self, model, key, **values):
        pk = stable_id(model, key)
        natural = {name: values[name] for name in NATURAL_FIELDS.get(model.__name__, ())}
        manager = model.objects.using(self.alias)
        field_names = {field.name for field in model._meta.fields}
        if model.__name__ == "Game" and "source" in field_names:
            matches = manager.filter(
                game_date=values["game_date"], game_time=values["game_time"],
                home_team=values["home_team"], away_team=values["away_team"],
            )
            protected = matches.filter(source="tving")
            if protected.count() > 1:
                raise CommandError(f"ambiguous TVING game identity for CSV row: {key}")
            if protected.exists():
                return self.fill_tving_game(protected.get(), values)
        existing = manager.filter(**natural).first() if natural else None
        if existing:
            if "source" in field_names and getattr(existing, "source", "csv") == "tving":
                if model.__name__ == "Game":
                    return self.fill_tving_game(existing, values)
                self.counts[f"{model.__name__}.tving_skipped"] += 1
                return existing
            self.counts[f"{model.__name__}.skipped"] += 1
            return existing
        occupant = manager.filter(pk=pk).first()
        if occupant:
            raise CommandError(f"stable ID collision: {model.__name__}:{key}:{pk}")
        obj = model(id=pk, **values)
        try:
            with transaction.atomic(using=self.alias):
                excluded = [field.name for field in model._meta.fields if field.is_relation]
                excluded += [
                    field.name for field in model._meta.fields
                    if field.get_internal_type() in {"TextField", "CharField"}
                    and getattr(obj, field.name, None) == ""
                ]
                obj.clean_fields(exclude=excluded)
                obj.save(force_insert=True, using=self.alias)
        except (ValidationError, IntegrityError) as error:
            self.counts[f"{model.__name__}.rejected"] += 1
            self.rejections.append({"model": model.__name__, "key": key, "error": str(error)})
            raise CommandError(f"{model.__name__}:{key} 적재 거부: {error}") from error
        self.counts[f"{model.__name__}.imported"] += 1
        return obj

    def fill_tving_game(self, game, values):
        updates = {}
        for field in ("stadium", "postseason_stage"):
            if getattr(game, f"{field}_id") is None and values.get(field) is not None:
                updates[field] = values[field]
        if game.game_type in (None, "", "UNKNOWN") and values.get("game_type") not in (None, ""):
            updates["game_type"] = values["game_type"]
        if updates:
            for field, value in updates.items():
                setattr(game, field, value)
            game.save(update_fields=tuple(updates), using=self.alias)
            self.counts["Game.tving_filled"] += 1
        else:
            self.counts["Game.tving_skipped"] += 1
        return game

    def import_all(self):
        models = SimpleNamespace(**{name: self.model(name) for name in NATURAL_FIELDS})
        mappings = self.rows("raw/team_stadium_code_map.csv")
        teams = {row["team_code"]: self.add(models.Team, row["team_code"], team_code=row["team_code"], team_name_ko=row["team_name_ko_full"]) for row in mappings}

        operations = {row["stadium_code"]: row for row in self.rows("preprocessed/구장운영정보.csv")}
        stadiums = {}
        locations = reviewed_locations(self.root)
        for row in self.rows("preprocessed/stadium_coordinates.csv"):
            point = locations.get(row["stadium_code"])
            if point:
                row = {**row, "lng_x": str(point["lng"]), "lat_y": str(point["lat"]), "geocode_source": "OSM_REVIEWED_FIRST_TEAM_2026_09_30"}
            op = operations.get(row["stadium_code"], {})
            stadiums[row["stadium_code"]] = self.add(
                models.Stadium, row["stadium_code"], stadium_code=row["stadium_code"],
                stadium_name_ko=row["stadium_name_ko"], address=row["address"],
                longitude=Decimal(row["lng_x"]).quantize(Decimal("0.00000001")), latitude=Decimal(row["lat_y"]).quantize(Decimal("0.00000001")), geocode_source=row["geocode_source"],
                facility_manager=nullable(op.get("facility_manager")), game_operator=nullable(op.get("game_operator")),
                phone_general=nullable(op.get("phone_general")), phone_facility=nullable(op.get("phone_facility")),
                phone_ticket=nullable(op.get("phone_ticket")), collected_at=timestamp(op["verified_at"]),
            )

        contexts = {}
        for row in mappings:
            team = teams[row["team_code"]]
            stadium = stadiums.get(row["stadium_code"])
            if team and stadium:
                key = f"2026:{team.team_code}:{stadium.stadium_code}"
                contexts[("2026", team.team_code, stadium.stadium_code)] = self.add(models.HomeContext, key, season=2026, team=team, stadium=stadium)
        source_teams = teams

        stages = []
        for row in self.rows("preprocessed/kbo_schedule_postseason_tbd.csv"):
            start, end = [item.strip() for item in row["game_date"].split("~")]
            match = re.search(r"KBO 포스트시즌 (.+?) 일정", row["content"])
            stages.append(self.add(models.PostseasonStage, row["id"], stage_code=row["id"], stage_name=match.group(1) if match else "포스트시즌 단계", start_date=start, end_date=end, matchup_description=row["content"], status_tag=row["status_tag"], collected_at=timestamp(row["updated_at"])))

        games = {}
        for row in self.rows("preprocessed/kbo_schedule_full.csv"):
            games[row["id"]] = self.add(models.Game, row["id"], game_code=row["id"], home_team=source_teams[row["home_team_code"]], away_team=source_teams[row["away_team_code"]], stadium=stadiums.get(row["stadium_code"]), postseason_stage=None, game_date=row["game_date"], game_time=row["game_time"], home_score=number(row["home_score"]), away_score=number(row["away_score"]), status_code=row["status_code"], game_type=row["game_type"], collected_at=timestamp(row["updated_at"]))

        for row in self.rows("preprocessed/kbo_standing_history.csv"):
            key = f'{row["snapshot_date"]}:{row["team_code"]}'
            self.add(models.StandingHistory, key, team=source_teams[row["team_code"]], snapshot_date=row["snapshot_date"], rank=int(row["rank"]), wins=int(row["wins"]), losses=int(row["losses"]), draws=int(row["draws"]), games_behind=row["games_behind"], collected_at=timestamp(row["collected_at"]))

        zones = {}
        for row in self.rows("preprocessed/구장좌석구역.csv"):
            context = contexts[(row["season"], row["team_code"], row["stadium_code"])]
            key = f'{row["season"]}:{row["team_code"]}:{row["stadium_code"]}:{row["zone_code"]}'
            zones[(row["season"], row["team_code"], row["stadium_code"], row["zone_code"])] = self.add(models.SeatZone, key, home_context=context, zone_code=row["zone_code"], zone_name_ko=row["zone_name_ko"], level=row["level"], side=row["side"], seat_type=row["seat_type"], group_size=number(row["group_size"], allow_text=True), accessible=boolean(row["accessible"]))

        duplicate_prices = Counter()
        for row in self.rows("preprocessed/구장티켓가격.csv"):
            zone = zones[(row["season"], row["team_code"], row["stadium_code"], row["zone_code"])]
            semantic = tuple(row[name] for name in ("season", "team_code", "stadium_code", "zone_code", "price_tier", "day_type", "customer_type", "group_size", "price_krw", "valid_from", "valid_to", "discount_condition"))
            duplicate_prices[semantic] += 1
            key = ":".join(semantic) + f":duplicate-{duplicate_prices[semantic]}"
            self.add(models.TicketPrice, key, seat_zone=zone, price_tier=row["price_tier"], day_type=row["day_type"], customer_type=row["customer_type"], group_size=number(row["group_size"], allow_text=True), price_krw=int(row["price_krw"]), valid_from=nullable(row["valid_from"]), valid_to=nullable(row["valid_to"]), discount_condition=row["discount_condition"], collected_at=timestamp(row["verified_at"]))

        for row in self.rows("preprocessed/kbo_ticket_policy_structured.csv"):
            self.add(models.TicketPolicy, row["id"], policy_code=row["id"], team=source_teams[row["team_code"]], game=None, policy_type=row["policy_type"], subtype=row["policy_subtype"], open_at=None, max_tickets=number(row["max_tickets"]), channel_no=1, booking_channel=row["booking_channel_and_condition"], channel_condition=row["content"], collected_at=timestamp(row["updated_at"]))

        seat_maps = {}
        for row in self.rows("preprocessed/구장좌석도.csv"):
            context = contexts[(row["season"], row["team_code"], row["stadium_code"])]
            key = f'{row["season"]}:{row["team_code"]}:{row["stadium_code"]}'
            seat_map = self.add(models.SeatMap, key, home_context=context, map_title=row["map_title"], page_url=row["page_url"])
            seat_maps[key] = seat_map
            for asset_no, column in enumerate(("asset_url", "secondary_asset_url"), 1):
                if row[column] and seat_map:
                    self.add(models.SeatMapAsset, f"{key}:{asset_no}", seat_map=seat_map, asset_no=asset_no, asset_url=row[column], asset_role="PRIMARY" if asset_no == 1 else "SECONDARY")

        for row in self.rows("preprocessed/구장좌석경험.csv"):
            context = contexts[(row["season"], row["team_code"], row["stadium_code"])]
            key = f'{row["season"]}:{row["team_code"]}:{row["stadium_code"]}:{row["scope_code"]}'
            scope = self.add(models.SeatScope, key, home_context=context, scope_code=row["scope_code"], scope_name=row["scope_name_ko"])
            if scope:
                self.add(models.SeatView, key, seat_scope=scope, view_characteristic=row["view_characteristic"], roof_coverage=row["roof_coverage"], evidence_scope=row["evidence_scope"])

        for row in self.rows("preprocessed/구장교통정보.csv"):
            self.add(models.Transport, f'{row["stadium_code"]}:{row["access_code"]}', stadium=stadiums[row["stadium_code"]], access_code=row["access_code"], mode=row["mode"], title=row["title"], details=row["details"], parking_spaces=number(row["parking_spaces"]), reservation_required=boolean(row["reservation_required"]), collected_at=timestamp(row["verified_at"]))

        for row in self.rows("preprocessed/구장먹거리_공식매점.csv"):
            store = self.add(models.FoodStore, row["record_id"], record_code=row["record_id"], stadium=stadiums[row["stadium_code"]], store_facility=row["store_facility"], location_qty=number(row["location_qty"]), collected_at=timestamp(row["verified_at"]))
            if store and (row["floor"] or row["zone_location"]):
                self.add(models.FoodStoreLocation, f'{row["record_id"]}:1', food_store=store, location_no=1, floor=row["floor"], zone_location=row["zone_location"])
            if store and row["menu_category_official"]:
                self.add(models.FoodStoreMenu, f'{row["record_id"]}:{row["menu_category_official"]}', food_store=store, menu_category_official=row["menu_category_official"])

        for row in self.rows("preprocessed/구장부가콘텐츠_공식.csv"):
            self.add(models.StadiumContent, row["record_id"], record_code=row["record_id"], stadium=stadiums[row["stadium_code"]], content_type=row["content_type"], name=row["content_name"], floor=row["floor"], location=row["location"], official_description=row["official_description"], operating_condition=row["operating_condition"], collected_at=timestamp(row["verified_at"]))

        for row in self.rows("preprocessed/구장편의시설.csv"):
            self.add(models.Facility, row["record_id"], record_code=row["record_id"], stadium=stadiums[row["stadium_code"]], facility_type=row["facility_type"], floor=row["floor"], side=row["side"], nearby_section=row["nearby_section"], gate=row["nearby_gate"], gender=row["gender"], indoor_outdoor=row["indoor_outdoor"], location_detail=row["location_detail"], collected_at=timestamp(row["verified_at"]))

        for skipped in ("preprocessed/kbo_standing.csv", "preprocessed/external_places.csv", "preprocessed/3차_신규확보데이터.csv", "preprocessed/구장먹거리_위치_자리어때.csv", "preprocessed/구장편의시설_위치_자리어때.csv", "preprocessed/구장편의시설_보류이력.csv", "preprocessed/구장잔여정보_좌석주차버스.csv"):
            self.rows(skipped)
