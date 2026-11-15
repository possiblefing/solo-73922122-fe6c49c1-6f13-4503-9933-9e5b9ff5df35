"""FastAPI 接口与页面测试。"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app

STATIONS = "站名,纬度,经度,半径米\nA,30.0000,120.0000,60\nB,30.0060,120.0000,60\n".encode()
POSITIONS = (
    "时刻UTC,纬度,经度\n"
    "2026-11-15T08:00:00Z,30.0000,120.0000\n"
    "2026-11-15T08:00:30Z,30.0000,120.0000\n"
    "2026-11-15T08:01:00Z,30.0000,120.0000\n"
    "2026-11-15T08:03:00Z,30.0060,120.0000\n"
    "2026-11-15T08:03:30Z,30.0060,120.0000\n"
).encode()

AMBIGUOUS_STATIONS = "站名,纬度,经度,半径米\nA,30.0000,120.0000,60\nB,30.0000,120.0000,60\n".encode()
AMBIGUOUS_POSITIONS = (
    "时刻UTC,纬度,经度\n"
    "2026-11-15T08:00:00Z,30.0000,120.0000\n"
    "2026-11-15T08:00:30Z,30.0000,120.0000\n"
).encode()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def upload(client: TestClient, station: bytes, position: bytes, path: str = "/api/analyze"):
    return client.post(
        path,
        files={
            "station_file": ("stations.csv", io.BytesIO(station), "text/csv"),
            "position_file": ("positions.csv", io.BytesIO(position), "text/csv"),
        },
    )


def test_index_page_renders(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "班车停靠事实" in response.text
    assert "上传数据" in response.text


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"ok": True}


def test_samples_are_downloadable(client: TestClient) -> None:
    for name in ("stations.csv", "positions.csv"):
        response = client.get(f"/samples/{name}")
        assert response.status_code == 200
        assert "纬度" in response.text
        assert "attachment" in response.headers.get("content-disposition", "")
    assert client.get("/samples/nope.csv").status_code == 404


def test_api_analyze_success(client: TestClient) -> None:
    response = upload(client, STATIONS, POSITIONS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["summary"]["optimal_stop_count"] == 2
    assert data["summary"]["fact_count"] == 2
    names = [fact["name"] for fact in data["facts"]]
    assert names == ["A", "B"]
    assert data["facts"][0]["arrival"] == "2026-11-15T08:00:00Z"
    assert data["facts"][1]["departure"] == "2026-11-15T08:03:30Z"


def test_api_pending_is_reported_with_windows(client: TestClient) -> None:
    response = upload(client, AMBIGUOUS_STATIONS, AMBIGUOUS_POSITIONS)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"]["fact_count"] == 0
    assert data["summary"]["pending_count"] == 2
    assert data["summary"]["has_pending"] is True
    for item in data["pending"]:
        assert len(item["possible_windows"]) == 1
        assert item["possible_windows"][0]["arrival"]


def test_api_missing_column_returns_422_with_reason(client: TestClient) -> None:
    bad = "站名,纬度,半径米\nA,30.0,60\n".encode()
    response = upload(client, bad, POSITIONS)
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "missing_column"
    assert "经度" in body["error"]["message"]
    # 不得输出任何事实
    assert "facts" not in body


def test_api_no_dwell_returns_422_and_no_facts(client: TestClient) -> None:
    positions = (
        "时刻UTC,纬度,经度\n"
        "2026-11-15T08:00:00Z,30.0000,120.0000\n"
        "2026-11-15T08:01:00Z,30.0500,120.0000\n"
        "2026-11-15T08:02:00Z,30.0000,120.0000\n"
    ).encode()
    response = upload(client, STATIONS, positions)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "no_dwell"
    assert "没有可用停靠" in error["message"]
    assert any("A" in detail for detail in error["details"])


def test_api_missing_file_returns_422(client: TestClient) -> None:
    response = client.post(
        "/api/analyze",
        files={"station_file": ("stations.csv", io.BytesIO(STATIONS), "text/csv")},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid_request"


def test_api_rejects_excel_extension(client: TestClient) -> None:
    response = client.post(
        "/api/analyze",
        files={
            "station_file": ("stations.xlsx", io.BytesIO(b"PK\x03\x04"), "application/octet-stream"),
            "position_file": ("positions.csv", io.BytesIO(POSITIONS), "text/csv"),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_file"


def test_api_export_csv_contains_only_facts(client: TestClient) -> None:
    response = upload(client, STATIONS, POSITIONS, path="/api/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    text = response.content.decode("utf-8-sig")
    assert "A" in text and "B" in text
    assert text.count("\r\n") == 3  # 表头 + 2 条事实


def test_web_form_success_renders_result_page(client: TestClient) -> None:
    response = upload(client, STATIONS, POSITIONS, path="/analyze")
    assert response.status_code == 200
    assert "停靠事实" in response.text
    assert "2026-11-15T08:00:00Z" in response.text


def test_web_form_error_rerenders_with_reason(client: TestClient) -> None:
    response = upload(client, b"", POSITIONS, path="/analyze")
    assert response.status_code == 422
    assert "无法生成停靠事实" in response.text
    assert "为空" in response.text


def test_station_name_is_html_escaped(client: TestClient) -> None:
    station = "站名,纬度,经度,半径米\n<script>alert(1)</script>,30.0,120.0,50\n".encode()
    points = (
        "时刻UTC,纬度,经度\n"
        "2026-11-15T08:00:00Z,30.0,120.0\n"
        "2026-11-15T08:00:30Z,30.0,120.0\n"
    ).encode()
    response = upload(client, station, points, path="/analyze")
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_unknown_page_returns_html_404(client: TestClient) -> None:
    response = client.get("/not-a-page")
    assert response.status_code == 404
    assert "返回" in response.text


def test_api_unknown_path_returns_json_404(client: TestClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json()["ok"] is False
