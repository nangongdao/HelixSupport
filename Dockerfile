FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/helix

RUN groupadd --system helix && useradd --system --gid helix --home /opt/helix helix

COPY pyproject.toml README.md requirements.lock ./
COPY app ./app
RUN python -m pip install --no-deps -r requirements.lock \
    && python -m pip install --no-deps . \
    && mkdir -p /var/lib/helix && chown -R helix:helix /opt/helix /var/lib/helix

USER helix
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
