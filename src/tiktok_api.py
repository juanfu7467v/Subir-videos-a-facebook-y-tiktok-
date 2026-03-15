"""
publishers/tiktok_api.py
─────────────────────────
Publica un video MP4 en TikTok usando la
TikTok Content Posting API v2 (Direct Post).

Variables de entorno necesarias:
  TIKTOK_ACCESS_TOKEN  – Access Token OAuth 2.0 del usuario
  TIKTOK_APP_ID        – Client Key de la aplicación TikTok for Developers
"""

import asyncio
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger("publisher.tiktok")

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"

# Tamaño mínimo/máximo de chunk según la API (entre 5 MB y 64 MB)
MIN_CHUNK_SIZE = 5 * 1024 * 1024   # 5 MB
MAX_CHUNK_SIZE = 64 * 1024 * 1024  # 64 MB
CHUNK_SIZE = 10 * 1024 * 1024      # 10 MB (valor conservador)

# Tamaño máximo de video permitido por TikTok (4 GB)
MAX_VIDEO_SIZE = 4 * 1024 * 1024 * 1024


async def publish_to_tiktok(
    video_path: str,
    title: str,
    description: str,
) -> dict:
    """
    Publica el video en TikTok mediante el flujo de subida por chunks:

    1. Inicializa la subida (POST /post/publish/video/init/).
    2. Sube los chunks al upload_url devuelto.
    3. Verifica el estado de publicación.

    Retorna el dict con publish_id y status.
    """
    access_token = _require_env("TIKTOK_ACCESS_TOKEN")

    video_file = Path(video_path)
    file_size = video_file.stat().st_size

    if file_size > MAX_VIDEO_SIZE:
        raise ValueError(
            f"El archivo ({file_size / 1e9:.2f} GB) supera el límite de TikTok (4 GB)."
        )

    chunk_count = max(1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE)

    logger.info(
        "Iniciando subida a TikTok | tamaño: %.2f MB | chunks: %d",
        file_size / (1024 * 1024),
        chunk_count,
    )

    async with httpx.AsyncClient(timeout=300) as client:
        # ── Paso 1: Inicializar subida ────────────────────────────────────────
        publish_id, upload_url = await _init_upload(
            client, access_token, title, description, file_size, chunk_count
        )
        logger.info("Subida inicializada | publish_id: %s", publish_id)

        # ── Paso 2: Subir chunks ──────────────────────────────────────────────
        await _upload_chunks(client, upload_url, video_path, file_size)
        logger.info("Todos los chunks subidos correctamente.")

        # ── Paso 3: Verificar estado ──────────────────────────────────────────
        status = await _poll_publish_status(client, access_token, publish_id)

    logger.info("Video publicado en TikTok | status: %s", status)
    return {"publish_id": publish_id, "status": status}


# ── Pasos internos ─────────────────────────────────────────────────────────────

async def _init_upload(
    client: httpx.AsyncClient,
    access_token: str,
    title: str,
    description: str,
    file_size: int,
    chunk_count: int,
) -> tuple[str, str]:
    """Llama al endpoint de inicialización y obtiene publish_id + upload_url."""
    url = f"{TIKTOK_API_BASE}/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    # Combinar título y descripción en el caption (máx 2200 chars)
    caption = f"{title}\n\n{description}"[:2200]

    payload = {
        "post_info": {
            "title": title[:150],          # TikTok limita el título a 150 chars
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": CHUNK_SIZE,
            "total_chunk_count": chunk_count,
        },
    }

    resp = await client.post(url, headers=headers, json=payload)
    _raise_for_tiktok_error(resp, "init")

    body = resp.json()
    data = body.get("data", {})
    return data["publish_id"], data["upload_url"]


async def _upload_chunks(
    client: httpx.AsyncClient,
    upload_url: str,
    video_path: str,
    file_size: int,
) -> None:
    """Sube el video en chunks usando el upload_url de TikTok."""
    with open(video_path, "rb") as fh:
        chunk_index = 0
        offset = 0

        while offset < file_size:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break

            chunk_end = offset + len(chunk) - 1
            content_range = f"bytes {offset}-{chunk_end}/{file_size}"

            headers = {
                "Content-Range": content_range,
                "Content-Type": "video/mp4",
                "Content-Length": str(len(chunk)),
            }

            resp = await client.put(upload_url, content=chunk, headers=headers)

            # 206 Partial Content = chunk aceptado; 200/201 = último chunk
            if resp.status_code not in (200, 201, 206):
                raise RuntimeError(
                    f"[TikTok/upload] Chunk {chunk_index} rechazado "
                    f"(HTTP {resp.status_code}): {resp.text}"
                )

            logger.debug(
                "Chunk %d subido | rango: %s", chunk_index, content_range
            )
            offset += len(chunk)
            chunk_index += 1


async def _poll_publish_status(
    client: httpx.AsyncClient,
    access_token: str,
    publish_id: str,
    max_retries: int = 20,
    wait_seconds: int = 10,
) -> str:
    """
    Consulta el estado de publicación hasta que esté completo o falle.
    Retorna el status final ('PUBLISH_COMPLETE' o similar).
    """
    url = f"{TIKTOK_API_BASE}/post/publish/status/fetch/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    payload = {"publish_id": publish_id}

    for attempt in range(1, max_retries + 1):
        resp = await client.post(url, headers=headers, json=payload)
        _raise_for_tiktok_error(resp, "status")

        body = resp.json()
        status = body.get("data", {}).get("status", "UNKNOWN")
        logger.info(
            "Estado de publicación TikTok (%d/%d): %s",
            attempt, max_retries, status
        )

        if status in ("PUBLISH_COMPLETE", "PUBLISH_FAILED"):
            if status == "PUBLISH_FAILED":
                fail_reason = body.get("data", {}).get("fail_reason", "desconocido")
                raise RuntimeError(
                    f"[TikTok] Publicación fallida: {fail_reason}"
                )
            return status

        await asyncio.sleep(wait_seconds)

    raise TimeoutError(
        f"[TikTok] La publicación no completó en {max_retries * wait_seconds}s."
    )


# ── Utilidades ─────────────────────────────────────────────────────────────────

def _raise_for_tiktok_error(response: httpx.Response, phase: str) -> None:
    """Lanza RuntimeError si la respuesta contiene un error de TikTok."""
    try:
        body = response.json()
    except Exception:
        response.raise_for_status()
        return

    error = body.get("error", {})
    if error.get("code", "ok") != "ok":
        raise RuntimeError(
            f"[TikTok/{phase}] {error.get('code','?')}: "
            f"{error.get('message','unknown error')} "
            f"(log_id: {error.get('log_id','-')})"
        )
    response.raise_for_status()


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Variable de entorno requerida no configurada: {key}"
        )
    return value
