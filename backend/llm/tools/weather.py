"""weather domain tools."""
from datetime import date
from langchain_core.tools import ToolException
from pydantic import Field

from .common import ToolInput, _tool

class WeatherInput(ToolInput):
    stadium_code: str = Field(pattern="^(JAMSIL|GOCHEOK|MUNHAK|SUWON|DAEJEON|DAEGU|GWANGJU|SAJIK|CHANGWON)$")
    game_date: date
    game_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

def create_weather_tools():

    def get_weather(stadium_code, game_date, game_time):
        """기존 기상청 서비스로 구장 경기 시각의 단기예보를 조회한다."""
        from travel.weather_service import WeatherError, get_stadium_weather
        try:
            return get_stadium_weather(stadium_code, game_date.isoformat(), game_time)
        except WeatherError as error:
            messages = {
                "invalid_request": "예보 날짜와 구장을 확인해 주세요.",
                "weather_not_configured": "날씨 데이터 연결 설정이 필요합니다.",
                "weather_busy": "날씨 요청이 많아요. 잠시 후 다시 시도해 주세요.",
            }
            raise ToolException(messages.get(error.code, "날씨 정보를 불러오지 못했습니다.")) from None

    specs = (
        (get_weather, 'get_weather', '기상청 연동 서비스로 구장 경기 시각의 날씨를 조회한다.', WeatherInput),
    )
    return tuple(_tool(*spec) for spec in specs)
