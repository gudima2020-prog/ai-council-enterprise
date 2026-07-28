from datetime import timezone

from backend.autonomy.schedule import MissionScheduleService


def test_utc_timezone_does_not_require_external_tzdata() -> None:
    assert MissionScheduleService._zone("UTC") is timezone.utc
    assert MissionScheduleService._zone("Etc/UTC") is timezone.utc
    assert MissionScheduleService._zone("GMT") is timezone.utc
