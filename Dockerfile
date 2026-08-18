# Phase 9 image. Runtime only: aiohttp from requirements.txt, not pytest/ruff.
# python:3.13-slim matches CI/README (the annex originally said 3.12; see Divergence).
FROM python:3.13-slim

WORKDIR /app

# Print logs immediately. Without this, a crash can sit in the buffer and look like a hang.
ENV PYTHONUNBUFFERED=1

# Deps first so a code-only change reuses this layer instead of re-pip-installing.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
# Whitelist only: same four files PUBLIC_FILES serves. Viewer/recordings stay out.
COPY client/index.html client/game.js client/render.js client/style.css client/

EXPOSE 8000
CMD ["python", "-m", "server.main"]
