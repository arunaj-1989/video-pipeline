# Configuration and Deployment Setup

This guide lists the application settings, external services, credentials, and
deployment locations. It also distinguishes what is available in this
repository from what still must be implemented before production.

> **Production status:** Universal Video Maker is not production-ready and does
> not currently generate a video end to end. The web studio is a browser-only
> preview. Backend modules and provider adapters exist, but they are not wired
> into the web application as a production workflow. Do not put real users,
> confidential material, or production decisions through the preview.

## 1. Quick answer: what credentials and services are needed?

| Service | Key or account required? | What it is for | Current integration status |
|---|---|---|---|
| PostgreSQL | Database credentials/connection string | Durable application records and workflow history | Models and migrations exist; web workflow is not connected |
| Tavily | Tavily API key | Search-result discovery for research | Adapter exists; not connected to the UI/API workflow |
| Ollama | No API key for a local Ollama server by default | Local research, planning, and embeddings | Adapters exist; not connected to the UI/API workflow |
| Keycloak | Admin bootstrap credentials for local Compose; production identity-provider deployment | User identity and API token validation | Local development setup exists; only `/api/v1/auth/me` is protected; no UI login |
| MinIO or S3-compatible storage | Access key and secret key | Durable media/source artifact storage | Storage adapter exists; service/bucket provisioning and workflow integration remain |
| RunPod | RunPod API key and Serverless endpoint ID | GPU compute dispatch | Budgeted adapter exists; no production video worker or UI/API dispatch flow |
| LTX-2.5 model access | Acceptance of gated model terms/license; worker-side model access | Selected ComfyUI video model | Custom worker is not implemented; do not assume a RunPod secret alone is sufficient |
| FFmpeg tools | `ffprobe` executable on any host inspecting media | Media metadata and artifact validation | Inspector exists; render/assembly workflow is not connected |

There is **no OpenAI key requirement** in the current dependency configuration.
No YouTube, TikTok, Instagram, or other publishing-platform credentials are
currently used: external publishing and live analytics integrations are not
implemented.

## Where to get each key, credential, or service

### Tavily search API key

