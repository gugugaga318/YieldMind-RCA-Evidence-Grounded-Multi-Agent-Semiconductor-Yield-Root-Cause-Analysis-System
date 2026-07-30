FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md ./
COPY core ./core
COPY backend ./backend

RUN python -m pip install --no-cache-dir .

USER app


FROM base AS runtime

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "yield_rca_api.app:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS seed

COPY --chown=app:app scripts/seed_database.py ./scripts/seed_database.py
COPY --chown=app:app db ./db
COPY --chown=app:app data/seeds ./data/seeds

ENTRYPOINT ["python", "scripts/seed_database.py"]

