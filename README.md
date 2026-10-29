# Universal Video Maker

Universal Video Maker is a topic-agnostic video-production workspace. Topics
can come from any subject; audience, research, review, and production settings
are intended to be selected for each project. The laptop is the orchestration
control plane; RunPod is reserved for GPU-intensive rendering work.

The current web studio accepts any topic and free-form category, but
its browser UI is still a preview: topic ideas remain in the current browser
session. The backend services are not yet connected into an end-to-end topic
to video workflow. Tavily search and Ollama research, embedding, and plan
generation adapters are implemented, but have not been exercised with live
provider accounts and are not wired to the API or UI. Media generation,
publishing, and platform analytics are not live integrations.
Factual content uses evidence and human-review gates; those gates are not a
complete content-safety classifier. Sensitive or high-impact topics still
require qualified editorial review.

## Architecture

The approved system design, including component boundaries, workflows, data
model, governance, RAG, RunPod, observability, roadmap, and deployment
assumptions, is documented in [docs/architecture.md](docs/architecture.md).

## Guides

- [Production operating guide](docs/user-guide.md): the intended end-to-end
  workflow, user responsibilities, approvals, outputs, storage, and an explicit
  status for each step that distinguishes the production design from today's
  preview.
- [Configuration and deployment setup](docs/configuration-setup.md): required
  dependencies, provider credentials, environment settings, secret locations,
  and production readiness blockers.

## Core Platform

The API foundation provides environment-based configuration, structured JSON
logging, request correlation IDs, liveness/readiness endpoints, and
framework-independent RBAC policy primitives.

## Database Layer

The persistence layer includes SQLAlchemy models for the core domain,
versioned Alembic migrations, session and repository helpers, and a ChromaDB
vector-store adapter. PostgreSQL is the authoritative store; ChromaDB is a
rebuildable local index. The vector adapter accepts embeddings produced by the
later embedding/RAG module and does not invent or generate embeddings itself.

Install the application, tests, and local ChromaDB provider:

```powershell
python -m pip install -e ".[dev,vector]"
```

For local development, start the persistent PostgreSQL container and apply
migrations:

```powershell
.\scripts\start-local-postgres.ps1
python -m alembic upgrade head
```

The Compose service binds only to localhost and stores database files in the
persistent `postgres-data` volume. The local-only container uses trust
authentication and must never be exposed to other machines or used in
production. The helper reads the existing PostgreSQL URL without printing it
and refuses non-loopback hosts. For managed production PostgreSQL, store its
URL in the deployment secret manager. `UVM_CHROMA_PERSIST_DIRECTORY` controls
the local ChromaDB data directory; `UVM_CHROMA_COLLECTION` controls the
collection.

Create and verify a local backup:

```powershell
.\scripts\backup-local-postgres.ps1
.\scripts\verify-local-postgres-restore.ps1 -BackupPath .\backups\postgres\<backup-file>.dump
```

The backup script writes a timestamped custom-format dump; restore verification
restores it into a temporary database and removes that database afterward. Keep
local backups out of source control, copy production backups off-device, and
schedule/monitor provider-managed backups for production. ChromaDB can be
rebuilt from durable source and claim records; generated media and source
snapshots require their own object-storage backup policy.

The database tests use SQLite and a fake vector collection, so they do not
require a running PostgreSQL server or ChromaDB service.

## Topic Discovery

The Topic Discovery service accepts typed candidates from injected curated
catalogs, validates HTTPS source references and editorial ranking inputs,
calculates an equal-weight score, removes normalized-title duplicates, and
imports new topics as `proposed`. Ranking inputs and source references are
stored with the topic, and each import creates an audit event. The caller owns
the database transaction.

Ranking dimensions are subject relevance, story/visual potential, search
interest, audience suitability, novelty, and timeliness. They do not assume a
particular subject area.

This module does not scrape external sites, estimate search demand, or expose
an unauthenticated write endpoint. The daily 06:00 IST schedule policy is
available for the workflow runner; recurring job registration and live source
connectors are not configured yet.

## Research

The internal Research service creates numbered research revisions for a topic
from caller-captured UTF-8 source snapshots. It verifies snapshot SHA-256
hashes, links source metadata and storage URIs, stores findings and open
questions, and records candidate claims as `unverified` with excerpts that
must match their captured source. Audit events are recorded for created
research revisions.

`ResearchService` does not fetch web pages, store snapshot bytes itself, call an
extraction model, or expose a public API. Callers using it directly must persist
captured snapshots and provide the resulting storage URI. A URL currently maps
to one immutable content hash; changed content at the same URL is rejected
until versioned source snapshots are implemented.

