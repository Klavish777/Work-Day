from core.settings import SettingsManager, REAL_CONFIRMATION_PHRASE


def make_sm(tmp_path):
    return SettingsManager(path=tmp_path / "settings.json",
                           default_path=tmp_path / "nope.json")


def test_defaults_loaded(tmp_path):
    sm = make_sm(tmp_path)
    s = sm.get()
    assert s["symbol"] == "AUDCAD"
    assert s["lot_size"] == 0.01
    assert s["mode"] == "DEMO"


def test_old_settings_migrated_to_demo_friendly_permissions(tmp_path):
    import json
    old = tmp_path / "settings.json"
    old.write_text(json.dumps({"min_confidence": 0.65,
                               "max_spread_points": 30}))
    sm = SettingsManager(path=old, default_path=tmp_path / "d.json")
    s = sm.get()
    assert s["min_confidence"] == 0.35
    assert s["max_spread_points"] == 60.0
    assert s["settings_schema"] == 2


def test_valid_update(tmp_path):
    sm = make_sm(tmp_path)
    ok, errors, s = sm.update({"lot_size": 0.05, "profit_target_usd": 0.8})
    assert ok and not errors
    assert s["lot_size"] == 0.05 and s["profit_target_usd"] == 0.8


def test_lot_cannot_exceed_max_lot(tmp_path):
    sm = make_sm(tmp_path)
    ok, errors, _ = sm.update({"lot_size": 0.5, "max_lot": 0.1})
    assert not ok and any("max_lot" in e for e in errors)


def test_invalid_timeframes_rejected(tmp_path):
    sm = make_sm(tmp_path)
    ok, errors, _ = sm.update({"timeframes": ["M2"]})
    assert not ok


def test_bybit_live_server_requires_real_mode(tmp_path):
    sm = make_sm(tmp_path)
    ok, errors, _ = sm.update({"mt5": {"server": "Bybit-Live2"}})
    assert not ok and any("REAL" in e for e in errors)
    ok, errors, s = sm.update({"mt5": {"server": "Bybit-Live2"},
                               "mode": "REAL",
                               "real_confirm": REAL_CONFIRMATION_PHRASE})
    assert ok and s["mode"] == "REAL" and s["mt5"]["server"] == "Bybit-Live2"


def test_bybit_demo_server_forces_demo_mode(tmp_path):
    sm = make_sm(tmp_path)
    sm.update({"mode": "REAL", "real_confirm": REAL_CONFIRMATION_PHRASE})
    ok, errors, s = sm.update({"mt5": {"server": "Bybit-Demo"}})
    assert ok and s["mode"] == "DEMO" and s["real_confirmed"] is False


def test_metaquotes_demo_server_forces_demo_mode(tmp_path):
    sm = make_sm(tmp_path)
    sm.update({"mode": "REAL", "real_confirm": REAL_CONFIRMATION_PHRASE})
    ok, errors, s = sm.update({"mt5": {"server": "MetaQuotes-Demo"}})
    assert ok and s["mode"] == "DEMO" and s["real_confirmed"] is False


def test_real_mode_requires_phrase(tmp_path):
    sm = make_sm(tmp_path)
    ok, errors, s = sm.update({"mode": "REAL"})
    assert not ok
    assert s["mode"] == "DEMO"
    ok, errors, s = sm.update({"mode": "REAL",
                               "real_confirm": REAL_CONFIRMATION_PHRASE})
    assert ok and s["mode"] == "REAL" and s["real_confirmed"] is True


def test_demo_resets_confirmation(tmp_path):
    sm = make_sm(tmp_path)
    sm.update({"mode": "REAL", "real_confirm": REAL_CONFIRMATION_PHRASE})
    ok, errors, s = sm.update({"mode": "DEMO"})
    assert ok and s["real_confirmed"] is False
