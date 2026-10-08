import csv
from pathlib import Path

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from baseball.data_loader import BaseballDataLoaderV1


class StadiumImageMigrationTests(TransactionTestCase):
    def test_existing_rows_and_repeat_loader_preserve_nonmedia(self):
        before_target = [("baseball", "0007_reviewed_stadium_locations")]
        target = [("baseball", "0008_stadium_images")]
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes()))
        executor = MigrationExecutor(connection)
        executor.migrate(before_target)
        historical = executor.loader.project_state(before_target).apps
        root = Path("/data") if Path("/data").is_dir() else Path(__file__).resolve().parents[3] / "data"
        BaseballDataLoaderV1(historical, root).load()
        Stadium = historical.get_model("baseball", "Stadium")
        self.assertEqual(Stadium.objects.count(), 9)
        Stadium.objects.filter(stadium_code="JAMSIL").update(address="관리자 주소", longitude="127.12345678")
        baseline = list(Stadium.objects.order_by("id").values())
        executor = MigrationExecutor(connection)
        executor.migrate(target)
        current = executor.loader.project_state(target).apps
        Stadium = current.get_model("baseball", "Stadium")
        self.assertEqual(baseline, [{k: v for k, v in row.items() if not k.startswith("image_")} for row in Stadium.objects.order_by("id").values()])
        root = Path("/data") if Path("/data").is_dir() else Path(__file__).resolve().parents[3] / "data"
        with (root / "preprocessed/stadium_coordinates.csv").open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            stadium = Stadium.objects.get(stadium_code=row["stadium_code"])
            self.assertEqual(stadium.image_url, row["image_url"])
            self.assertEqual(stadium.image_credit_url, row["image_credit_url"])
            self.assertEqual(stadium.image_license_url, row["image_license_url"] or None)
        all_before = {model.__name__: list(model.objects.order_by("pk").values()) for model in current.get_app_config("baseball").get_models()}
        Stadium.objects.filter(stadium_code="JAMSIL").update(image_credit="stale credit")
        for _ in range(2):
            report = BaseballDataLoaderV1(current, root).load()
            self.assertEqual(report["totals"]["imported"], 0)
        self.assertEqual(all_before, {model.__name__: list(model.objects.order_by("pk").values()) for model in current.get_app_config("baseball").get_models()})