The Tavily adapter can search a topic and persist only Tavily's short search
excerpts as local, content-hashed snapshots; it does not fetch or archive full
pages. The Ollama research assistant can turn those excerpts into candidate
claims, but exact quote/source matching is required and all claims remain
`unverified` until a permitted human fact-check decision. The provider workflow
is not exposed through the API or UI. Configure `UVM_TAVILY_API_KEY` locally;
never commit the credential.

## Fact Check

The Fact Check service assesses persisted evidence for supporting,
contradicting, and contextual material; conservatively groups sources by
declared lineage, publisher, and hostname; and records a permission-checked
human decision with classification, rationale, evidence assessment, reviewer,
timestamp, and audit event. A `verified` decision requires at least two
independent supporting groups and no conflicting evidence. Rejection requires
contradicting evidence. Ambiguous or conflicting cases must remain in
follow-up.

This is an editorial aid, not automated truth determination. The service does
not fetch or validate source contents, perform model-based fact checking,
expose an API route, or provide authentication; it relies on the existing
application-level `Principal` and permission policy.

## RAG

The Grounded RAG service chunks evidence excerpts for currently verified
claims, accepts document/query embeddings through an injected provider
interface, indexes the chunks in the configured vector-store adapter, and
retrieves citations only when the current relational claim/evidence state
still permits them. Answers use explicit retrieved-chunk citations and exact
quotes; the validator checks citation membership and quote presence, not
semantic entailment.

An Ollama embedding adapter is available and defaults to
`nomic-embed-text`; the model must be installed in the local Ollama runtime.
No vector indexing or retrieval API is exposed, and the provider has not been
tested against a live Ollama instance.

## Episode Planner

The Episode Planner validates and persists an editor-authored, evidence-linked
episode outline as an `EpisodeRevision`. A plan must total exactly 180 seconds,
contain learning objectives and a story arc, and include exactly three
30–60-second Short concepts. Factual narration in every beat and Short must
cite a retrieved evidence chunk with a verbatim quote; the planner rechecks
that cited material still belongs to a currently verified claim and source.
Plans require the existing episode-write permission and remain drafts.

The Ollama plan generator can create an in-memory draft from the RAG service's
currently verified retrievals. It validates the exact plan format and requires
each factual narration citation and quote to come from retrieved evidence.
The draft still needs editor review and persistence through the permission-
checked planner service; it does not create storyboard scenes, approve
content, or expose an API/UI. Semantic entailment between narration and quotes
still requires editorial review.

## Storyboard

The Storyboard service creates a numbered `EpisodeRevision` with `Scene` rows
from the latest draft episode-plan revision. Editors provide a visual
description, camera direction, visual prompt, and required-asset list for each
plan beat. Scene timing, titles, purpose, narration, and citations are
inherited from the evidence-grounded plan, keeping visuals tied to reviewed
content. The service validates editor permissions, checks that cited evidence
is still verified and unchanged, and writes an audit event.

The initial workflow requires exactly one scene per plan beat. It stores
prompts and asset requirements only; it does not generate media, manage asset
files, call external providers, or expose a UI/API. No new `.env.example`
settings are needed until a media-generation provider is selected.

## RunPod Integration

The RunPod Serverless adapter dispatches approved storyboard revisions to a
configured endpoint, records remote IDs/status/runtime/costs, supports status
checks/cancellation, and permits only bounded retries of terminal failures.
Render submission requires an active authorized user, an approval for that
exact storyboard revision, and estimates within configured USD per-attempt and
monthly budgets. The worker receives the local render-job ID as a
deduplication key and should treat repeated submissions with that key
idempotently.
The caller must commit the queued job before making the external dispatch call;
the service does not commit caller-owned transactions.

Configure `UVM_RUNPOD_API_KEY` and
`UVM_RUNPOD_SERVERLESS_ENDPOINT_ID` in a local secret environment, never in
source control. The API key is represented as a secret setting; `.env.example`
contains only an empty placeholder. Tests use an injected transport and do
not contact RunPod or incur charges. This implementation uses Serverless
endpoints; it does not create or manage persistent GPU pods or provide a
generation UI/API. A MinIO S3-compatible adapter is available for output
storage, but the RunPod worker is not yet connected to it.

