from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from investor_digest.config import Settings
from investor_digest.llm_client import LocalOpenAIClient
from investor_digest.pipeline import analyze_path, prepare_path
from investor_digest.schemas import AnalyzePathRequest


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Investor Digest API", version="0.1.0")
    runtime_settings = settings or Settings.from_env()
    static_dir = Path(__file__).resolve().parent / "static"

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=FileResponse)
    def home() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/report", response_class=FileResponse)
    def report() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        client = LocalOpenAIClient(runtime_settings)
        return {"status": "ok", "model": client.resolve_model_name()}

    @app.post("/api/prepare/path")
    def prepare_from_path(request: AnalyzePathRequest) -> dict[str, object]:
        try:
            prepared = prepare_path(request.path, settings=runtime_settings)
        except Exception as exc:  # pragma: no cover - API glue
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "company_name": prepared.document.company_name,
            "reporting_period": prepared.document.reporting_period,
            "selected_file": str(prepared.document.selected_file),
            "warnings": prepared.warnings,
            "context_preview": prepared.context[:5000],
        }

    @app.post("/api/analyze/path")
    def analyze_from_path(request: AnalyzePathRequest) -> dict[str, object]:
        try:
            digest = analyze_path(
                request.path,
                settings=runtime_settings,
                audience=request.audience,
                language=request.language,
            )
        except Exception as exc:  # pragma: no cover - API glue
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return digest.model_dump()

    @app.post("/api/analyze/file")
    async def analyze_from_file(
        file: UploadFile = File(...),
        audience: str | None = Form(default=None),
        language: str | None = Form(default=None),
    ) -> dict[str, object]:
        suffix = Path(file.filename or "upload.txt").suffix or ".txt"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            temp_path = Path(tmp.name)

        try:
            digest = analyze_path(
                str(temp_path),
                settings=runtime_settings,
                audience=audience,
                language=language,
            )
        except Exception as exc:  # pragma: no cover - API glue
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            temp_path.unlink(missing_ok=True)

        return digest.model_dump()

    return app


app = create_app()
