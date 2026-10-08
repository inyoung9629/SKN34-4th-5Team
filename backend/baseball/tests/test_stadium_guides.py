import csv
from pathlib import Path
from types import SimpleNamespace

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TransactionTestCase

from baseball.data_loader import BaseballDataLoaderV1
from baseball.stadium_guides import GUIDE_PREFETCH, parking_map, seating_map


class SeatingMapSelectionTests(SimpleTestCase):
    def test_latest_supported_diagram_and_explicit_season(self):
        def relation(*items):
            return SimpleNamespace(all=lambda: items)

        asset = SimpleNamespace(pk=1, asset_no=3, asset_role="LOCAL_DIAGRAM",
                                asset_url="/images/stadiums/seating-maps/jamsil.png", source_url="https://www.lgtwins.com/ticket/general")
        seat = SimpleNamespace(pk=1, assets=relation(asset), page_url=asset.source_url, map_title="LG 2026")
        supported = SimpleNamespace(pk=1, season=2026, team=SimpleNamespace(team_code="LG"), seat_maps=relation(seat))
        empty = SimpleNamespace(pk=2, season=2027, team=supported.team, seat_maps=relation())
        stadium = SimpleNamespace(home_contexts=relation(empty, supported))
        self.assertEqual(seating_map(stadium)["season"], 2026)
        self.assertEqual(seating_map(stadium)["team_code"], "LG")
        self.assertEqual(seating_map(stadium, season=2026)["imageUrl"], asset.asset_url)
        self.assertIsNone(seating_map(stadium, season=2027))
        self.assertIsNone(seating_map(stadium, season=2025))
        empty.seat_maps = relation(seat)
        self.assertEqual(seating_map(stadium)["season"], 2027)
        self.assertEqual(seating_map(stadium, season=2026)["season"], 2026)


class StadiumGuideMigrationTests(TransactionTestCase):
    def test_upgrade_and_repeat_preserve_existing_rows(self):
        before = [("baseball", "0008_stadium_images")]
        target = [("baseball", "0009_stadium_guides")]
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes()))
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        apps.get_model("baseball", "SeatMapAsset").objects.filter(asset_role="LOCAL_DIAGRAM").delete()
        root = Path("/data") if Path("/data").is_dir() else Path(__file__).resolve().parents[3] / "data"
        BaseballDataLoaderV1(apps, root).load()
        Stadium = apps.get_model("baseball", "Stadium")
        Stadium.objects.filter(stadium_code="JAMSIL").update(address="관리자 주소", longitude="127.12345678")
        baseline = {m.__name__: list(m.objects.order_by("pk").values()) for m in apps.get_app_config("baseball").get_models()}
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        root = Path("/data") if Path("/data").is_dir() else Path(__file__).resolve().parents[3] / "data"
        current = {m.__name__: list(m.objects.order_by("pk").values()) for m in apps.get_app_config("baseball").get_models()}
        for name, rows in baseline.items():
            indexed = {row["id"]: row for row in current[name]}
            for row in rows:
                self.assertEqual(row, {key: indexed[row["id"]][key] for key in row}, name)
        self.assertEqual(len(current["SeatMapAsset"]), len(baseline["SeatMapAsset"]) + 9)
        Stadium = apps.get_model("baseball", "Stadium")
        with (root / "preprocessed/stadium_coordinates.csv").open(encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                stadium = Stadium.objects.prefetch_related(*GUIDE_PREFETCH).get(stadium_code=row["stadium_code"])
                self.assertEqual(parking_map(stadium)["imageUrl"], row["parking_map_url"])
                self.assertEqual(parking_map(stadium)["capturedAt"], row["parking_map_captured_at"])
                self.assertTrue(seating_map(stadium)["imageUrl"].startswith("/images/stadiums/seating-maps/"))
                if stadium.stadium_code == "JAMSIL":
                    self.assertEqual(seating_map(stadium)["team_code"], "LG")
        for _ in range(2):
            report = BaseballDataLoaderV1(apps, root).load()
            self.assertEqual(report["totals"]["imported"], 0)
        self.assertEqual(current, {m.__name__: list(m.objects.order_by("pk").values()) for m in apps.get_app_config("baseball").get_models()})
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        self.assertEqual(BaseballDataLoaderV1(old_apps, root).load()["totals"]["imported"], 0)
        executor = MigrationExecutor(connection)
        executor.migrate(target)
