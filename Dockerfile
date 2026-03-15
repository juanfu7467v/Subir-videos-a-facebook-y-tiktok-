# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – Build
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Dependencias del sistema necesarias para yt-dlp y ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python en un entorno virtual
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Usuario no-root por seguridad
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Copiar ffmpeg desde builder
COPY --from=builder /usr/bin/ffmpeg /usr/bin/ffmpeg
COPY --from=builder /usr/bin/ffprobe /usr/bin/ffprobe
COPY --from=builder /usr/lib/x86_64-linux-gnu/ /usr/lib/x86_64-linux-gnu/
COPY --from=builder /lib/x86_64-linux-gnu/ /lib/x86_64-linux-gnu/

# Copiar paquetes Python instalados
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copiar código fuente
COPY --chown=appuser:appuser . .

# Directorio temporal para los videos descargados
RUN mkdir -p /tmp/video_distributor && chown appuser:appuser /tmp/video_distributor

USER appuser

# Puerto de escucha
EXPOSE 8080

# Variables de entorno por defecto (se sobreescriben en Fly.io)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TMP_DIR=/tmp/video_distributor \
    PORT=8080

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Comando de arranque
CMD ["uvicorn", "src.web_server.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "1", \
     "--log-level", "info"]
