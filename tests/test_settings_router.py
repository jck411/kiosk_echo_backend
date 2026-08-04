from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import settings as settings_router
from backend.services.client_settings_service import KioskSettingsService


def make_client(tmp_path) -> TestClient:
    service = KioskSettingsService(data_dir=tmp_path)
    app = FastAPI()
    app.dependency_overrides[settings_router.get_service] = lambda: service
    app.include_router(settings_router.router)
    return TestClient(app)


def test_kiosk_stt_settings_are_available(tmp_path) -> None:
    response = make_client(tmp_path).get("/api/settings/stt")

    assert response.status_code == 200
    assert response.json()["eot_threshold"] == 0.7


def test_old_multi_client_route_is_absent(tmp_path) -> None:
    response = make_client(tmp_path).get("/api/clients/kiosk/stt")

    assert response.status_code == 404
