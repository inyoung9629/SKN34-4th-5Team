"""Frozen 2026-09-30 OSM venue review, not address/nearest-place geocoding.

See data/preprocessed/stadium_locations.json for feature IDs and official evidence.
The original address-geocoded CSV is retained as a recovery/audit baseline.
"""
from django.db import migrations

POINTS = {
    "JAMSIL": ("37.51218540", "127.07185130"),
    "GOCHEOK": ("37.49816530", "126.86707190"),
    "MUNHAK": ("37.43687445", "126.69328065"),
    "SUWON": ("37.29978995", "127.00973720"),
    "DAEJEON": ("36.31617880", "127.43127175"),
    "DAEGU": ("35.84099880", "128.68130610"),
    "GWANGJU": ("35.16807185", "126.88895635"),
    "SAJIK": ("35.19418415", "129.06148585"),
    "CHANGWON": ("35.22262735", "128.58238155"),
}

PREVIOUS = {
    "JAMSIL": ("37.51619878", "127.07594059"),
    "GOCHEOK": ("37.49821257", "126.86708874"),
    "MUNHAK": ("37.43508198", "126.69075983"),
    "SUWON": ("37.29784289", "127.01134810"),
    "DAEJEON": ("36.31733700", "127.42801382"),
    "DAEGU": ("35.84112892", "128.68123637"),
    "GWANGJU": ("35.16942496", "126.88880547"),
    "SAJIK": ("35.19436680", "129.05990089"),
    "CHANGWON": ("35.22198486", "128.57958012"),
}


def apply_review(apps, schema_editor):
    stadium = apps.get_model("baseball", "Stadium")
    for code, (lat, lng) in POINTS.items():
        stadium.objects.using(schema_editor.connection.alias).filter(stadium_code=code).update(
            latitude=lat, longitude=lng, geocode_source="OSM_REVIEWED_FIRST_TEAM_2026_09_30")


def reverse_review(apps, schema_editor):
    stadium = apps.get_model("baseball", "Stadium")
    for code, (lat, lng) in PREVIOUS.items():
        current_lat, current_lng = POINTS[code]
        # Do not roll back a venue that has since received another correction.
        stadium.objects.using(schema_editor.connection.alias).filter(
            stadium_code=code, latitude=current_lat, longitude=current_lng,
            geocode_source="OSM_REVIEWED_FIRST_TEAM_2026_09_30").update(
                latitude=lat, longitude=lng, geocode_source="KAKAO_LOCAL_ADDRESS_SEARCH")


class Migration(migrations.Migration):
    dependencies = [("baseball", "0006_alter_player_table_alter_playercareerrecord_table_and_more")]
    operations = [migrations.RunPython(apply_review, reverse_review)]
