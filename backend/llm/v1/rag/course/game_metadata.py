"""Course-only projection of schedule provenance; no schedule lookup or model."""
DEMO_LABEL = "시연용 가상 경기"


def is_demo(game):
    return bool(game and (game.get("isDemo") is True or game.get("source") == "demo" or game.get("game_type") == "DEMO"))


def summary(game):
    return {"date": game["date"], "time": game["time"], **({"isDemo": True} if is_demo(game) else {})}


def notice(game):
    return f"{DEMO_LABEL} 기준입니다. 실제 공식 경기 일정이 아닙니다." if is_demo(game) else ""


def annotate(text, game):
    return f"{notice(game)}\n\n{text}" if is_demo(game) and DEMO_LABEL not in text else text
