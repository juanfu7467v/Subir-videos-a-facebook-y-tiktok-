"""
media_downloader/downloader.py
──────────────────────────────
Descarga un video de YouTube usando yt-dlp y lo almacena
en /tmp/<job_id>/<job_id>.mp4  (memoria efímera del servidor).
"""

import asyncio
import logging
import os
import shutil

import yt_dlp

logger = logging.getLogger("media_downloader")

# Directorio base para archivos temporales
TMP_BASE = os.getenv("TMP_DIR", "/tmp/video_distributor")


async def download_video(youtube_url: str, job_id: str) -> str:
    """
    Descarga el video indicado por `youtube_url`.

    Retorna la ruta absoluta del archivo MP4 descargado.
    Lanza una excepción si la descarga falla.
    """
    # Crear directorio temporal único para este job
    job_dir = os.path.join(TMP_BASE, job_id)
    os.makedirs(job_dir, exist_ok=True)

    output_template = os.path.join(job_dir, f"{job_id}.%(ext)s")

    ydl_opts = {
        # Mejor calidad combinada de vídeo+audio hasta 1080p
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "outtmpl": output_template,
        # Post-proceso: fusionar en un único MP4
        "merge_output_format": "mp4",
        # Silenciar la salida estándar de yt-dlp (usamos nuestro logger)
        "quiet": True,
        "no_warnings": False,
        "logger": _YdlLogger(),
        # Respetar límites de velocidad para evitar bloqueos
        "ratelimit": 2_000_000,  # 2 MB/s
        # Reintentos ante errores de red
        "retries": 5,
        "fragment_retries": 5,
        # No descargar listas de reproducción completas
        "noplaylist": True,
    }

    # Ejecutar yt-dlp en un hilo separado para no bloquear el event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_ydl, ydl_opts, youtube_url, job_id)

    # Buscar el archivo descargado
    video_path = _find_mp4(job_dir, job_id)
    if not video_path:
        raise FileNotFoundError(
            f"[{job_id}] No se encontró el archivo MP4 tras la descarga en {job_dir}"
        )

    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    logger.info("[%s] Archivo listo: %s (%.2f MB)", job_id, video_path, size_mb)
    return video_path


def _run_ydl(ydl_opts: dict, url: str, job_id: str) -> None:
    """Wrapper sincrónico que lanza yt_dlp.YoutubeDL."""
    logger.info("[%s] Iniciando descarga con yt-dlp: %s", job_id, url)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ret = ydl.download([url])
    if ret != 0:
        raise RuntimeError(f"[{job_id}] yt-dlp terminó con código de error: {ret}")


def _find_mp4(directory: str, job_id: str) -> str | None:
    """Busca el archivo MP4 generado dentro del directorio del job."""
    for fname in os.listdir(directory):
        if fname.startswith(job_id) and fname.endswith(".mp4"):
            return os.path.join(directory, fname)
    return None


async def cleanup_video(video_path: str) -> None:
    """Elimina el directorio temporal del job de forma asíncrona."""
    job_dir = os.path.dirname(video_path)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, shutil.rmtree, job_dir)
        logger.info("Directorio temporal eliminado: %s", job_dir)
    except Exception as exc:
        logger.warning("No se pudo eliminar %s: %s", job_dir, exc)


# ── Logger bridge para yt-dlp ──────────────────────────────────────────────────
class _YdlLogger:
    """Redirige los mensajes de yt-dlp al logger estándar de Python."""

    def debug(self, msg: str) -> None:
        # yt-dlp usa debug para progreso; lo filtramos para no saturar los logs
        if msg.startswith("[download]"):
            logger.debug(msg)

    def info(self, msg: str) -> None:
        logger.info(msg)

    def warning(self, msg: str) -> None:
        logger.warning(msg)

    def error(self, msg: str) -> None:
        logger.error(msg)
