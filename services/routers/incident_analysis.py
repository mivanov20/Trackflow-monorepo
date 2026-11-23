"""Análisis del archivo de incidencias (`POST /api/incidents/analyze`, `GET /api/incidents/results/export`).

Usa exactamente la misma lógica que `scripts/analyze.py` (`trackflow_shared.incident_analysis`).
Las dos rutas exigen sesión, como el resto de `/api/incidents`.

Privacidad: el archivo trae emails de clientes. Se lee en streaming desde un archivo temporal que se
borra al terminar, y solo se conservan los números agregados del último análisis de cada usuario
(en memoria: se pierden al reiniciar la API). Nunca se guarda, devuelve ni registra un valor de celda.
"""

import io
import logging
import os
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile
from trackflow_shared.incident_analysis import AnalysisResult, InvalidFileError, analyze_csv, results_csv_text

from ..accounts import User
from ..auth import get_current_user, require_staff
from .incidents import IncidentRoute, error_response

logger = logging.getLogger("services.incident_analysis")

DEFAULT_MAX_UPLOAD_MB = 250  # un millón de filas pesa del orden de 150-250 MB
MULTIPART_OVERHEAD_BYTES = 1_048_576  # cabeceras y límites del cuerpo multipart, además del archivo
EXPORT_FILENAME = "results.csv"

router = APIRouter(
    prefix="/api/incidents",
    tags=["incident analysis"],
    dependencies=[Depends(require_staff)],
    route_class=IncidentRoute,
)

# Último análisis de cada usuario (solo agregados). Un bloqueo porque las rutas síncronas corren en hilos.
_last_results: dict[str, AnalysisResult] = {}
_results_lock = threading.Lock()


def max_upload_bytes() -> int:
    """Tamaño máximo del archivo, de ANALYSIS_MAX_UPLOAD_MB (por defecto 250)."""
    raw = os.getenv("ANALYSIS_MAX_UPLOAD_MB", str(DEFAULT_MAX_UPLOAD_MB))
    try:
        megabytes = int(raw)
    except ValueError as error:
        raise RuntimeError("ANALYSIS_MAX_UPLOAD_MB must be a whole number of megabytes.") from error
    if megabytes < 1:
        raise RuntimeError("ANALYSIS_MAX_UPLOAD_MB must be at least 1.")
    return megabytes * 1024 * 1024


def validate_analysis_config() -> None:
    """Falla al arrancar si el límite de subida está mal configurado."""
    max_upload_bytes()


def remember_result(user_id: str, result: AnalysisResult) -> None:
    with _results_lock:
        _last_results[user_id] = result


def last_result(user_id: str) -> AnalysisResult | None:
    with _results_lock:
        return _last_results.get(user_id)


def clear_results() -> None:
    """Olvida los análisis guardados (para las pruebas)."""
    with _results_lock:
        _last_results.clear()


def _safe_name(filename: str | None) -> str:
    """Nombre del archivo sin carpetas ni caracteres de control, para mostrarlo en el resumen."""
    name = Path((filename or "").replace("\\", "/")).name
    return "".join(char for char in name if char.isprintable())[:200]


def _analyze_stream(upload: UploadFile, name: str) -> AnalysisResult:
    """Analiza el archivo subido leyéndolo como UTF-8 en streaming (con o sin marca BOM)."""
    upload.file.seek(0)
    text = io.TextIOWrapper(upload.file, encoding="utf-8-sig", newline="")
    try:
        return analyze_csv(text, name)
    finally:
        text.detach()  # no cerrar el archivo temporal: lo cierra `upload.close()`


@router.post(
    "/analyze",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary", "description": "The incidents CSV file (UTF-8, comma separated)."}},
                    }
                }
            },
        }
    },
)
async def analyze_incident_file(request: Request, current_user: User = Depends(get_current_user)) -> Response:
    """Valida y analiza un CSV de incidencias (campo multipart `file`) y devuelve el resumen en JSON.

    400 sin archivo o vacío, 413 demasiado grande, 415 si no es un .csv enviado como multipart,
    422 si el contenido no se puede analizar (columnas que faltan, no es UTF-8, no es un CSV).
    """
    limit = max_upload_bytes()
    if not (request.headers.get("content-type") or "").lower().startswith("multipart/form-data"):
        return error_response(415, "unsupported_media_type", "Send the file as multipart/form-data in a field named 'file'.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + MULTIPART_OVERHEAD_BYTES:
        return error_response(413, "file_too_large", f"The file is larger than the {limit // (1024 * 1024)} MB limit.")

    form = await request.form(max_files=1, max_fields=5)
    upload = form.get("file")
    try:
        if not isinstance(upload, UploadFile):
            return error_response(400, "file_missing", "No file received. Send the CSV in a form field named 'file'.")
        name = _safe_name(upload.filename)
        if not name.lower().endswith(".csv"):
            return error_response(415, "unsupported_file_type", "Only .csv files can be analysed.")
        if upload.size == 0:
            return error_response(400, "empty_file", "The file is empty.")
        if upload.size is not None and upload.size > limit:
            return error_response(413, "file_too_large", f"The file is larger than the {limit // (1024 * 1024)} MB limit.")
        try:
            result = await run_in_threadpool(_analyze_stream, upload, name)
        except InvalidFileError as error:
            return error_response(422, "invalid_csv", str(error))
    finally:
        await form.close()  # borra el archivo temporal con los datos de clientes

    remember_result(current_user.id, result)
    logger.info("Incident file analysed: %s records, %s invalid", result.total_records, result.invalid_records)
    return JSONResponse(result.to_dict())


@router.get("/results/export")
def export_last_results(current_user: User = Depends(get_current_user)) -> Response:
    """Descarga el último análisis del usuario como CSV (`metric,value`, una fila por métrica)."""
    result = last_result(current_user.id)
    if result is None:
        return error_response(404, "no_analysis", "There is no analysis to export yet. Upload and analyse a file first.")
    return Response(
        content=results_csv_text(result),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{EXPORT_FILENAME}"', "Cache-Control": "no-store"},
    )
