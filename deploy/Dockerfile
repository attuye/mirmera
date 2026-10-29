# Mirmera for a server: two images from one file.
#   target "app": the backend, with Chromium and the libraries it needs (Playwright's image)
#   target "web": Caddy with the built frontend; it serves the pages, proxies /api to the
#                 backend and gets the HTTPS certificate (Let's Encrypt) by itself
# Built by deploy/docker-compose.yml; see deploy/README.md.

# ---------- the frontend, built once ----------
FROM node:20-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# The browser calls the API on the same address, under /api (Caddy strips the prefix).
ENV VITE_API_URL=/api
RUN npm run build

# ---------- the backend ----------
# The tag must match playwright== in backend/requirements.txt, so the browsers fit the library.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --break-system-packages -r requirements.txt
COPY backend/app ./app
# Data (database, evidence, secret.key) lives in the /data volume, never in the image.
RUN mkdir -p /data && useradd --create-home --uid 10001 mirmera && chown mirmera /data
USER mirmera
EXPOSE 8000
# One worker: background jobs and running browsers are tracked in this process.
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1", "--timeout-graceful-shutdown", "20"]

# ---------- the web server ----------
FROM caddy:2.10-alpine AS web
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=frontend /web/dist /srv/web
