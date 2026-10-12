import re

from baseball.models import Player, Team


class StatImportError(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("; ".join(errors))


FIELD_ALIASES = {
    "name": {"선수명", "선수"},
    "external_code": {"선수코드", "선수 코드", "선수id", "선수 id", "external_code"},
    "at_bats": {"타수"},
    "hits": {"안타"},
    "rbi": {"타점"},
    "runs": {"득점"},
    "saves": {"세", "세이브"},
    "batters_faced": {"타자"},
    "strikeouts": {"삼진", "탈삼진"},
    "pitch_count": {"투구수"},
}
MAX_DB_POSITIVE_INTEGER = 2_147_483_647


def _normalize(value):
    return "".join(value.split()).strip("|").lower()


def _split_line(line):
    stripped = line.strip()
    if "|" in stripped:
        return [cell.strip() for cell in stripped.strip("|").split("|")]
    return [cell.strip() for cell in line.split("\t")]


def _integer(value, line_number, label, max_value=MAX_DB_POSITIVE_INTEGER):
    try:
        result = int(value.replace(",", "").strip())
    except ValueError as error:
        raise ValueError(f"{line_number}행: {label} 값은 0 이상의 정수여야 합니다.") from error
    if result < 0:
        raise ValueError(f"{line_number}행: {label} 값은 0 이상이어야 합니다.")
    if result > max_value:
        raise ValueError(f"{line_number}행: {label} 값은 {max_value:,} 이하여야 합니다.")
    return result


def _team_for_header(value, teams):
    normalized = _normalize(value)
    return next(
        (
            team for team in teams
            if _normalize(team.team_code) == normalized
            or _normalize(team.team_name_ko) == normalized
        ),
        None,
    )


def parse_stat_table(text, kind, game):
    if kind == "batting":
        required_fields = ("name", "at_bats", "hits", "rbi", "runs")
        heading = "타자"
    elif kind == "pitching":
        required_fields = ("name", "saves", "batters_faced", "strikeouts", "pitch_count")
        heading = "투수"
    else:
        raise ValueError(f"지원하지 않는 기록 유형입니다: {kind}")

    if not text.strip():
        raise StatImportError([f"{heading} 기록을 입력해 주세요."])

    teams = [team for team in (game.home_team, game.away_team) if team]
    rows = []
    errors = []
    current_team = None
    field_indexes = None
    external_code_index = None
    seen_players = set()

    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        cells = _split_line(line)
        normalized_cells = [_normalize(cell) for cell in cells]
        if all(not re.search(r"[가-힣a-zA-Z0-9]", cell) for cell in normalized_cells):
            continue
        if len(cells) == 1:
            if normalized_cells[0] in {"타자", "투수"} or cells[0].lstrip().startswith("#"):
                continue
            team = _team_for_header(cells[0], teams)
            if team:
                current_team = team
                field_indexes = None
                continue

        header = {}
        for field in required_fields:
            aliases = {_normalize(alias) for alias in FIELD_ALIASES[field]}
            index = next((i for i, cell in enumerate(normalized_cells) if cell in aliases), None)
            if index is not None:
                header[field] = index
        if len(header) == len(required_fields):
            field_indexes = header
            aliases = {_normalize(alias) for alias in FIELD_ALIASES["external_code"]}
            external_code_index = next(
                (i for i, cell in enumerate(normalized_cells) if cell in aliases), None
            )
            continue

        if field_indexes is None:
            continue
        if current_team is None:
            errors.append(f"{line_number}행: 먼저 경기 구단 코드 또는 구단명을 입력해 주세요.")
            continue

        try:
            row_indexes = field_indexes
            # 코드 열을 추가한 최신 양식과 코드 열이 없는 기존 행을 함께 허용합니다.
            if external_code_index is not None and len(cells) > field_indexes["name"]:
                candidate = cells[row_indexes["name"]].strip()
                if candidate.isdecimal() and field_indexes["name"] > 0:
                    row_indexes = {field: index - 1 for field, index in field_indexes.items()}
            if len(cells) <= max(row_indexes.values()):
                raise ValueError(f"{line_number}행: 표의 열 수가 부족합니다.")
            player_name = cells[row_indexes["name"]].strip()
            if not player_name:
                raise ValueError(f"{line_number}행: 선수명이 비어 있습니다.")
            external_code = (
                cells[external_code_index].strip()
                if external_code_index is not None and row_indexes is field_indexes and len(cells) > external_code_index
                else ""
            )
            if external_code:
                player = Player.objects.filter(external_code=external_code).first()
                if player is None:
                    raise ValueError(f"{line_number}행: 선수 코드 '{external_code}'를 찾을 수 없습니다.")
                if player.name != player_name:
                    raise ValueError(f"{line_number}행: 선수 코드와 선수명이 일치하지 않습니다.")
            else:
                matches = list(Player.objects.filter(team=current_team, name=player_name).order_by("pk")[:2])
                if len(matches) != 1:
                    if matches:
                        codes = ", ".join(player.external_code for player in matches)
                        raise ValueError(
                            f"{line_number}행: {current_team.team_name_ko} 구단에 같은 이름의 선수가 여러 명입니다. "
                            f"선수코드를 입력해 주세요 ({codes})."
                        )
                    raise ValueError(f"{line_number}행: {current_team.team_name_ko} 구단에서 선수 '{player_name}'을(를) 찾을 수 없습니다.")
                player = matches[0]
            if player.pk in seen_players:
                raise ValueError(f"{line_number}행: 선수 '{player_name}' 기록이 중복되었습니다.")

            if kind == "batting":
                values = {
                        field: _integer(cells[row_indexes[field]], line_number, label)
                    for field, label in (
                        ("at_bats", "타수"),
                        ("hits", "안타"),
                        ("rbi", "타점"),
                        ("runs", "득점"),
                    )
                }
                if values["hits"] > values["at_bats"]:
                    raise ValueError(f"{line_number}행: 안타 수가 타수보다 많습니다.")
            else:
                values = {
                    "saves": _integer(cells[row_indexes["saves"]], line_number, "세이브"),
                    "batters_faced": _integer(cells[row_indexes["batters_faced"]], line_number, "타자"),
                    "strikeouts": _integer(cells[row_indexes["strikeouts"]], line_number, "삼진"),
                    "pitch_count": _integer(cells[row_indexes["pitch_count"]], line_number, "투구 수"),
                }
            rows.append({"player": player, "team": current_team, **values})
            seen_players.add(player.pk)
        except ValueError as error:
            errors.append(str(error))

    if not rows and not errors:
        errors.append(f"{heading} 표에서 저장할 선수 기록을 찾을 수 없습니다.")
    if errors:
        raise StatImportError(errors)
    return rows
