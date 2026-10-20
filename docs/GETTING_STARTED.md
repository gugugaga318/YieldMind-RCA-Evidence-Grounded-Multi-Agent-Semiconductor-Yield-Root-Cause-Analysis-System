# Running Copilot v1 locally

## Offline workflow

Use Python 3.11+ and install from the repository root:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux / macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/run_golden_rca.py --no-print-report
```

This uses existing synthetic CSV seeds and deterministic execution. It does not call a paid model or reset a database.

## Local Web / API with Docker Compose

Create your own local `.env`, using the setting names in [configuration.md](configuration.md). Set `POSTGRES_PASSWORD` yourself; no environment file is distributed.

For a **new disposable demo database**:

```bash
docker compose up -d db
docker compose --profile tools run --rm --build seed
docker compose up --build -d backend worker frontend
```

The explicit seed service uses `--reset-schema`, which deletes and rebuilds the demo schema. Do not use it against data you need to retain. Normal API / worker startup does not migrate, seed or generate data.

Dashboard: `http://127.0.0.1:5173`. API documentation: `http://127.0.0.1:8000/docs`. The supplied PostgreSQL service is available on the Compose network and is not exposed on a host port.

Defaults are `deterministic` and `fixed`. For a no-network LLM-control-path demonstration, set the following locally and recreate backend and worker containers:

```dotenv
YIELD_RCA_AGENT_MODE=fake
YIELD_RCA_ORCHESTRATION_MODE=llm_react
```

## Real models

Real requests incur charges. Explicitly enable `llm` and `llm_react` and provide credentials in local process environments.

- Qwen: `DASHSCOPE_API_KEY`, provider `dashscope`, model `qwen-plus`.
- DeepSeek: `DEEPSEEK_API_KEY`, provider `deepseek`, model `deepseek-v4-pro`.

Web / API model selection controls the job configuration. A matching credential must be available to the actual worker. The frozen Compose configuration forwards only DashScope credentials. For DeepSeek, run API and worker as host processes with the key available to both; do not assume Compose forwards it.

For host execution, set `YIELD_RCA_DATABASE_URL` to an initialized, reachable PostgreSQL database and start these in separate terminals:

```bash
python -m uvicorn yield_rca_api.app:app --app-dir backend --host 127.0.0.1 --port 8000
```

```bash
python scripts/run_rca_worker.py
```

The shipped Compose database has no host port, so a host API must use a separately reachable database. For frontend development:

```bash
cd frontend
pnpm install
pnpm dev
```

The Vite server proxies `/api` to the backend.

## Tests and local outputs

```bash
python -m pytest tests -q
```

Paid-provider and PostgreSQL checks have separate opt-in conditions. A passed deterministic case is not real-model accuracy. Scripts may create local `outputs/`; environment files, runtime outputs, caches and private benchmark inputs are excluded from publication.
