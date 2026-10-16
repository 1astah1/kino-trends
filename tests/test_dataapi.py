from kinotrends.yt.dataapi import parse_duration


def test_parse_duration():
    assert parse_duration("PT58S") == 58
    assert parse_duration("PT1M3S") == 63
    assert parse_duration("PT1H2M3S") == 3723
    assert parse_duration(None) is None
    assert parse_duration("garbage") is None
