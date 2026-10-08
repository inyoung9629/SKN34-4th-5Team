from django.db import models


# =========================================================
# 1. TEAM — 구단
# =========================================================
class Team(models.Model):
    id = models.IntegerField(primary_key=True)
    team_code = models.TextField(unique=True)
    team_name_ko = models.TextField()
    source = models.CharField(max_length=16, default="csv")
    source_fetched_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "TEAM"


# =========================================================
# 2. STADIUM — 구장
# =========================================================
class Stadium(models.Model):
    id = models.IntegerField(primary_key=True)
    stadium_code = models.TextField(unique=True)
    stadium_name_ko = models.TextField()
    address = models.TextField()

    # ERD: numeric. 정밀도·소수 자릿수는 임시 지정.
    longitude = models.DecimalField(max_digits=12, decimal_places=8)
    latitude = models.DecimalField(max_digits=12, decimal_places=8)

    image_url = models.CharField(max_length=500, null=True, blank=True)
    image_source_url = models.URLField(max_length=500, null=True, blank=True)
    image_credit = models.CharField(max_length=300, null=True, blank=True)
    image_credit_url = models.URLField(max_length=500, null=True, blank=True)
    image_license_url = models.URLField(max_length=500, null=True, blank=True)

    parking_map_url = models.CharField(max_length=500, null=True, blank=True)
    parking_map_source_url = models.URLField(max_length=500, null=True, blank=True)
    parking_map_credit = models.CharField(max_length=300, null=True, blank=True)
    parking_map_title = models.CharField(max_length=300, null=True, blank=True)
    parking_map_summary = models.TextField(null=True, blank=True)
    parking_map_kind = models.CharField(max_length=30, null=True, blank=True, choices=[(kind, kind) for kind in ("entrance", "preferred-area", "nearby-alternatives", "access-gates")])
    parking_map_visual_notes = models.JSONField(null=True, blank=True)
    parking_map_captured_at = models.DateField(null=True, blank=True)
    parking_map_width = models.PositiveIntegerField(null=True, blank=True)
    parking_map_height = models.PositiveIntegerField(null=True, blank=True)

    geocode_source = models.TextField()
    facility_manager = models.TextField(null=True, blank=True)
    game_operator = models.TextField(null=True, blank=True)
    phone_general = models.TextField(null=True, blank=True)
    phone_facility = models.TextField(null=True, blank=True)
    phone_ticket = models.TextField(null=True, blank=True)
    collected_at = models.DateTimeField()

    class Meta:
        db_table = "STADIUM"


# =========================================================
# 3. HOME_CONTEXT — 시즌별 구단·홈구장 연결
# =========================================================
class HomeContext(models.Model):
    id = models.IntegerField(primary_key=True)
    season = models.SmallIntegerField()

    # 실제 DB 컬럼: team_id INTEGER
    team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="home_contexts",
    )

    # 실제 DB 컬럼: stadium_id INTEGER
    stadium = models.ForeignKey(
        Stadium,
        on_delete=models.PROTECT,
        related_name="home_contexts",
    )

    class Meta:
        db_table = "HOME_CONTEXT"
        constraints = [
            models.UniqueConstraint(
                fields=["season", "team", "stadium"],
                name="uq_home_context",
            ),
        ]


# =========================================================
# 4. 포스트시즌 라운드 정보
# 영문 테이블명: POSTSEASON_STAGE
# =========================================================
class PostseasonStage(models.Model):
    id = models.IntegerField(primary_key=True)
    stage_code = models.TextField(unique=True)
    stage_name = models.TextField()
    start_date = models.DateField()
    end_date = models.DateField()
    matchup_description = models.TextField()
    status_tag = models.TextField()
    collected_at = models.DateTimeField()

    class Meta:
        db_table = "POSTSEASON_STAGE"


