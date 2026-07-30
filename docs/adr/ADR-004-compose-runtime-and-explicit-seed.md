# ADR-004: Compose Runtime and Explicit Offline Seed

## Status

Accepted

## Context

The completed RCA MVP has separate PostgreSQL, FastAPI, and React processes.
Manual startup works, but it does not provide a reproducible service topology.
At the same time, Synthetic Fab generation and database reset must not become
FastAPI startup behavior.

## Decision

Use Docker Compose for the local industrial prototype with three default
runtime services:

- PostgreSQL
- FastAPI Backend
- Nginx-hosted React Frontend

Provide database initialization as an explicitly invoked `seed` service in a
non-default `tools` profile. The seed image may contain migrations and existing
offline seed files. The Backend runtime image must not contain the generator or
seed importer.

Compose startup waits for service health but does not generate, migrate, reset,
or seed application data.

PostgreSQL is reachable only on the private Compose network; it is not
published to a host port. Host-side database tooling may use the existing local
PostgreSQL installation or `docker compose exec db psql` when container data
must be inspected.

## Consequences

- Local startup and service networking become reproducible.
- Data reset remains visible and deliberate.
- The same Backend Python workflow is used inside and outside containers.
- Nginx provides one browser origin and proxies `/api` to FastAPI.
- This decision does not provide production orchestration, authentication,
  secret management, or observability.
