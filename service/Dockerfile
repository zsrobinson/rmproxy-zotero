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

RUN useradd --system --uid 1000 --home /data rmzotero && mkdir -p /data /config && chown rmzotero /data
USER rmzotero
VOLUME ["/data"]
ENV RMZOTERO_CONFIG=/config/rmzotero.toml \
    RMZOTERO_DATA_DIR=/data \
    PYTHONUNBUFFERED=1
EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=4)" || exit 1
ENTRYPOINT ["rmzotero"]
CMD ["run"]
