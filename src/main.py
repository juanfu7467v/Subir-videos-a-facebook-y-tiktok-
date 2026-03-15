import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

from src.media_downloader.downloader import download_video, cleanup_video
from src.publishers.facebook_api import publish_to_facebook
from src.publishers.tiktok_api import publish_to_tiktok

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("web_server")


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Video Distributor starting up…")
    yield
    logger.info("🛑 Video Distributor shutting down…")


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Video Distributor",
    description="Descarga un video de YouTube y lo publica en Facebook y TikTok.",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Request schema ─────────────────────────────────────────────────────────────
class VideoRequest(BaseModel):
    youtube_url: HttpUrl
    title: str
    description: str
    category: str = "Entertainment"


# ── Job tracker (en memoria; suficiente para Fly.io single-instance) ───────────
jobs: dict[str, dict] = {}


# ── Background task ────────────────────────────────────────────────────────────
async def process_video(job_id: str, payload: VideoRequest):
    jobs[job_id]["status"] = "downloading"
    video_path: str | None = None

    try:
        # ── Paso C: Descarga ────────────────────────────────────────────────
        logger.info("[%s] Descargando video: %s", job_id, payload.youtube_url)
        video_path = await download_video(str(payload.youtube_url), job_id)
        logger.info("[%s] Video descargado en: %s", job_id, video_path)

        jobs[job_id]["status"] = "publishing"

        # ── Paso D: Publicación en paralelo ─────────────────────────────────
        fb_task = publish_to_facebook(
            video_path=video_path,
            title=payload.title,
            description=payload.description,
        )
        tt_task = publish_to_tiktok(
            video_path=video_path,
            title=payload.title,
            description=payload.description,
        )

        results = await asyncio.gather(fb_task, tt_task, return_exceptions=True)

        fb_result, tt_result = results

        # Registrar resultados individuales
        jobs[job_id]["facebook"] = (
            {"success": True, "data": fb_result}
            if not isinstance(fb_result, Exception)
            else {"success": False, "error": str(fb_result)}
        )
        jobs[job_id]["tiktok"] = (
            {"success": True, "data": tt_result}
            if not isinstance(tt_result, Exception)
            else {"success": False, "error": str(tt_result)}
        )

        jobs[job_id]["status"] = "completed"
        logger.info("[%s] Proceso completado.", job_id)

    except Exception as exc:
        logger.exception("[%s] Error inesperado: %s", job_id, exc)
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(exc)

    finally:
        # ── Limpieza del archivo temporal ───────────────────────────────────
        if video_path:
            await cleanup_video(video_path)


# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.post("/upload-to-social", status_code=202)
async def upload_to_social(payload: VideoRequest, background_tasks: BackgroundTasks):
    """
    Recibe la información del video y lanza el proceso en segundo plano.
    Responde inmediatamente con 202 Accepted + job_id para seguimiento.
    """
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "queued", "job_id": job_id}

    # Lanzar tarea en segundo plano (no bloquea la respuesta)
    background_tasks.add_task(process_video, job_id, payload)

    logger.info("[%s] Job encolado para: %s", job_id, payload.youtube_url)
    return {"message": "Accepted – procesando en segundo plano.", "job_id": job_id}


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    """Consulta el estado de un job por su ID."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job no encontrado.")
    return jobs[job_id]


@app.get("/health")
async def health():
    """Health-check para Fly.io."""
    return {"status": "ok"}


# ── Entry-point local ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.web_server.main:app", host="0.0.0.0", port=8080, reload=True)
