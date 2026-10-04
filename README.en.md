# Genealogy Management System

Digitize scanned family trees (PDF/TIF) with a **multimodal vision LLM** (OCR + structured
person/relation extraction), human-reviewed, then written into a graph database — with visual
family trees, 3D lineage, RAG Q&A and no-login visitor sharing. Handles the hard cases of ancient
book scanning: bleed-through text, mixed vertical/horizontal layout, damaged characters, classical
Chinese and handwriting.

> Chinese documentation: [README.md](README.md)

## Features

- **AI scan import** — batch upload → per-page VLM recognition → human review (whole-book /
  per-page) → graph write; turns manual entry into proofreading
- **Lineage & persons** — hierarchical lineage/branch management; full CRUD for persons and
  parent/spouse relations
- **Visualization** — AntV G6 2D tree + 3D force-directed lineage, subtree expansion, search
- **RAG Q&A** — two-stage retrieval (embedding + rerank) over genealogy content, for both admins
  and visitors
- **Visitor sharing** — 6-char short links: *lineage* and *dashboard* shares; visitors need no
  account for 3D tree / person search / AI Q&A (toggleable per share)
- **Security** — HOTP dynamic-code login, forced first-login password change, module-level
  permissions, admin-approved deletion, full audit log

## Screenshots

| Login (dynamic code) | Dashboard |
| --- | --- |
| <img src="docs/screenshots/1-login.png" width="480"> | <img src="docs/screenshots/2-dashboard.png" width="480"> |

| Scan Import (AI Recognition) |
| --- |
| <img src="docs/screenshots/3-scan.png" width="480"> |

## Architecture

```
Browser ──https──▶ Nginx(:443) ──┬─ /       → frontend static (Vue 3)
                                 ├─ /api/   → backend API (:8000)
                                 └─ /files/ → MinIO (:9000)
Backend ──┬─▶ Neo4j (:7687)       person / relation graph
          ├─▶ PostgreSQL (:5432)  users / file metadata / tasks
          └─▶ MinIO (:9000)       scanned-file object storage
```

- Stack: FastAPI (Python 3.11) / Vue 3 + Vite + Ant Design Vue / Neo4j 5.26 / PostgreSQL 15 /
  MinIO / Nginx / Docker Compose
- Only 80/443 exposed (80 merely 301-redirects to HTTPS, no HTTP pages); other services stay
  inside the container network
- AI inference (VLM / embedding / reranker) is **not** part of this repo — independently deployed,
  OpenAI-compatible services configured in `.env`, upgradable or replaceable as a whole

## Quick Start

Prereqs: Docker + Compose, and reachable inference services (VLM / embedding / reranker).

```bash
bash scripts/build-frontend.sh   # build frontend (node container, no local Node needed)
bash scripts/gen-certs.sh        # self-signed HTTPS certs (distribute ca.crt to clients)
cp .env.example .env && vi .env  # change all default passwords + inference URLs
docker compose up -d --build
```

Open `https://<server-ip>` (default `admin / admin123`, **change the password after first login**).
After changing `.env`, use `docker compose up -d backend` (`restart` does not re-inject env vars);
more tuning options are documented in [.env.example](.env.example).

## Data & Migration

All data is bind-mounted under `./data/` (PG / Neo4j / MinIO original scans). Moving that
directory plus the images (`docker save` / `docker load`) migrates the whole system to another
machine offline.

## Operations

```bash
docker compose logs -f backend   # backend logs
docker compose restart backend   # after code changes
docker compose up -d backend     # after .env changes
docker compose down              # stop (data preserved)
```

> - Make sure no recognition task is running before restarting the backend (pause it if needed).
> - The frontend build is bind-mounted into Nginx — **never `rm -rf dist`**.

## Neo4j Data Model

```
(:Person {name, gender, birth/death year, places, generation, biography, photo, ...})  (:Place)  (:Document)
(:Person)-[:PARENT_OF]->(:Person)          parent → child
(:Person)-[:SPOUSE_OF {marriage_date}]->(:Person)  spouse
(:Person)-[:HAS_DOCUMENT]->(:Document)
```

## License

MIT (see [LICENSE](./LICENSE)): free use/modification/redistribution of the source code; code
copyright stays with the developer (© 2026 Yugo, AI-assisted development); genealogy data entered
into the system, scanned files and AI-recognized content are **not** licensed with the code and
remain with the data owners.
