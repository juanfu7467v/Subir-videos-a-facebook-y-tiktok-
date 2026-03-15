# 🎬 Video Distributor

Servidor que recibe un enlace de YouTube, descarga el video automáticamente y lo publica en **Facebook** y **TikTok** de forma simultánea.

---

## 📐 Arquitectura

```
POST /upload-to-social
        │
        ▼
┌──────────────────┐
│   Web Server     │  FastAPI – responde 202 Accepted inmediatamente
│  (src/web_server)│
└────────┬─────────┘
         │ Background Task
         ▼
┌──────────────────┐
│ Media Downloader │  yt-dlp descarga el video desde YouTube
│(src/media_downloader)│
└────────┬─────────┘
         │ video.mp4 en /tmp
         ▼
┌─────────────────────────────────┐
│         Publishers              │
│  ┌───────────────┐  ┌─────────┐ │
│  │ facebook_api  │  │tiktok_  │ │  ← Subida paralela (asyncio.gather)
│  │     .py       │  │api.py   │ │
│  └───────────────┘  └─────────┘ │
└─────────────────────────────────┘
```

---

## 📦 Estructura del proyecto

```
video-distributor/
├── src/
│   ├── web_server/
│   │   └── main.py              # FastAPI app + endpoints
│   ├── media_downloader/
│   │   └── downloader.py        # Descarga con yt-dlp
│   └── publishers/
│       ├── facebook_api.py      # Facebook Graph API (subida resumible)
│       └── tiktok_api.py        # TikTok Content Posting API v2
├── Dockerfile
├── fly.toml
├── requirements.txt
├── .env.example
└── README.md
```

---

## 🚀 Despliegue en Fly.io

### Prerrequisitos

```bash
# Instalar flyctl
curl -L https://fly.io/install.sh | sh

# Iniciar sesión
fly auth login
```

### Paso 1 – Crear la aplicación

```bash
cd video-distributor

# Crear la app (NO despliega aún, solo registra el nombre)
fly launch --no-deploy
```

> Cuando te pregunte por el nombre, usa `video-distributor` o el nombre que prefieras.  
> Edita `fly.toml` y asegúrate de que `app = "tu-nombre-de-app"` coincide.

### Paso 2 – Configurar los secretos

```bash
fly secrets set FB_ACCESS_TOKEN="tu_token_de_facebook"
fly secrets set FB_PAGE_ID="tu_id_de_pagina"
fly secrets set TIKTOK_ACCESS_TOKEN="tu_token_de_tiktok"
fly secrets set TIKTOK_APP_ID="tu_app_id_de_tiktok"
```

### Paso 3 – Desplegar

```bash
fly deploy
```

### Paso 4 – Verificar que funciona

```bash
# Health check
curl https://tu-app.fly.dev/health

# Debe responder:
# {"status":"ok"}
```

---

## 🔑 Variables de entorno

| Variable | Descripción | Requerida |
|---|---|---|
| `FB_ACCESS_TOKEN` | Page Access Token de Facebook (permiso `publish_video`) | ✅ |
| `FB_PAGE_ID` | ID numérico de la página de Facebook | ⚠️ Recomendado |
| `TIKTOK_ACCESS_TOKEN` | Access Token OAuth 2.0 de TikTok | ✅ |
| `TIKTOK_APP_ID` | Client Key de la app en TikTok for Developers | ✅ |
| `TMP_DIR` | Ruta para archivos temporales | No (default: `/tmp/video_distributor`) |
| `PORT` | Puerto del servidor | No (default: `8080`) |

---

## 📡 API

### `POST /upload-to-social`

Inicia el proceso de descarga y publicación.

**Body (JSON):**

```json
{
  "youtube_url": "https://youtu.be/L_XFNINxhj8",
  "title": "Misterios del océano profundo",
  "description": "Una descripción increíble sobre el abismo...",
  "category": "Science"
}
```

**Respuesta (202 Accepted):**

```json
{
  "message": "Accepted – procesando en segundo plano.",
  "job_id": "a3f7e9c1-4b2d-4e8a-8f1b-123456789abc"
}
```

---

### `GET /status/{job_id}`

Consulta el estado de un job.

**Estados posibles:** `queued` → `downloading` → `publishing` → `completed` / `failed`

**Ejemplo de respuesta completada:**

```json
{
  "job_id": "a3f7e9c1-...",
  "status": "completed",
  "facebook": {
    "success": true,
    "data": { "id": "123456789" }
  },
  "tiktok": {
    "success": true,
    "data": {
      "publish_id": "v_pub_url~...",
      "status": "PUBLISH_COMPLETE"
    }
  }
}
```

---

### `GET /health`

Health check para Fly.io.

```json
{"status": "ok"}
```

---

## 💻 Desarrollo local

```bash
# 1. Clonar y entrar al proyecto
cd video-distributor

# 2. Crear entorno virtual
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp .env.example .env
# Editar .env con tus tokens reales

# 5. Cargar .env y arrancar el servidor
export $(cat .env | xargs)
python -m src.web_server.main
```

El servidor estará disponible en `http://localhost:8080`.

---

## 🔐 Cómo obtener los tokens

### Facebook Page Access Token

1. Ve a [Meta for Developers](https://developers.facebook.com/)
2. Crea una app de tipo **Business**
3. Agrega el producto **Facebook Login**
4. En Graph API Explorer, selecciona tu página y solicita el permiso `publish_video`
5. Genera un **Page Access Token** de larga duración

### TikTok Access Token

1. Ve a [TikTok for Developers](https://developers.tiktok.com/)
2. Crea una app y activa el producto **Content Posting API**
3. Solicita los permisos: `video.publish`, `video.upload`
4. Implementa el flujo OAuth 2.0 para obtener el `access_token`

---

## ⚠️ Notas importantes

- Los videos se guardan **temporalmente** en `/tmp` y se eliminan automáticamente tras la publicación.
- El servidor responde `202 Accepted` de forma inmediata; el proceso real ocurre en segundo plano.
- En Fly.io con una sola instancia, el estado de los jobs se guarda en memoria. Si la máquina se reinicia, los jobs en curso se pierden.
- Para producción con alta disponibilidad, considera agregar Redis para persistir el estado de los jobs.
