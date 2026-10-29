# Cascade Guard server image.
#   docker build -t cascade-guard .
#   docker run -p 8000:8000 --env-file .env -v cascade-data:/data cascade-guard
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH \
    CASCADE_HOME=/data \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /src
COPY pyproject.toml README.md LICENSE constraints.txt ./
COPY cascade_guard ./cascade_guard
RUN python3 -m venv /opt/venv \
 && pip install -c constraints.txt ".[postgres]" \
 && rm -rf /src \
 && useradd --create-home --uid 10001 cascade \
 && mkdir -p /data && chown cascade /data

USER cascade
WORKDIR /data
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT','8000'), timeout=3)"

# Containers are reached through the network, so they listen on 0.0.0.0. Without API_KEY the
# server still only answers requests from inside the container: set API_KEY (see .env.example).
CMD ["cascade-guard", "serve"]
