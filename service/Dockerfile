# rmzotero: the always-on sync service (ADR 0004). Debian trixie for pandoc >= 3.1 (Typst output).
FROM python:3.13-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      pandoc ocrmypdf tesseract-ocr tesseract-ocr-eng ghostscript \
      fonts-noto-core fonts-noto-ui-core fonts-noto-mono fonts-ebgaramond fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN mkdir -p /data /config
# Starts as root, then drops to the owner of /data (or PUID/PGID): see entrypoint.sh.
VOLUME ["/data"]
ENV RMZOTERO_CONFIG=/config/rmzotero.toml \
    RMZOTERO_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
EXPOSE 8787 8788
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=4)" || exit 1
# last, so a new commit doesn't invalidate the cached layers above
ARG REVISION=""
ENV RMZOTERO_REVISION=$REVISION
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["run"]