# =========================================================
# 5. GAME — 개별 경기
# =========================================================
class Game(models.Model):
    id = models.IntegerField(primary_key=True)
    game_code = models.TextField(unique=True)

    home_team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="home_games",
        null=True,
        blank=True,
    )
    away_team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="away_games",
        null=True,
        blank=True,
    )
    stadium = models.ForeignKey(
        Stadium,
        on_delete=models.PROTECT,
        related_name="games",
        null=True,
        blank=True,
    )
    postseason_stage = models.ForeignKey(
        PostseasonStage,
        on_delete=models.PROTECT,
        related_name="games",
        null=True,
        blank=True,
    )

    game_date = models.DateField()
    game_time = models.TimeField()
    home_score = models.IntegerField(null=True, blank=True)
    away_score = models.IntegerField(null=True, blank=True)
    status_code = models.TextField()
    game_type = models.TextField()
    collected_at = models.DateTimeField()
    source = models.CharField(max_length=16, default="csv")
    source_external_code = models.CharField(max_length=60, unique=True, null=True, blank=True)
    source_stadium_name = models.CharField(max_length=60, blank=True)
    source_status_label = models.CharField(max_length=30, blank=True)
    source_home_code = models.CharField(max_length=2, blank=True)
    source_home_name = models.CharField(max_length=30, blank=True)
    source_away_code = models.CharField(max_length=2, blank=True)
    source_away_name = models.CharField(max_length=30, blank=True)
    source_fetched_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    home_starting_pitcher = models.CharField(max_length=60, null=True, blank=True)
    away_starting_pitcher = models.CharField(max_length=60, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "GAME"


# =========================================================
# 6. STANDING_HISTORY — 구단별 일자 순위
# =========================================================
class StandingHistory(models.Model):
    id = models.IntegerField(primary_key=True)

    team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="standing_histories",
    )

    snapshot_date = models.DateField()
    rank = models.IntegerField()
    wins = models.IntegerField()
    losses = models.IntegerField()
    draws = models.IntegerField()

    # ERD: numeric. 정밀도·소수 자릿수는 임시 지정.
    games_behind = models.DecimalField(max_digits=8, decimal_places=2)

    collected_at = models.DateTimeField()
    played = models.PositiveSmallIntegerField(null=True, blank=True)
    win_rate = models.CharField(max_length=24, blank=True)
    winning_streak = models.CharField(max_length=20, blank=True)
    batting_average = models.CharField(max_length=24, blank=True)
    era = models.CharField(max_length=24, blank=True)
    last_ten = models.CharField(max_length=30, blank=True)
    source = models.CharField(max_length=16, default="csv")
    source_fetched_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "STANDING_HISTORY"
        constraints = [
            models.UniqueConstraint(
                fields=["team", "snapshot_date"],
                name="uq_standing_team_date",
            ),
        ]


class SyncedRecord(models.Model):
    source_fetched_at = models.DateTimeField()
    last_synced_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TeamProfile(SyncedRecord):
    team = models.OneToOneField(Team, on_delete=models.PROTECT, related_name="provider_profile")
    external_code = models.CharField(max_length=2, unique=True)
    short_name = models.CharField(max_length=30)
    image_url = models.URLField(max_length=1000, null=True, blank=True)
    background_image_url = models.URLField(max_length=1000, null=True, blank=True)
    season_title = models.CharField(max_length=80)
    roster_codes = models.JSONField(default=dict)
    top_keys = models.JSONField(default=dict)


class TeamSeasonRecord(SyncedRecord):
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="season_records")
    season = models.PositiveSmallIntegerField()
    category = models.CharField(max_length=8, choices=(("main", "Main"), ("box", "Box")))
    title = models.CharField(max_length=40)
    value = models.CharField(max_length=60)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("team", "season", "category", "title"), name="uq_tving_team_season_record")]


class Player(models.Model):
    external_code = models.CharField(max_length=40, primary_key=True)
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="players")
    name = models.CharField(max_length=80)
    image_url = models.URLField(max_length=1000, null=True, blank=True)
    positions = models.JSONField(default=list)
    back_number = models.CharField(max_length=20, blank=True)
    join_date = models.CharField(max_length=40, blank=True)
    birth_date = models.CharField(max_length=40, blank=True)
    body = models.JSONField(default=list)
    education = models.CharField(max_length=200, blank=True)
    draft_order = models.CharField(max_length=100, blank=True)
    team_color = models.CharField(max_length=20, blank=True)
    team_logo_url = models.URLField(max_length=1000, null=True, blank=True)
    season_title = models.CharField(max_length=80, blank=True)
    career_title = models.CharField(max_length=80, blank=True)
    profile_source_fetched_at = models.DateTimeField(null=True, blank=True)
    profile_last_synced_at = models.DateTimeField(null=True, blank=True)
    identity_source_fetched_at = models.DateTimeField(null=True, blank=True)
    identity_last_synced_at = models.DateTimeField(null=True, blank=True)
    detail_source_fetched_at = models.DateTimeField(null=True, blank=True)
    detail_last_synced_at = models.DateTimeField(null=True, blank=True)
    detail_record_keys = models.JSONField(default=list)
    career_positions = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)


