from core.updater import extract_latest, is_newer, parse_version
from core.version import APP_VERSION


def test_parse_version_variants():
    assert parse_version("prototip-v1.0") == (1, 0, 0)
    assert parse_version("prototip-v1.0.2") == (1, 0, 2)
    assert parse_version("v2.1") == (2, 1, 0)
    assert parse_version("1.3") == (1, 3, 0)
    assert parse_version("prototip v1.2") == (1, 2, 0)


def test_is_newer():
    assert is_newer("prototip-v99.0", APP_VERSION) is True
    assert is_newer("prototip-v0.1", "99.0") is False
    assert is_newer("prototip-v1.0", "1.0") is False
    assert is_newer("garbage", "1.0") is False


def test_extract_latest_picks_newest_prototip():
    releases = [
        {"tag_name": "v1.0.0-apk", "html_url": "x"},
        {"tag_name": "prototip-v1.1", "html_url": "y"},
        {"tag_name": "prototip-v1.0", "html_url": "z"},
    ]
    rel = extract_latest(releases)
    assert rel["tag_name"] == "prototip-v1.1"
    assert extract_latest([]) is None
    assert extract_latest([{"tag_name": "other"}]) is None
