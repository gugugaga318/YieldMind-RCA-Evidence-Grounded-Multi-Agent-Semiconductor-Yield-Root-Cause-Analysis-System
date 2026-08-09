from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
BACKEND_DOCKERFILE = ROOT / "docker" / "backend.Dockerfile"
FRONTEND_DOCKERFILE = ROOT / "docker" / "frontend.Dockerfile"
NGINX_CONFIG = ROOT / "docker" / "nginx.conf"


class ComposeContractTest(unittest.TestCase):
    def test_runtime_and_seed_services_are_separated(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")

        self.assertIn("  db:", compose)
        self.assertIn("  backend:", compose)
        self.assertIn("  frontend:", compose)
        self.assertIn("  seed:", compose)
        self.assertIn("  knowledge-index:", compose)
        self.assertIn("profiles: [tools]", compose)
        self.assertIn("target: ${YIELD_RCA_BACKEND_TARGET:-runtime}", compose)
        self.assertIn("target: seed", compose)
        self.assertIn("target: knowledge-index", compose)
        self.assertIn("image: pgvector/pgvector:pg16", compose)

    def test_runtime_startup_does_not_generate_or_seed_data(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8").lower()
        runtime_section, seed_section = compose.split("  seed:", maxsplit=1)

        self.assertNotIn("generate_synthetic", runtime_section)
        self.assertNotIn("seed_database.py", runtime_section)
        self.assertNotIn("index_knowledge_embeddings.py", runtime_section)
        self.assertNotIn("--reset-schema", runtime_section)
        self.assertIn("--reset-schema", seed_section)

    def test_postgres_is_not_published_on_a_host_port(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        db_section, backend_section = compose.split("  backend:", maxsplit=1)

        self.assertNotIn("ports:", db_section)
        self.assertIn("@db:5432/", backend_section)

    def test_backend_runtime_image_excludes_seed_assets(self) -> None:
        dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
        runtime_section, seed_section = dockerfile.split("FROM base AS seed", maxsplit=1)

        self.assertIn("FROM base AS runtime", runtime_section)
        self.assertNotIn("seed_database.py", runtime_section)
        self.assertNotIn("data/seeds", runtime_section)
        self.assertNotIn("generate_synthetic", dockerfile)
        self.assertIn("seed_database.py", seed_section)
        self.assertIn("data/seeds", seed_section)

    def test_frontend_is_static_and_proxies_api_to_backend(self) -> None:
        frontend = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")
        nginx = NGINX_CONFIG.read_text(encoding="utf-8")

        self.assertIn("pnpm run build", frontend)
        self.assertIn("FROM nginx:", frontend)
        self.assertIn("location /api/", nginx)
        self.assertIn("proxy_pass http://backend:8000/", nginx)


if __name__ == "__main__":
    unittest.main()