class PlayerSeasonRecord(SyncedRecord):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="season_records")
    season = models.PositiveSmallIntegerField()
    record_kind = models.CharField(max_length=16, choices=(("pitcher", "Pitcher ranking"), ("hitter", "Hitter ranking"), ("detail", "Player detail")))
    record_key = models.CharField(max_length=60)
    rank = models.PositiveSmallIntegerField(null=True, blank=True)
    title = models.CharField(max_length=60, blank=True)
    value = models.CharField(max_length=60, blank=True)
    rank_label = models.CharField(max_length=40, null=True, blank=True)
    is_first_rank = models.BooleanField(default=False)
    metrics = models.JSONField(default=dict)
    graphs = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("player", "season", "record_kind", "record_key"), name="uq_tving_player_season_record")]


class PlayerCareerRecord(SyncedRecord):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="career_records")
    position = models.PositiveSmallIntegerField()
    season_label = models.CharField(max_length=60)
    title = models.CharField(max_length=80)
    columns = models.JSONField(default=list)
    metrics = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("player", "position"), name="uq_tving_player_career_record")]


class TeamRoster(SyncedRecord):
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="roster")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="roster_memberships")
    position = models.CharField(max_length=16, choices=(("pitcher", "Pitcher"), ("infielder", "Infielder"), ("outfielder", "Outfielder"), ("catcher", "Catcher")))
    back_number = models.CharField(max_length=20, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("team", "player"), name="uq_tving_team_roster")]


class TeamTopPlayer(SyncedRecord):
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="top_players")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="team_top_records")
    athlete_type = models.CharField(max_length=8, choices=(("pitcher", "Pitcher"), ("hitter", "Hitter")))
    category = models.CharField(max_length=40)
    rank = models.PositiveSmallIntegerField()
    value = models.CharField(max_length=40, blank=True)
    image_url = models.URLField(max_length=1000, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("team", "player", "athlete_type", "category"), name="uq_tving_team_top_player")]


class ScheduleDay(SyncedRecord):
    date = models.DateField(unique=True)
    status = models.CharField(max_length=8, choices=(("ready", "Ready"), ("empty", "Empty"), ("pending", "Pending"), ("error", "Error")))
    game_count = models.PositiveSmallIntegerField()
    game_codes = models.JSONField(default=list)


class ProviderSnapshot(models.Model):
    DAILY = "daily"
    MONTH = "month"
    TEAM = "team"
    ATHLETE = "athlete"
    KINDS = ((DAILY, "Daily"), (MONTH, "Month"), (TEAM, "Team"), (ATHLETE, "Athlete"))

    resource_kind = models.CharField(max_length=16, choices=KINDS)
    resource_key = models.CharField(max_length=16)
    payload = models.JSONField()
    source_fetched_at = models.DateTimeField()
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("resource_kind", "resource_key"), name="uq_tving_snapshot_resource"),
            models.CheckConstraint(
                condition=(
                    models.Q(resource_kind="daily", resource_key__regex=r"^\d{4}-\d{2}-\d{2}$")
                    | models.Q(resource_kind="month", resource_key__regex=r"^\d{4}-\d{2}$")
                    | models.Q(resource_kind="team", resource_key__regex=r"^(SS|KT|LG|HT|OB|NC|HH|LT|SK|WO)$")
                    | models.Q(resource_kind="athlete", resource_key__regex=r"^\d{4,12}$")
                ),
                name="ck_tving_snapshot_identity",
            ),
        ]
        indexes = [models.Index(fields=("resource_kind", "resource_key"), name="tving_resource_idx")]


