# PasStar: one image that serves the screens, the API and /mcp from one address.
# Build:  fly deploy   (the build runs on Fly's builders; the local folder is the build context, so the
#                       catalogue backend/data/products.sqlite goes in although it is not in git)

# ── 1. The screens ──
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# The in-browser mock imports the shared fixtures from the repository root (../fixtures from frontend/).
COPY fixtures/ /fixtures/
# Same origin as the API, and never fall back to the in-browser mock.
RUN printf 'VITE_API_BASE=\nVITE_API_MODE=live\n' > .env.production.local && npm run build

# ── 2. The backend, serving the built screens ──
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY plugin/passtar/skills plugin/passtar/skills
COPY --from=web /web/dist frontend/dist
WORKDIR /app/backend
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
