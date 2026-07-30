FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY --chown=app:app pyproject.toml README.md alembic.ini ./
COPY --chown=app:app src ./src
COPY --chown=app:app alembic ./alembic
COPY --chown=app:app scripts ./scripts

RUN python -m pip install --upgrade pip && python -m pip install .

FROM base AS production
USER app

EXPOSE 8001

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]

FROM base AS test
RUN python -m pip install ".[dev]"
COPY --chown=app:app tests ./tests
USER app
CMD ["pytest"]