# =========================================================
# 7. SEAT_ZONE — 구단·시즌별 좌석구역
# =========================================================
class SeatZone(models.Model):
    id = models.IntegerField(primary_key=True)

    home_context = models.ForeignKey(
        HomeContext,
        on_delete=models.PROTECT,
        related_name="seat_zones",
    )

    zone_code = models.TextField()
    zone_name_ko = models.TextField()
    level = models.TextField()
    side = models.TextField()
    seat_type = models.TextField()
    group_size = models.IntegerField(null=True, blank=True)
    accessible = models.BooleanField(null=True, blank=True)

    class Meta:
        db_table = "SEAT_ZONE"
        constraints = [
            models.UniqueConstraint(
                fields=["home_context", "zone_code"],
                name="uq_seat_zone_context_code",
            ),
        ]


# =========================================================
# 8. TICKET_PRICE — 구역별 조건부 가격
# =========================================================
class TicketPrice(models.Model):
    id = models.IntegerField(primary_key=True)

    seat_zone = models.ForeignKey(
        SeatZone,
        on_delete=models.PROTECT,
        related_name="ticket_prices",
    )

    price_tier = models.TextField()
    day_type = models.TextField()
    customer_type = models.TextField()
    group_size = models.IntegerField(null=True, blank=True)
    price_krw = models.IntegerField()
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    discount_condition = models.TextField()
    collected_at = models.DateTimeField()

    class Meta:
        db_table = "TICKET_PRICE"


# =========================================================
# 9. TICKET_POLICY — 예매 정책·예매처·적용 조건
# =========================================================
class TicketPolicy(models.Model):
    id = models.IntegerField(primary_key=True)
    policy_code = models.TextField()

    team = models.ForeignKey(
        Team,
        on_delete=models.PROTECT,
        related_name="ticket_policies",
    )
    game = models.ForeignKey(
        Game,
        on_delete=models.PROTECT,
        related_name="ticket_policies",
        null=True,
        blank=True,
    )

    policy_type = models.TextField()
    subtype = models.TextField()
    open_at = models.DateTimeField(null=True, blank=True)
    max_tickets = models.IntegerField(null=True, blank=True)
    channel_no = models.IntegerField()
    booking_channel = models.TextField()
    channel_condition = models.TextField()
    collected_at = models.DateTimeField()

    class Meta:
        db_table = "TICKET_POLICY"
        constraints = [
            models.UniqueConstraint(
                fields=["policy_code", "channel_no"],
                name="uq_ticket_policy_channel",
            ),
        ]


# =========================================================
# 10. 좌석도
# 영문 테이블명: SEAT_MAP
# =========================================================
class SeatMap(models.Model):
    id = models.IntegerField(primary_key=True)

    home_context = models.ForeignKey(
        HomeContext,
        on_delete=models.PROTECT,
        related_name="seat_maps",
    )

    map_title = models.TextField()
    page_url = models.TextField()

    class Meta:
        db_table = "SEAT_MAP"


# =========================================================
# 11. 좌석도 이미지
# 영문 테이블명: SEAT_MAP_ASSET
# =========================================================
class SeatMapAsset(models.Model):
    id = models.IntegerField(primary_key=True)

    seat_map = models.ForeignKey(
        SeatMap,
        on_delete=models.PROTECT,
        related_name="assets",
    )

    asset_no = models.IntegerField()
    asset_url = models.TextField()
    asset_role = models.TextField()
    source_url = models.URLField(max_length=500, null=True, blank=True)

    class Meta:
        db_table = "SEAT_MAP_ASSET"
        constraints = [
            models.UniqueConstraint(
                fields=["seat_map", "asset_no"],
                name="uq_seat_map_asset_no",
            ),
        ]


# =========================================================
# 12. 좌석 관람 범위
# 영문 테이블명: SEAT_SCOPE
# =========================================================
class SeatScope(models.Model):
    id = models.IntegerField(primary_key=True)

    home_context = models.ForeignKey(
        HomeContext,
        on_delete=models.PROTECT,
        related_name="seat_scopes",
    )

    scope_code = models.TextField()
    scope_name = models.TextField()

    class Meta:
        db_table = "SEAT_SCOPE"
        constraints = [
            models.UniqueConstraint(
                fields=["home_context", "scope_code"],
                name="uq_seat_scope_context_code",
            ),
        ]


