import json

import backend.services.client_settings_service as client_settings_module
from backend.schemas.client_settings import SttSettingsUpdate
from backend.services.client_settings_service import KioskSettingsService


def test_uses_bundled_defaults_then_writes_runtime(monkeypatch, tmp_path) -> None:
    runtime_dir = tmp_path / "runtime-data"
    bundled_dir = tmp_path / "bundled-defaults"
    bundled_dir.mkdir(parents=True, exist_ok=True)
    (bundled_dir / "stt.json").write_text(
        json.dumps({"eot_threshold": 0.66}),
        encoding="utf-8",
    )

    monkeypatch.setenv("KIOSK_SETTINGS_DIR", str(runtime_dir))
    monkeypatch.setattr(client_settings_module, "_BUNDLED_DATA_DIR", bundled_dir)

    service = KioskSettingsService()
    settings = service.get_stt()
    assert settings.eot_threshold == 0.66

    updated = service.update_stt(SttSettingsUpdate(eot_threshold=0.8))
    assert updated.eot_threshold == 0.8

    runtime_file = runtime_dir / "stt.json"
    assert runtime_file.exists()
    saved_payload = json.loads(runtime_file.read_text(encoding="utf-8"))
    assert saved_payload["eot_threshold"] == 0.8


def test_invalid_presets_fall_back_to_empty_list(tmp_path) -> None:
    (tmp_path / "presets.json").write_text(
        json.dumps({"presets": "invalid"}),
        encoding="utf-8",
    )

    presets = KioskSettingsService(data_dir=tmp_path).get_presets()

    assert presets.presets == []
    assert presets.active_index is None