1. Create/sign in to a Tavily account at the [Tavily platform](https://app.tavily.com/home).
2. Find **API keys** in the account dashboard and create/copy a key. Tavily's
   [key creation instructions](https://help.tavily.com/articles/9170796666-how-can-i-create-an-api-key)
   explain the dashboard flow.
3. Put it in `UVM_TAVILY_API_KEY` in your ignored local `.env`, or in the
   production deployment's secret manager.

Tavily also documents [API key management and security](https://docs.tavily.com/documentation/best-practices/api-key-management).
Do not place this key in Next.js/browser configuration.

### Runpod API key and Serverless endpoint ID

These are two separate values:

1. Sign in to the [Runpod console](https://console.runpod.io/).
2. Create an API key on the [Credentials page](https://console.runpod.io/user/credentials).
   Follow Runpod's [credential management guide](https://docs.runpod.io/get-started/credentials);
   choose the least permissions the current workflow supports and store the
   secret as `UVM_RUNPOD_API_KEY`.
3. Create/deploy a Serverless endpoint from the Runpod console. The endpoint
   detail page shows its ID. See the [Serverless endpoint overview](https://docs.runpod.io/serverless/endpoints/overview)
   and [sending endpoint requests](https://docs.runpod.io/serverless/endpoints/send-requests).
   Set the ID as `UVM_RUNPOD_SERVERLESS_ENDPOINT_ID`.

Creating an account, API key, or endpoint may involve billing. Do not deploy a
paid GPU worker or submit a paid test job until spend limits, worker behavior,
and the required approval are understood. The custom LTX-2.5 video worker is
not supplied by this repository.

### Hugging Face account/token for gated model files (worker only)

Hugging Face is the selected upstream host for LTX-2.5 model weights; this is
**not an application setting today**. If the deployed worker needs to download
gated files:

1. Sign in or create an account at [Hugging Face](https://huggingface.co/).
2. Read the [Lightricks LTX-2.5 model page and license](https://huggingface.co/Lightricks/LTX-2.5);
   request/accept access there if the repository requires it.
3. Create a scoped user access token from
   [Hugging Face token settings](https://huggingface.co/settings/tokens).
   Review the official [gated-model access guide](https://huggingface.co/docs/hub/models-gated).
4. Store the token only in the Runpod worker/deployment secret store if the
   worker's model download procedure needs it. The application does not
   currently read a Hugging Face token, and the worker must define its own
   secret contract.

Accepting an access agreement is not a substitute for reviewing the model
license and checking that the intended use is permitted. Never commit a token
or bake it into a worker image.

### MinIO/S3 access keys

MinIO does not issue a hosted API key from a public signup page. The person
operating your MinIO/S3-compatible storage creates credentials inside that
deployment:

1. Provision the storage service and create the private bucket named by
   `UVM_MINIO_BUCKET`.
2. In the storage administrator console, create an application/service
   identity with only the bucket actions required by the API or worker.
3. Generate an access key and secret key for that identity and save them when
   shown; set `UVM_MINIO_ACCESS_KEY` and `UVM_MINIO_SECRET_KEY` in the
   deployment secret store.

See MinIO's [security and access management guide](https://docs.min.io/aistor/administration/console/security-and-access/)
and [access policy documentation](https://docs.min.io/aistor/administration/iam/access/).
Console names and features can vary by MinIO edition/version. Do not use a
root/admin credential for application uploads. If using a cloud S3-compatible
service instead, create an IAM/service account in that provider's console and
use the provider's endpoint, key, secret, and bucket values.

### PostgreSQL connection credentials

PostgreSQL does not require a third-party API key for this application. Create
a database and a dedicated application user/password either in the managed
database provider's console or in your self-managed PostgreSQL installation.
The provider/administrator supplies the host, port, database name, username,
password, and TLS requirements used to form `UVM_DATABASE_URL`.

- [PostgreSQL Windows download/install information](https://www.postgresql.org/download/windows/)
- [PostgreSQL documentation](https://www.postgresql.org/docs/)

For production, use a managed production database or a securely operated
PostgreSQL service, not the local development setup. Keep the password in a
secret manager. The repository README describes the `PGPASSFILE` option for
local CLI authentication.

### Keycloak credentials and OIDC values

Keycloak does not give you an external API key for this setup. For local
development, you choose a development bootstrap username/password and set
`UVM_KEYCLOAK_BOOTSTRAP_ADMIN` and `UVM_KEYCLOAK_BOOTSTRAP_PASSWORD` in `.env`;
Compose uses these to initialize the local Keycloak administrator. For
production, deploy your own Keycloak service and create its realm, clients,
users, and roles using its secured administrator console. The `UVM_OIDC_*`
values are the issuer URL, JWKS URL, and audience configured for that realm
and API client—not secret API keys.

- [Keycloak production configuration guide](https://www.keycloak.org/server/configuration-production)
- [Keycloak server guides](https://www.keycloak.org/guides)

The repository's local `start-dev` Compose service and realm import are for
development only; do not expose them as a production identity service.

### Ollama, ChromaDB, and FFmpeg

These dependencies do not require vendor API keys in the current local setup:

- Install Ollama using the [official downloads](https://ollama.com/download)
  or [Windows download page](https://ollama.com/download/windows).
  The configured chat/embedding model names must also be installed in Ollama.
- ChromaDB is installed with the project's `vector` extra; see the
  [Chroma documentation](https://docs.trychroma.com/). It is a local vector
  store, not an API credential provider.
- Install FFmpeg/`ffprobe` from the [official FFmpeg download page](https://ffmpeg.org/download.html)
  and consult the [ffprobe documentation](https://ffmpeg.org/ffprobe.html).
  On Windows, use a reputable build linked from the official downloads page.

### Base development tools

- [Python downloads](https://www.python.org/downloads/) — the project requires
  Python 3.11 or newer.
- [Node.js downloads](https://nodejs.org/en/download/) — npm is included with
  the Node.js installation.
- [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
  — needed only if you want to run the local Keycloak Compose service.

## 2. Software and infrastructure dependencies

### Needed to run the local preview

- Windows PowerShell (the provided startup script is `startup.ps1`).
- Python 3.11 or newer.
- Node.js/npm compatible with the installed Next.js 16 project.
- Python packages installed from `pyproject.toml`.
- Web packages installed from `apps/governance-web/package-lock.json`.

The preview itself uses illustrative browser data. Starting the API alongside
it does not make the browser workflow live.

### Needed by backend features

- PostgreSQL for durable relational records and Alembic migrations.
- The Python `vector` extra (`chromadb`) if using the Chroma vector adapter.
- Ollama and the configured chat and embedding models if using local AI
  adapters.
- Tavily credentials if using Tavily source search.
- `ffprobe` on a host that inspects media files.
- Keycloak for OIDC token issuance/validation.
- MinIO or another compatible S3 service for object storage.
- A RunPod account, configured Serverless endpoint, and GPU worker for remote
  jobs.

These backend components are not all started by `startup.ps1` or `compose.yaml`.
The current Compose file starts **Keycloak only**, in development mode.

### Additional production infrastructure

Before a production launch, provision and operate the following separately:

- A production-grade PostgreSQL service with encryption, restricted network
  access, automated backups, and tested restores.
- Keycloak backed by a supported persistent database, with HTTPS, secure
  administrator access, realm/client setup, recovery procedures, and user
  lifecycle ownership.
- HTTPS ingress/reverse proxy, DNS, certificate renewal, firewall/network
  rules, and a deployment platform for the API and web app.
- An S3-compatible storage service, private bucket, least-privilege service
  accounts, lifecycle/retention rules, and tested backup/restore.
- A central secret manager, monitoring/alerting, log retention, incident
  response, and cost controls.
- A custom, tested RunPod worker and GPU/model cache plan before enabling
  rendering.

The repo does not currently supply a complete production deployment stack,
production Compose configuration, or a production secret manager integration.

## 3. Where settings belong

### Local development

1. Copy `.env.example` to `.env` in the project root (the startup script does
   this automatically if `.env` is missing).
2. Edit `.env` with local, development-only values. `.env` is ignored by Git;
   keep it that way.
3. Start services you need separately. For example, `startup.ps1` starts the
   local API and web preview, but not PostgreSQL, Ollama, MinIO, or Keycloak.
4. For the local Keycloak Compose service, set
   `UVM_KEYCLOAK_BOOTSTRAP_ADMIN` and
   `UVM_KEYCLOAK_BOOTSTRAP_PASSWORD` in `.env` before running
   `docker compose up -d keycloak`.

Do not use a developer `.env` file as the production secret-management plan.

### Production

Set the same `UVM_...` settings as process/container environment variables
from the deployment platform's secret manager or protected environment
configuration. Keep secret values out of source code, build logs, Docker
images, browser code, screenshots, and documentation.

The web application currently has no configured API URL or login settings
because it does not call the API. Do not put server credentials in a
`NEXT_PUBLIC_...` variable; Next.js public variables are exposed to browsers.

The existing `startup.ps1` forcibly selects development mode, binds services to
loopback, launches Next.js in dev mode, and does not start dependencies or run
migrations. It is a local convenience script, not a production launcher.

## 4. Application environment settings

All application settings use the `UVM_` prefix. Secret fields should be
populated only in a local ignored `.env` or, preferably for production, a
deployment secret manager.

### Core application and data

| Setting | Required? | Purpose / where it points |
|---|---:|---|
| `UVM_ENVIRONMENT` | Yes for deployment | `development`, `test`, or `production`. The startup script overrides this to `development`. |
| `UVM_LOG_LEVEL` | Optional | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. Prefer `INFO` or stricter in production; avoid logging secrets. |
| `UVM_API_PREFIX` | Optional | API route prefix; default `/api/v1`. Keep it consistent with ingress routing and clients. |
| `UVM_DATABASE_URL` | Required for durable backend workflows | SQLAlchemy PostgreSQL connection URL. Use the deployment provider's secret mechanism; do not put a password in committed files. Apply schema migrations with `python -m alembic upgrade head` as a controlled deployment step. |
| `UVM_CHROMA_PERSIST_DIRECTORY` | Optional | Local Chroma persistence directory; default `data/chroma`. Protect/backup or rebuild according to the index policy. |
| `UVM_CHROMA_COLLECTION` | Optional | Chroma collection name; default `universal_video_knowledge`. |
| `UVM_SOURCE_SNAPSHOT_DIRECTORY` | Optional | Local Tavily excerpt snapshot directory; default `data/source-snapshots`. This is not a general generated-video output folder. |

### Tavily search

| Setting | Required? | Purpose |
|---|---:|---|
| `UVM_TAVILY_API_KEY` | Only when using Tavily | Tavily account API key. Store as a secret. |
| `UVM_TAVILY_API_BASE_URL` | Optional | HTTPS API origin; default `https://api.tavily.com`. |
| `UVM_TAVILY_REQUEST_TIMEOUT_SECONDS` | Optional | Request timeout; default `30`. |
| `UVM_TAVILY_MAX_RESULTS` | Optional | Search-result limit; default `6` (allowed 1–20). |

### Ollama local models

| Setting | Required? | Purpose |
|---|---:|---|
| `UVM_OLLAMA_BASE_URL` | Only when using Ollama | Ollama service origin; default `http://localhost:11434`. For a remote host, configure a protected HTTPS endpoint and network policy. |
| `UVM_OLLAMA_CHAT_MODEL` | Optional | Chat model name; default `qwen2.5:7b`. Pull/install it in Ollama on the host that serves requests. |
| `UVM_OLLAMA_EMBEDDING_MODEL` | Optional | Embedding model name; default `nomic-embed-text`. Pull/install it before embedding workflows. |
| `UVM_OLLAMA_REQUEST_TIMEOUT_SECONDS` | Optional | Request timeout; default `180`. |

Local Ollama does not require a vendor API key by default. Model licensing,
capacity, privacy, and performance still need to be reviewed for the deployment.

### Keycloak / OpenID Connect

| Setting | Required? | Purpose |
|---|---:|---|
| `UVM_OIDC_ISSUER_URL` | Required when verifying tokens | Exact issuer URL of the Keycloak realm. Must match the token's `iss` claim. |
| `UVM_OIDC_JWKS_URL` | Required when verifying tokens | HTTPS-reachable JWKS endpoint used to verify signatures. |
| `UVM_OIDC_AUDIENCE` | Required when verifying tokens | Expected API audience; default `universal-video-maker-api`. Keep consistent with the realm's audience mapper. |
| `UVM_KEYCLOAK_BOOTSTRAP_ADMIN` | Local Compose only | Development Keycloak initial administrator username. Not an API setting. |
| `UVM_KEYCLOAK_BOOTSTRAP_PASSWORD` | Local Compose only | Development Keycloak initial administrator password. Never reuse a production credential. |

The local realm/client/role import is
`identity/keycloak/universal-video-maker-realm.json`; the local-only service
definition is `compose.yaml`. Configure production realms and clients in the
production identity deployment rather than exposing the development Compose
service. Rotate bootstrap credentials and use HTTPS.

Current limitation: the API verifies identity only at `/api/v1/auth/me`. The
web UI has no Keycloak login flow, and identity subjects are not linked to
internal user records or protected domain operations.

### MinIO / S3-compatible object storage

| Setting | Required? | Purpose |
|---|---:|---|
| `UVM_MINIO_ENDPOINT_URL` | Required when using MinIO | S3-compatible service URL. Remote production endpoints must use HTTPS and be reachable by the API and worker. |
| `UVM_MINIO_ACCESS_KEY` | Required when using MinIO | Least-privilege service account access key. Do not use a root/admin key for application workloads. |
| `UVM_MINIO_SECRET_KEY` | Required when using MinIO | Matching secret key, stored as a secret. |
| `UVM_MINIO_BUCKET` | Required when using MinIO | Existing bucket name; default `universal-video-assets`. Create it and set access policy/lifecycle explicitly. |
| `UVM_MINIO_REGION` | Optional | S3 region label; default `us-east-1`. Match the storage service configuration. |

The MinIO service and bucket are not provisioned by this repository's Compose
file. A RunPod worker cannot reach an endpoint bound to a developer's
`localhost`; use a private, protected, HTTPS endpoint and worker credentials
scoped to the required bucket/actions.

### RunPod and spend limits

| Setting | Required? | Purpose |
|---|---:|---|
| `UVM_RUNPOD_API_KEY` | Required only when dispatching RunPod work | RunPod API credential; store as a secret and apply account restrictions where available. |
| `UVM_RUNPOD_SERVERLESS_ENDPOINT_ID` | Required only when dispatching RunPod work | ID of the deployed Serverless endpoint. No compatible custom video worker is currently supplied. |
| `UVM_RUNPOD_API_BASE_URL` | Optional | RunPod API base URL; default `https://api.runpod.io/v2`. |
| `UVM_RUNPOD_REQUEST_TIMEOUT_SECONDS` | Optional | Request timeout; default `30`. |
| `UVM_RUNPOD_MAX_JOB_COST_USD` | Required operational policy | Per-attempt render cost ceiling; default `$25.00`. Select a conservative value and verify actual billing semantics. |
| `UVM_RUNPOD_MONTHLY_BUDGET_USD` | Required operational policy | Application-side monthly budget ceiling; default `$250.00`. Add independent provider-side spending limits and alerts. |

The adapter and budget checks do not create a worker, guarantee a provider-side
hard cap, or make the browser UI dispatch jobs. Test idempotency, cancellation,
retries, cost accounting, and alerting before authorizing paid jobs.

### Model access and worker-only secrets

LTX-2.5 is the selected model path, but the custom ComfyUI/RunPod worker is not
implemented. Model weights are gated and subject to upstream terms. The model
requires substantial GPU memory and disk. Before deployment:

1. Review and accept the model access terms and license with the model host.
2. Build and test the custom worker, ComfyUI graph, video encoding, retries,
   and output manifest.
3. Put any model-host token needed for worker downloads in the worker's secret
   store, not this repository, browser, local UI, or application `.env`.
4. Configure a persistent model cache and scoped storage credentials without
   baking model weights or credentials into the image.
5. Confirm the worker can upload video outputs over HTTPS to the configured
   object store.

No model token setting is currently consumed by the application code; the
worker must define its own secret contract when it is implemented.

## 5. Local setup checklist

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,vector]"
npm --prefix apps\governance-web ci
Copy-Item .env.example .env
```

Edit `.env` only for local values. Then:

```powershell
.\startup.ps1
```

This starts the API and web app in development mode. The web page is
`http://127.0.0.1:3000`; API documentation is
`http://127.0.0.1:8000/docs`; liveness is
`http://127.0.0.1:8000/health/live`. The script does not start PostgreSQL,
Ollama, MinIO, or Keycloak. To start the local development Keycloak service,
set both bootstrap values in `.env` and run:

```powershell
docker compose up -d keycloak
```

Do not interpret a healthy page or API as evidence that a provider, database,
login, rendering, or publishing workflow is live.

## 6. Production launch blockers

Do not launch as a production video-generation service until at least these
items are complete and verified:

- Persistent PostgreSQL, migration/rollback process, backups, and restore test.
- Production Keycloak deployment and secure realm/client configuration.
- UI login and server-side authorization on every protected domain operation;
  internal-user mapping, account recovery, role lifecycle, and audit review.
- API and UI deployment behind HTTPS with production-safe startup and
  configuration validation.
- Durable source snapshots and output storage with retention, access controls,
  backup, and tested restore.
- Custom RunPod/ComfyUI worker that renders LTX-2.5 video and returns a
  checksummed manifest to reachable object storage.
- End-to-end UI/API workflow from topic intake through research, factual
  review, outline, storyboard, spend approval, render, validation, and output
  retrieval.
- Publishing and analytics platform adapters, OAuth consent/token storage,
  platform policy review, and human release controls if those features are
  required.
- Operational logs/metrics/alerts, provider quotas, data retention and privacy
  review, abuse controls, cost alerts, and incident/runbook ownership.

See [the architecture overview](./architecture.md) and
[the user guide](./user-guide.md) for scope and current behavior.