The selected media-generation path is LTX-2.5 through ComfyUI. It is not
deployed yet: the upstream guide specifies a CUDA GPU with at least 32 GB VRAM,
100 GB free disk, and a gated model download governed by Lightricks' community
license. This repository does not contain the model weights or any access
token. See the [official ComfyUI guide](https://docs.ltx.video/open-source-model/integration-tools/comfy-ui)
before provisioning a worker.

## Local identity and object storage

Keycloak is the selected self-hosted OIDC provider. For local development,
set `UVM_KEYCLOAK_BOOTSTRAP_ADMIN` and
`UVM_KEYCLOAK_BOOTSTRAP_PASSWORD` in the ignored `.env`, then run
`docker compose up -d keycloak`. The imported realm configures the public web
client for Authorization Code + PKCE and the API audience. Create a user and
assign the `editor` realm role in the local Keycloak console. The governance
UI signs in with Keycloak, and its topic queue reads and creates PostgreSQL
records through authenticated `/api/v1/topics` endpoints. Other governance
sections remain previews; broader workflow authorization and internal-user
mapping are not implemented.

`MinioObjectStore` supports checksum-tracked `s3://` uploads and downloads;
`MinioArtifactInspector` downloads an object temporarily and validates media
using the existing local `ffprobe` inspector. MinIO itself must be run and the
bucket created separately. Local HTTP endpoints are limited to loopback; a
RunPod worker requires an HTTPS MinIO endpoint that is reachable from RunPod.
Use a bucket-scoped MinIO credential for the worker, not the MinIO root
credential, and keep credentials in local environment configuration.

## Rendering

The rendering module verifies a complete final-output bundle before registering
assets: one 16:9 episode at 180 seconds (±1 second), three 9:16 Shorts of
30–60 seconds, matching WebVTT captions for each, and a readable thumbnail.
The injected inspector must verify SHA-256 checksums and media facts; the local
file inspector uses `ffprobe`, and MinIO objects can be validated through the
MinIO inspector. Asset records retain render-job and storyboard provenance.

This module validates and registers completed output; it does not generate
visuals or assemble video. RunPod output transfer and an FFmpeg/remote render
worker still need to be wired. Validation tests use injected inspectors.

## Publishing

The Publishing service creates a release package from a completed render's
validated assets, with bounded metadata, tags, hashtags, language, visibility,
three Short titles, and a checksummed asset manifest. A publisher explicitly
approves an immutable package fingerprint before recording an external
publication ID, public URL, and timestamp. Metadata tampering invalidates the
approval. No external publishing API is called and nothing is posted
automatically.

## Analytics

Analytics accepts manually captured, cumulative platform snapshots for
published packages, validates counters and chronological monotonicity, and
reports measured CTR, average view duration, engagement, render cost per
video-minute/1,000 views, validated-asset ratio, and storyboard citation
coverage. Publishers can record snapshots; viewers can read summaries.
Snapshots are not fetched from a platform API. No predictions are produced;
title/thumbnail experiments need a larger measured dataset and evaluation.

## Governance UI

The Next.js app in `apps/governance-web` provides a responsive editorial
workspace. Every screen requires Keycloak sign-in and reads PostgreSQL through
role-protected endpoints: the topic queue (`/api/v1/topics`), overview metrics,
lineage counts and reports (`/api/v1/governance/summary`), approval history
(`/api/v1/governance/approvals`), and the audit trail
(`/api/v1/governance/audit-events`, admin only). Editors and admins can approve or
reject a proposed topic with a required note (`POST /api/v1/topics/{id}/decision`,
moving it to `ready_to_plan` or `rejected` and writing an audit event).
Fact verification lists claims
with their evidence and independence assessment (`/api/v1/claims`); users with
the `fact_reviewer` or `admin` role record decisions through
`POST /api/v1/claims/{id}/review`, which enforces the two-independent-source rule
and writes an audit event. The first review creates the reviewer's internal
`users` row from the Keycloak subject. Editors and admins can click **Run research**
on an approved topic (`POST /api/v1/research`): Tavily finds sources, snapshots are
saved under `source_snapshot_directory`, and Ollama proposes claims that are stored as
`unverified` for Fact verification. This needs `UVM_TAVILY_API_KEY` in `.env` and a
running Ollama (503 with a clear message otherwise). `GET /api/v1/episodes` lists episodes.
Editors and admins can click **Plan episode** on an approved (ready to plan) topic;
this calls `POST /api/v1/episodes/plan`, which retrieves verified evidence, asks the
local Ollama model for a 3-minute outline and three Shorts concepts with cited
narration, and saves it as a draft revision (`GET /api/v1/episodes/{id}/plan` to
view). It needs Ollama running with the chat and embedding models, and the
`vector` extra (`pip install -e ".[vector]"`); otherwise the API returns 503. It
returns 409 if the topic has no verified evidence. Recording approvals is not
exposed by the API yet, so those screens show empty states until records exist.

## Monitoring UI

`/monitoring` requires Keycloak sign-in with a role that has `analytics:read`
(`viewer`, `publisher`, or `admin`). It reads `/api/v1/monitoring/overview`,
which aggregates `workflow_runs`, `workflow_steps`, and `render_jobs` for the
selected window (workflow counts and success rate, render jobs and recorded
cost, recent failures). Agent telemetry, storage usage, budgets, and live
RunPod probes are not connected and are shown as such. Empty tables show zeros
and empty states, not sample data.

```powershell
Set-Location apps\governance-web
npm run dev
```

Open `http://localhost:3000` for Universal Video Maker or
`http://localhost:3000/monitoring` for Monitoring. Run `npm run lint`,
`npx tsc --noEmit`, and `npm run build` from the app directory to validate the
UI.

### Run locally

From the project root, start the API and web studio with:

```powershell
# First set UVM_KEYCLOAK_BOOTSTRAP_ADMIN and UVM_KEYCLOAK_BOOTSTRAP_PASSWORD
# in .env, then start Keycloak separately:
docker compose up -d keycloak

.\startup.ps1
```

Create a Keycloak user and assign the `editor` realm role before signing in to
create topics. The topic queue loads/saves PostgreSQL records; the other
workspace screens are still previews.

The script uses `.venv` when available, checks that Python/Node dependencies
are installed, creates `.env` from `.env.example` only if it is missing, and
starts the API and Next.js development server in the background. Startup logs
are written under `logs\startup`. Use `.\startup.ps1 -ApiOnly` or
`.\startup.ps1 -WebOnly` to launch one service, `-NoBrowser` to suppress
opening the UI, or `-NoWait` to skip readiness checks.

To install dependencies for a fresh checkout, run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,vector]"
Set-Location apps\governance-web
npm install
```

The API is available at `http://127.0.0.1:8000`; OpenAPI documentation is
available at `/docs` in development mode. Health endpoints are `/health/live`
and `/health/ready`.

