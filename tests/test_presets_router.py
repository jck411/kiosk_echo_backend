from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import presets as presets_router
from backend.routers import settings as settings_router
from backend.services.client_settings_service import KioskSettingsService


def make_client(tmp_path) -> TestClient:
    service = KioskSettingsService(data_dir=tmp_path)
    app = FastAPI()
    app.dependency_overrides[settings_router.get_service] = lambda: service
    app.include_router(presets_router.router)
    return TestClient(app)


def test_kiosk_presets_are_available(tmp_path) -> None:
    response = make_client(tmp_path).get("/api/presets")

    assert response.status_code == 200
    assert response.json() == {"presets": [], "active_index": None}
