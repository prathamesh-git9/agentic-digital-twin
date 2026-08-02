FROM python:3.13-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY data ./data
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

FROM python:3.13-slim AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TWIN_PROFILE_PATH=/app/data/profile.yaml \
    TWIN_DATABASE_URL=sqlite:////app/state/digital-twin.db

RUN addgroup --system twin \
    && adduser --system --ingroup twin --home /app twin \
    && mkdir -p /app/state \
    && chown -R twin:twin /app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=twin:twin data ./data

USER twin
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" || exit 1

CMD ["uvicorn", "digital_twin.main:app", "--host", "0.0.0.0", "--port", "8000"]

