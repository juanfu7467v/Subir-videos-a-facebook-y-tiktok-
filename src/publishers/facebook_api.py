"""
publishers/facebook_api.py
───────────────────────────
Publica un video MP4 en una Página de Facebook usando
la Facebook Graph API (video upload resumible).

Variables de entorno necesarias:
  FB_ACCESS_TOKEN  – Page Access Token con permiso publish_video
  FB_PAGE_ID       – ID numérico de la página (opcional si está en el token)
"""

import asyncio
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger("publisher.facebook")

GRAPH_API_VERSION = "v19.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Tamaño de cada chunk en la subida resumible (10 MB)
CHUNK_SIZE = 10 * 1024 * 1024


async def publish_to_facebook(
    video_path: str,
    title: str,
    description: str,
) -> dict:
    """
    Sube el video a Facebook mediante el protocolo de subida resumible.

    1. Inicia la sesión de subida (start phase).
    2. Transfiere los chunks (transfer phase).
    3. Finaliza la subida (finish phase).

    Retorna el dict con la respuesta de la API.
    """
    access_token = _require_env("FB_ACCESS_TOKEN")
    page_id = os.getenv("FB_PAGE_ID", "me")

    video_file = Path(video_path)
    file_size = video_file.stat().st_size

    logger.info(
        "Iniciando subida a Facebook | tamaño: %.2f MB", file_size / (1024 * 1024)
    )

    async with httpx.AsyncClient(timeout=300) as client:
        # ── Fase 1: start ─────────────────────────────────────────────────────
        upload_session_id, start_offset = await _start_upload(
            client, page_id, access_token, file_size
        )
        logger.info("Sesión de subida iniciada: %s", upload_session_id)

        # ── Fase 2: transfer ──────────────────────────────────────────────────
        with open(video_path, "rb") as fh:
            end_offset = await _transfer_chunks(
                client, page_id, access_token, upload_session_id, fh, start_offset
            )

        logger.info("Transferencia completada hasta byte %s", end_offset)

        # ── Fase 3: finish ────────────────────────────────────────────────────
        result = await _finish_upload(
            client, page_id, access_token, upload_session_id, title, description
        )

    logger.info("Video publicado en Facebook: %s", result)
    return result


# ── Fases internas ─────────────────────────────────────────────────────────────

async def _start_upload(
    client: httpx.AsyncClient,
    page_id: str,
    access_token: str,
    file_size: int,
) -> tuple[str, int]:
    url = f"{GRAPH_BASE}/{page_id}/videos"
    data = {
        "upload_phase": "start",
        "file_size": str(file_size),
        "access_token": access_token,
    }
    resp = await client.post(url, data=data)
    _raise_for_graph_error(resp, "start")

    body = resp.json()
    return body["upload_session_id"], int(body["start_offset"])


async def _transfer_chunks(
    client: httpx.AsyncClient,
    page_id: str,
    access_token: str,
    upload_session_id: str,
    fh,
    start_offset: int,
) -> int:
    url = f"{GRAPH_BASE}/{page_id}/videos"
    current_offset = start_offset

    while True:
        fh.seek(current_offset)
        chunk = fh.read(CHUNK_SIZE)
        if not chunk:
            break

        files = {"video_file_chunk": ("chunk.mp4", chunk, "video/mp4")}
        data = {
            "upload_phase": "transfer",
            "upload_session_id": upload_session_id,
            "start_offset": str(current_offset),
            "access_token": access_token,
        }

        resp = await client.post(url, data=data, files=files)
        _raise_for_graph_error(resp, "transfer")

        body = resp.json()
        next_offset = int(body["start_offset"])

        logger.debug(
            "Chunk subido: %d → %d bytes", current_offset, next_offset
        )

        if next_offset == current_offset:
            # La API indicó que no avanzó; evitar bucle infinito
            break
        current_offset = next_offset

    return current_offset


async def _finish_upload(
    client: httpx.AsyncClient,
    page_id: str,
    access_token: str,
    upload_session_id: str,
    title: str,
    description: str,
) -> dict:
    url = f"{GRAPH_BASE}/{page_id}/videos"
    data = {
        "upload_phase": "finish",
        "upload_session_id": upload_session_id,
        "title": title,
        "description": description,
        "published": "true",
        "access_token": access_token,
    }
    resp = await client.post(url, data=data)
    _raise_for_graph_error(resp, "finish")
    return resp.json()


# ── Utilidades ─────────────────────────────────────────────────────────────────

def _raise_for_graph_error(response: httpx.Response, phase: str) -> None:
    """Lanza RuntimeError si la respuesta de Graph API contiene un error."""
    try:
        body = response.json()
    except Exception:
        response.raise_for_status()
        return

    if "error" in body:
        err = body["error"]
        raise RuntimeError(
            f"[Facebook/{phase}] {err.get('type','Error')} "
            f"({err.get('code','?')}): {err.get('message','unknown error')}"
        )
    response.raise_for_status()


def _require_env(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Variable de entorno requerida no configurada: {key}"
        )
    return value
