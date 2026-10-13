# Publication Bank web app (Linux). Build:  docker build -t publication-bank .
# Everything an installation keeps (organization settings, uploaded files) lives in /data: mount a volume there.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    PUBBANK_HOME=/data \
    MALLOC_ARENA_MAX=2 \
    OPENBLAS_NUM_THREADS=2

WORKDIR /app

COPY requirements-web.txt .
RUN pip install -r requirements-web.txt

# the search model is part of the image, so the app never downloads anything when it starts
COPY scripts/fetch_model.py scripts/fetch_model.py
RUN python scripts/fetch_model.py

COPY . .
RUN mkdir -p /data

# (No VOLUME line: Railway does not allow it. On Railway, attach a volume at /data in its dashboard; docker compose mounts one itself.)
EXPOSE 8000

# One worker only: background jobs are kept in this process's memory. The host sets PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
