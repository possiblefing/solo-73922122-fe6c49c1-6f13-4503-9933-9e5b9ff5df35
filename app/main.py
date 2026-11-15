"""FastAPI 应用：上传两路 CSV、校验、求解、展示与导出。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from .analysis import analyze
from .models import MAX_REQUEST_BYTES, InputError
from .parsing import parse_inputs

BASE_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = BASE_DIR / "samples"
logger = logging.getLogger("bus_stop.facts")

app = FastAPI(title="班车停靠事实", version="1.0.0", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.exception_handler(InputError)
async def handle_input_error(request: Request, exc: InputError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"ok": False, "error": exc.to_dict()})


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "error": {
                "code": "invalid_request",
                "message": "请求参数不完整，请同时上传站点文件和定位文件。",
                "details": [f"{'.'.join(str(p) for p in err.get('loc', []))}: {err.get('msg', '')}" for err in exc.errors()],
            },
        },
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={"ok": False, "error": {"code": "http_error", "message": str(exc.detail), "details": []}},
        )
    return HTMLResponse(
        f"<h1>{exc.status_code}</h1><p>{exc.detail}</p><p><a href='/'>返回</a></p>",
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常：%s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": {
                "code": "internal_error",
                "message": "服务器处理时发生内部错误，未生成任何事实。",
                "details": [type(exc).__name__],
            },
        },
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


_SAMPLE_FILES = {
    "stations.csv": ("stations.csv", "text/csv; charset=utf-8"),
    "positions.csv": ("positions.csv", "text/csv; charset=utf-8"),
}


@app.get("/samples/{name}")
async def download_sample(name: str) -> Response:
    if name not in _SAMPLE_FILES:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": {"code": "not_found", "message": "样例不存在。", "details": []}},
        )
    filename, media_type = _SAMPLE_FILES[name]
    return FileResponse(SAMPLES_DIR / filename, media_type=media_type, filename=filename)


async def _read_upload(upload: UploadFile, label: str) -> bytes:
    if upload is None or not upload.filename:
        raise InputError(f"没有收到{label}，请选择要上传的 CSV 文件。", code="missing_file")
    data = await upload.read(MAX_REQUEST_BYTES + 1)
    if len(data) > MAX_REQUEST_BYTES:
        raise InputError(f"{label}（{upload.filename}）超过请求大小上限。", code="file_too_large")
    return data


async def _run(station: UploadFile, positions: UploadFile):
    station_label = station.filename if station and station.filename else "站点文件"
    position_label = positions.filename if positions and positions.filename else "定位文件"
    if station_label.lower().endswith((".xlsx", ".xls")) or position_label.lower().endswith(
        (".xlsx", ".xls")
    ):
        raise InputError("请上传 CSV 文件；Excel 文件请先另存为 CSV。", code="unsupported_file")
    station_bytes = await _read_upload(station, "站点文件")
    position_bytes = await _read_upload(positions, "定位文件")
    parsed = parse_inputs(
        station_bytes,
        position_bytes,
        station_filename=station_label,
        position_filename=position_label,
    )
    return analyze(parsed.stops, parsed.positions, warnings=parsed.warnings)


@app.post("/api/analyze")
async def api_analyze(
    station_file: UploadFile = File(...),
    position_file: UploadFile = File(...),
) -> JSONResponse:
    result = await _run(station_file, position_file)
    return JSONResponse({"ok": True, "data": result.to_dict()})


@app.post("/api/export.csv")
async def api_export(
    station_file: UploadFile = File(...),
    position_file: UploadFile = File(...),
) -> Response:
    result = await _run(station_file, position_file)
    body = result.facts_csv()
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="stop_facts.csv"'},
    )


@app.post("/analyze", response_class=HTMLResponse)
async def web_analyze(
    request: Request,
    station_file: UploadFile = File(...),
    position_file: UploadFile = File(...),
) -> HTMLResponse:
    """页面表单提交入口：出错时仍渲染页面并说明原因。"""

    try:
        result = await _run(station_file, position_file)
    except InputError as exc:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "error": exc.to_dict(),
                "station_filename": station_file.filename,
                "position_filename": position_file.filename,
            },
            status_code=422,
        )
    payload = result.to_dict()
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "data": payload,
            "station_filename": station_file.filename,
            "position_filename": position_file.filename,
        },
    )
