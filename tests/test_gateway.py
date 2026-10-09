import os

from mt5.gateway import MT5Gateway


def test_candidate_paths_empty():
    assert MT5Gateway.candidate_terminal_paths("") == []
    assert MT5Gateway.candidate_terminal_paths(None) == []


def test_candidate_paths_file():
    p = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    assert MT5Gateway.candidate_terminal_paths(p) == [p]


def test_candidate_paths_directory(tmp_path):
    out = MT5Gateway.candidate_terminal_paths(str(tmp_path))
    assert out[0] == str(tmp_path)
    assert any(os.path.basename(x) == "terminal64.exe" for x in out)


def test_connect_unavailable_platform():
    gw = MT5Gateway()
    if gw.available:
        return  # only meaningful where MetaTrader5 is absent
    ok, msg = gw.connect({"login": "1", "password": "x", "server": "S"})
    assert not ok
    assert gw.status == "UNAVAILABLE"
    assert msg
