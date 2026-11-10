# Ubuntu 24.04: Python 3.12, FFmpeg 6.1 with libass (animated subtitles) and the Inter font.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ENGINE_DATA_DIR=/data ENGINE_ENV=production PATH=/opt/venv/bin:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg fonts-inter fonts-dejavu-core curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/venv

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY engine ./engine
COPY migrations ./migrations
COPY alembic.ini .

RUN useradd --create-home --uid 10001 engine && mkdir -p /data && chown engine /data
USER engine

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["sh", "-c", "alembic upgrade head && uvicorn engine.api.app:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
