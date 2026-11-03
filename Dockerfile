# The hosted service: drop page and MCP connector (scoreproof serve).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[hosted]" && useradd --create-home scoreproof
USER scoreproof
# Load music21 once at build time so its first request is not slower.
RUN python -c "import music21, verovio, typst; from scoreproof import hosted"

CMD ["sh", "-c", "exec scoreproof serve --host 0.0.0.0 --port ${PORT:-8080}"]
