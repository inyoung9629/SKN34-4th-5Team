import json
from datetime import datetime
from email.message import Message
from http.client import IncompleteRead
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, override_settings

from . import weather_service
from .weather_service import (
    KST,
    WeatherBusyError,
    WeatherConfigurationError,
    WeatherProviderError,
    WeatherProviderUnavailable,
    WeatherValidationError,
    get_stadium_weather,
)


NOW = datetime(2026, 9, 15, 12, 0, tzinfo=KST)


def items(date="20260915", time="1900", tmp="24", sky="3", pty="0"):
    return [
        {"category": "TMP", "fcstDate": date, "fcstTime": time, "fcstValue": tmp},
        {"category": "SKY", "fcstDate": date, "fcstTime": time, "fcstValue": sky},
        {"category": "PTY", "fcstDate": date, "fcstTime": time, "fcstValue": pty},
    ]


class Response:
    def __init__(self, body, content_type="application/json"):
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size):
        return self.body[:size]


class WeatherTests(SimpleTestCase):
    def test_grid_matches_all_canonical_stadiums(self):
        expected = {
            "JAMSIL": (62, 126), "GOCHEOK": (58, 125), "MUNHAK": (55, 124),
            "SUWON": (60, 121), "DAEJEON": (68, 100), "DAEGU": (90, 90),
            "GWANGJU": (59, 75), "SAJIK": (98, 76), "CHANGWON": (89, 76),
        }
        self.assertEqual(
            {code: weather_service.lambert_grid(*coordinates) for code, coordinates in weather_service.STADIUM_COORDINATES.items()},
            expected,
        )

    def test_calendar_range_and_injection_inputs_are_rejected_by_callable_and_http(self):
        bad = [
            ("UNKNOWN", "2026-09-15", "18:00"),
            ("JAMSIL", "2026-02-30", "18:00"),
            ("JAMSIL", "2020-01-01", "18:00"),
            ("JAMSIL", "2099-01-01", "18:00"),
            ("JAMSIL;DROP TABLE", "2026-09-15", "18:00"),
            ("JAMSIL", "NaN", "NaN"),
        ]
        for stadium, date, time in bad:
            with self.subTest(stadium=stadium, date=date, time=time):
                with self.assertRaises(WeatherValidationError):
                    get_stadium_weather(stadium, date, time, now=NOW)
        for stadium, date, time in [bad[0], bad[1], bad[4], bad[5]]:
            with self.subTest(http=(stadium, date, time)):
                response = self.client.get("/api/v1/weather/", {"stadium": stadium, "date": date, "time": time})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"weather": None, "error": {"code": "invalid_request"}})
                self.assertEqual(response["Cache-Control"], "no-store")

    def test_half_hour_rounds_up_like_javascript_and_no_matching_hour_is_null(self):
        with patch.object(weather_service, "_request_items", return_value=items(time="1900")):
            weather = get_stadium_weather("JAMSIL", "2026-09-15", "18:30", now=NOW)
        self.assertEqual(weather["forecastAt"], "2026-09-15T19:00+09:00")
        with patch.object(weather_service, "_request_items", return_value=items(time="1800")):
            self.assertIsNone(get_stadium_weather("JAMSIL", "2026-09-15", "18:30", now=NOW))

    def test_target_range_is_rolling_24_hours_back_and_120_hours_forward(self):
        with patch.object(weather_service, "_request_items", return_value=[]):
            self.assertIsNone(get_stadium_weather("JAMSIL", "2026-09-14", "12:00", now=NOW))
            self.assertIsNone(get_stadium_weather("JAMSIL", "2026-09-20", "12:00", now=NOW))
            for date, time in [("2026-09-14", "11:59"), ("2026-09-20", "12:01")]:
                with self.assertRaises(WeatherValidationError):
                    get_stadium_weather("JAMSIL", date, time, now=NOW)

    def test_forecast_rounding_crosses_midnight(self):
        with patch.object(weather_service, "_request_items", return_value=items(date="20260916", time="0000")):
            weather = get_stadium_weather("JAMSIL", "2026-09-15", "23:30", now=NOW)
        self.assertEqual(weather["forecastAt"], "2026-09-16T00:00+09:00")

    def test_issue_cycle_uses_publication_delay_and_previous_day_rollover(self):
        cases = [
            (datetime(2026, 9, 15, 3, 0, tzinfo=KST), "2026-09-15T02:00+09:00"),
            (datetime(2026, 9, 15, 2, 59, tzinfo=KST), "2026-09-14T23:00+09:00"),
            (datetime(2026, 9, 15, 6, 0, tzinfo=KST), "2026-09-15T05:00+09:00"),
        ]
        for now, expected in cases:
            with self.subTest(now=now):
                self.assertEqual(weather_service._issue_datetime(now, now), datetime.fromisoformat(expected))

    def test_complete_finite_forecast_is_required(self):
        bad_items = [items(tmp="NaN"), items(sky="2"), items(pty="5"), items()[:2], [{"category": [], "fcstDate": "20260915", "fcstTime": "1900", "fcstValue": "1"}]]
        for payload in bad_items:
            with self.subTest(payload=payload), patch.object(weather_service, "_request_items", return_value=payload):
                with self.assertRaises(WeatherProviderError):
                    get_stadium_weather("JAMSIL", "2026-09-15", "19:00", now=NOW)

    def test_two_identical_calls_make_two_provider_calls_without_database_access(self):
        with patch.object(weather_service, "_request_items", return_value=items()) as provider:
            first = get_stadium_weather("JAMSIL", "2026-09-15", "19:00", now=NOW)
            second = get_stadium_weather("JAMSIL", "2026-09-15", "19:00", now=NOW)
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(first["label"], "구름많음")
        self.assertEqual(second["label"], "구름많음")

    def test_busy_is_429_and_provider_errors_are_sanitized(self):
        with patch.object(weather_service._slots, "acquire", return_value=False):
            with self.assertRaises(WeatherBusyError):
                get_stadium_weather("JAMSIL", "2026-09-15", "19:00", now=NOW)
        cases = [(WeatherBusyError("secret"), 429), (WeatherProviderError("secret"), 502), (WeatherProviderUnavailable("secret"), 503)]
        for error, status in cases:
            with patch("travel.weather_views.get_stadium_weather", side_effect=error):
                response = self.client.get("/api/v1/weather/", {"stadium": "JAMSIL", "date": "2026-09-15", "time": "19:00"})
            self.assertEqual(response.status_code, status)
            self.assertNotContains(response, "secret", status_code=status)

    @override_settings(KMA_SERVICE_KEY="abc%2B%2F%3D", KMA_API_KEY="fallback")
    def test_encoded_key_is_decoded_exactly_once(self):
        captured = self._provider_response()
        weather_service._request_items(NOW, 62, 126)
        query = parse_qs(urlsplit(captured.call_args.args[0].full_url).query)
        self.assertEqual(query["serviceKey"], ["abc+/="])
        self.assertEqual(query["numOfRows"], ["2000"])

    @override_settings(KMA_SERVICE_KEY="abc+/=", KMA_API_KEY="fallback")
    def test_decoded_key_and_primary_setting_are_preserved(self):
        captured = self._provider_response()
        weather_service._request_items(NOW, 62, 126)
        query = parse_qs(urlsplit(captured.call_args.args[0].full_url).query)
        self.assertEqual(query["serviceKey"], ["abc+/="])

    @override_settings(KMA_SERVICE_KEY="", KMA_API_KEY="fallback-key")
    def test_fallback_key_and_missing_or_invalid_keys(self):
        self.assertEqual(weather_service._service_key(), "fallback-key")
        with override_settings(KMA_SERVICE_KEY="   "):
            self.assertEqual(weather_service._service_key(), "fallback-key")
        with override_settings(KMA_API_KEY=""):
            with self.assertRaises(WeatherConfigurationError):
                weather_service._service_key()
        with override_settings(KMA_SERVICE_KEY="bad%key"):
            with self.assertRaises(WeatherConfigurationError):
                weather_service._service_key()

    @override_settings(KMA_SERVICE_KEY="test-key")
    def test_no_data_is_distinct_from_malformed_provider_response(self):
        no_data = {"response": {"header": {"resultCode": "03"}}}
        captured = self._provider_response(no_data)
        self.assertEqual(weather_service._request_items(NOW, 62, 126), [])
        self.assertTrue(captured.called)
        self._provider_response({"response": {"header": {"resultCode": "00"}, "body": {}}})
        with self.assertRaises(WeatherProviderError):
            weather_service._request_items(NOW, 62, 126)
        self._provider_response({"response": {"header": {"resultCode": "04"}}})
        with self.assertRaises(WeatherProviderError):
            weather_service._request_items(NOW, 62, 126)

    @override_settings(KMA_SERVICE_KEY="test-key")
    def test_provider_items_cannot_claim_a_different_issue_or_grid(self):
        payload = {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": [
            {"baseDate": "20260914", "baseTime": "1100", "nx": 62, "ny": 126},
        ]}}}}
        self._provider_response(payload)
        with self.assertRaises(WeatherProviderError):
            weather_service._request_items(NOW, 62, 126)

    @override_settings(KMA_SERVICE_KEY="test-key")
    def test_redirect_oversize_content_type_and_network_failure_are_rejected(self):
        opener = Mock()
        opener.open.side_effect = HTTPError("redacted", 302, "Found", {}, None)
        with patch.object(weather_service, "build_opener", return_value=opener), self.assertRaises(WeatherProviderError):
            weather_service._request_items(NOW, 62, 126)
        for response in [Response(b"x" * (weather_service.MAX_RESPONSE_BYTES + 1)), Response(b"{}", "text/html")]:
            with patch.object(weather_service, "build_opener") as build, self.assertRaises(WeatherProviderError):
                build.return_value.open.return_value = response
                weather_service._request_items(NOW, 62, 126)
        with patch.object(weather_service, "build_opener") as build, self.assertRaises(WeatherProviderUnavailable):
            build.return_value.open.side_effect = URLError("secret network detail")
            weather_service._request_items(NOW, 62, 126)
        with patch.object(weather_service, "build_opener") as build, self.assertRaises(WeatherProviderUnavailable):
            build.return_value.open.return_value = Response(b"{}")
            build.return_value.open.return_value.read = Mock(side_effect=IncompleteRead(b""))
            weather_service._request_items(NOW, 62, 126)

    def _provider_response(self, payload=None):
        payload = payload or {"response": {"header": {"resultCode": "00"}, "body": {"items": {"item": []}}}}
        opener = Mock()
        opener.open.return_value = Response(json.dumps(payload).encode())
        patcher = patch.object(weather_service, "build_opener", return_value=opener)
        patcher.start()
        self.addCleanup(patcher.stop)
        return opener.open