### Run tests

```powershell
python -m pytest
```

## Configuration

Settings use the `UVM_` environment-variable prefix. Existing local
`SBE_`-prefixed variables remain supported as a migration alias; new
configuration should use `UVM_`. The initial settings are:

| Variable | Default | Description |
|---|---|---|
| `UVM_ENVIRONMENT` | `development` | Runtime environment |
| `UVM_LOG_LEVEL` | `INFO` | Structured log level |
| `UVM_API_PREFIX` | `/api/v1` | Versioned API prefix |
| `UVM_SOURCE_SNAPSHOT_DIRECTORY` | `data/source-snapshots` | Local store for short captured search excerpts |
| `UVM_TAVILY_API_KEY` | unset | Secret credential for Tavily Search |
| `UVM_TAVILY_API_BASE_URL` | `https://api.tavily.com` | HTTPS Tavily API origin |
| `UVM_TAVILY_REQUEST_TIMEOUT_SECONDS` | `30` | Tavily request timeout |
| `UVM_TAVILY_MAX_RESULTS` | `6` | Search excerpt count, capped at 20 |
| `UVM_OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama URL; remote URLs must use HTTPS |
| `UVM_OLLAMA_CHAT_MODEL` | `qwen2.5:7b` | Structured research and plan-generation model |
| `UVM_OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model used by grounded RAG |
| `UVM_OLLAMA_REQUEST_TIMEOUT_SECONDS` | `600` | Ollama request timeout |
| `UVM_OIDC_ISSUER_URL` | `http://localhost:8080/realms/universal-video-maker` | Keycloak realm issuer |
| `UVM_OIDC_JWKS_URL` | `http://localhost:8080/realms/universal-video-maker/protocol/openid-connect/certs` | Keycloak signing-key endpoint |
| `UVM_OIDC_AUDIENCE` | `universal-video-maker-api` | Required JWT audience |
| `UVM_KEYCLOAK_BOOTSTRAP_ADMIN` | unset | Local-development Keycloak admin username |
| `UVM_KEYCLOAK_BOOTSTRAP_PASSWORD` | unset | Local-development Keycloak admin password |
| `UVM_MINIO_ENDPOINT_URL` | `http://localhost:9000` | MinIO S3 endpoint; remote endpoints must use HTTPS |
| `UVM_MINIO_ACCESS_KEY` | unset | Secret MinIO access key |
| `UVM_MINIO_SECRET_KEY` | unset | Secret MinIO secret key |
| `UVM_MINIO_BUCKET` | `universal-video-assets` | Existing bucket for generated and validated assets |
| `UVM_MINIO_REGION` | `us-east-1` | S3-compatible signing region |
| `UVM_RUNPOD_API_KEY` | unset | Secret credential for RunPod Serverless |
| `UVM_RUNPOD_SERVERLESS_ENDPOINT_ID` | unset | Configured RunPod Serverless endpoint |
| `UVM_RUNPOD_API_BASE_URL` | `https://api.runpod.io/v2` | HTTPS RunPod API base URL |
| `UVM_RUNPOD_REQUEST_TIMEOUT_SECONDS` | `30` | RunPod request timeout |
| `UVM_RUNPOD_MAX_JOB_COST_USD` | `25.00` | Hard per-attempt estimated cost limit |
| `UVM_RUNPOD_MONTHLY_BUDGET_USD` | `250.00` | Monthly estimated/actual spend limit |