# =========================================================
# 13. 좌석 시야/특성
# 영문 테이블명: SEAT_VIEW
# =========================================================
class SeatView(models.Model):
    id = models.IntegerField(primary_key=True)

    seat_scope = models.ForeignKey(
        SeatScope,
        on_delete=models.PROTECT,
        related_name="seat_views",
    )

    view_characteristic = models.TextField()
    roof_coverage = models.TextField()
    evidence_scope = models.TextField()

    class Meta:
        db_table = "SEAT_VIEW"


# =========================================================
# 14. TRANSPORT — 교통·주차 안내
# =========================================================
class Transport(models.Model):
    id = models.IntegerField(primary_key=True)

    stadium = models.ForeignKey(
        Stadium,
        on_delete=models.PROTECT,
        related_name="transports",
    )

    access_code = models.TextField()
    mode = models.TextField()
    title = models.TextField()
    details = models.TextField()
    parking_spaces = models.IntegerField(null=True, blank=True)
    reservation_required = models.BooleanField(null=True, blank=True)
    collected_at = models.DateTimeField()

    class Meta:
        db_table = "TRANSPORT"
        constraints = [
            models.UniqueConstraint(
                fields=["stadium", "access_code"],
                name="uq_transport_stadium_access",
            ),
        ]


# =========================================================
# 15. FOOD_STORE — 공식 매점
# =========================================================
class FoodStore(models.Model):
    id = models.IntegerField(primary_key=True)
    record_code = models.TextField(unique=True)

    stadium = models.ForeignKey(
        Stadium,
        on_delete=models.PROTECT,
        related_name="food_stores",
    )

    store_facility = models.TextField()
    location_qty = models.IntegerField(null=True, blank=True)
    collected_at = models.DateTimeField()

    class Meta:
        db_table = "FOOD_STORE"


# =========================================================
# 16. FOOD_STORE_LOCATION — 매점별 개별 위치
# =========================================================
class FoodStoreLocation(models.Model):
    id = models.IntegerField(primary_key=True)

    food_store = models.ForeignKey(
        FoodStore,
        on_delete=models.PROTECT,
        related_name="locations",
    )

    location_no = models.IntegerField()
    floor = models.TextField()
    zone_location = models.TextField()

    class Meta:
        db_table = "FOOD_STORE_LOCATION"
        constraints = [
            models.UniqueConstraint(
                fields=["food_store", "location_no"],
                name="uq_food_store_location_no",
            ),
        ]


# =========================================================
# 17. FOOD_STORE_MENU — 매점별 공식 메뉴 분류
# =========================================================
class FoodStoreMenu(models.Model):
    id = models.IntegerField(primary_key=True)

    food_store = models.ForeignKey(
        FoodStore,
        on_delete=models.PROTECT,
        related_name="menus",
    )

    menu_category_official = models.TextField()

    class Meta:
        db_table = "FOOD_STORE_MENU"
        constraints = [
            models.UniqueConstraint(
                fields=["food_store", "menu_category_official"],
                name="uq_food_store_menu_category",
            ),
        ]


# =========================================================
# 18. STADIUM_CONTENT — 구장 부가 콘텐츠
# =========================================================
class StadiumContent(models.Model):
    id = models.IntegerField(primary_key=True)
    record_code = models.TextField(unique=True)

    stadium = models.ForeignKey(
        Stadium,
        on_delete=models.PROTECT,
        related_name="contents",
    )

    content_type = models.TextField()
    name = models.TextField()
    floor = models.TextField()
    location = models.TextField()
    official_description = models.TextField()
    operating_condition = models.TextField()
    collected_at = models.DateTimeField()

    class Meta:
        db_table = "STADIUM_CONTENT"


# =========================================================
# 19. FACILITY — 편의시설
# =========================================================
class Facility(models.Model):
    id = models.IntegerField(primary_key=True)
    record_code = models.TextField(unique=True)

    stadium = models.ForeignKey(
        Stadium,
        on_delete=models.PROTECT,
        related_name="facilities",
    )

    facility_type = models.TextField()
    floor = models.TextField()
    side = models.TextField()
    nearby_section = models.TextField()
    gate = models.TextField()
    gender = models.TextField()
    indoor_outdoor = models.TextField()
    location_detail = models.TextField()
    collected_at = models.DateTimeField()

    class Meta:
        db_table = "FACILITY"
