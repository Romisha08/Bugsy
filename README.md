# Bugsy

Bugsy is a Python dependency compatibility and repair assistant. The current app accepts a `requirements.txt`, validates and previews its parsed entries, retrieves PyPI metadata, builds a deterministic compatibility report, and can request evidence-grounded AI explanations and repair proposals. Repair proposals require a separate resolver check before they become ready. Installation and import verification are separate approval-gated Sandbox stages. Project tests are reported as not run because project code is not uploaded.

## Current verification limits

- Resolution, installation, imports, and project tests have distinct statuses. An unavailable or incomplete result is never a successful verification.
- Real ConTree operations require `SANDBOX_ENABLED=true` and configured ConTree credentials. Real AI explanations and repair selection require `TOKEN_FACTORY_ENABLED=true` and a configured API key.
- Both service flags default to `false`; installation artifact retention also defaults to `false`.
- Recent live ConTree file-staging attempts have failed with transport errors. Do not present the live Sandbox workflow as reliable until a separately approved run succeeds. Prior successful resolver evidence applies only to the specific proposal and environment where it was captured.
- Retained-image installation/import flow depends on provider behavior and account access. Artifact retention cost, lifetime, and availability have not been established.
- The API has no user authentication or application-level rate limiting. Before enabling cost-bearing integrations on a public instance, restrict access or add an abuse-control layer.

## Local development

The root `.env` is loaded by the backend configuration and is ignored by Git. Keep credentials there locally and in the hosting provider's secret environment settings in production. Never put credentials in a `VITE_` variable; Vite embeds those values into browser assets.

Install the backend in its project-specific environment and start it from `backend/`:

```powershell
uv venv --python 3.12 backend/.venv
Set-Location backend
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, install frontend dependencies and start Vite:

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run dev
```

The local API health route is `http://127.0.0.1:8000/health`. It reports process health only; it does not check PyPI, Token Factory, or ConTree.

## Simplest hosting path: Render

Use one Render Web Service for FastAPI and one Render Static Site for Vite. This matches the existing two-process architecture and requires no application proxy or deployment manifest.

### 1. Deploy the backend Web Service

Create a Web Service from the repository with:

| Setting | Value |
| --- | --- |
| Root Directory | `backend` |
| Runtime | Python |
| Python version | `3.12.13` via `PYTHON_VERSION` |
| Build Command | `pip install .` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |

Add these non-secret environment settings in the backend service:

```text
CORS_ORIGINS=https://<your-static-site>.onrender.com
# Optional: omit BUGSY_DATABASE_PATH on Render Free to use /tmp/bugsy.sqlite3.
# For a paid service with a persistent disk mounted at /var/data, set:
# BUGSY_DATABASE_PATH=/var/data/bugsy.sqlite3
SANDBOX_ENABLED=false
TOKEN_FACTORY_ENABLED=false
SANDBOX_RETAIN_INSTALLATION_ARTIFACT=false
```

On Render Free, leave `BUGSY_DATABASE_PATH` unset. Bugsy stores SQLite at `/tmp/bugsy.sqlite3`, which is writable but ephemeral: records can be lost when the instance restarts or redeploys. For durable data, attach a persistent disk to an eligible paid backend service, mount it at `/var/data`, and set `BUGSY_DATABASE_PATH=/var/data/bugsy.sqlite3`. SQLite on a Render disk is a single-instance setup, can cause downtime during deploys, and cannot be shared across multiple service instances. Do not scale this backend horizontally while it uses SQLite on a disk.

Only if you separately choose to enable a real integration, add the required backend-only credentials/settings in Render's Environment page: `NEBIUS_API_KEY`, `CONTREE_BASE_URL`, and `CONTREE_PROJECT_ID` for ConTree; `NEBIUS_BASE_URL` and `NEBIUS_MODEL_ID` are optional Token Factory overrides. Keep the two feature flags off until you are ready for those external calls and their possible usage costs. Configure secret values in Render's dashboard, never in source files or frontend variables.

### 2. Deploy the frontend Static Site

Create a Static Site from the same repository:

| Setting | Value |
| --- | --- |
| Root Directory | `frontend` |
| Build Command | `npm ci && npm run build` |
| Publish Directory | `dist` |
| Build Environment Variable | `VITE_API_BASE_URL=https://<your-backend>.onrender.com` |

`VITE_API_BASE_URL` is compiled into the frontend at build time. If the backend URL changes, update this value and rebuild the static site. After Render assigns the frontend URL, set the exact origin in the backend's `CORS_ORIGINS` and redeploy the backend.

### 3. Configure and review

1. Create the backend service first and record its public HTTPS URL.
2. Create the frontend static site with that URL as `VITE_API_BASE_URL`.
3. Set `CORS_ORIGINS` to the frontend's exact HTTPS origin.
4. Confirm `/health` returns HTTP 200.
5. Keep Sandbox, Token Factory, and artifact retention disabled until credentials, access, costs, and a controlled live integration result have been reviewed.
6. Test the local/public UI only against workflows whose dependencies are enabled. With Sandbox disabled, compatibility resolution and repair resolution cannot be demonstrated as completed. With Token Factory disabled, model explanations and repair selection are unavailable. Do not represent either as successful.

Render's filesystem is ephemeral by default. The `/tmp` default allows the free service to start, but it does not preserve records across restarts or redeploys. A mounted persistent disk and explicit `BUGSY_DATABASE_PATH` are required for durable SQLite storage. Back up any existing local SQLite data separately before migrating it; a new hosted disk starts with an empty database.

## Configuration reference

| Variable | Purpose | Safe default |
| --- | --- | --- |
| `VITE_API_BASE_URL` | Browser API origin, set at frontend build time | Localhost |
| `CORS_ORIGINS` | Comma-separated allowed browser origins | Local Vite origins |
| `BUGSY_DATABASE_PATH` | SQLite file path | System temporary directory (Render: `/tmp/bugsy.sqlite3`; ephemeral) |
| `SANDBOX_ENABLED` | Enables real ConTree Sandbox operations | `false` |
| `TOKEN_FACTORY_ENABLED` | Enables real model requests | `false` |
| `SANDBOX_RETAIN_INSTALLATION_ARTIFACT` | Retains successful install image for later imports | `false` |
| `CONTREE_BASE_URL` | ConTree API base URL | Unconfigured |
| `CONTREE_PROJECT_ID` | ConTree `Project` header value | Unconfigured |
| `NEBIUS_API_KEY` | Backend credential for Nebius integrations | Unconfigured |
| `NEBIUS_BASE_URL` | Optional Token Factory API base override | Official default in backend settings |
| `NEBIUS_MODEL_ID` | Optional Token Factory model override | Backend default |

## Demo workflow status

- **Local UI and API:** implemented; local mocked tests and frontend build have been used during development. Confirm the current checkout with local checks before submission.
- **Requirements parsing and read-only preview:** implemented locally.
- **PyPI metadata and deterministic compatibility report:** implemented, but live metadata access depends on outbound network access and can return incomplete results.
- **Real ConTree resolution:** implemented but currently unverified as a reliable hosted workflow; recent live file-staging attempts failed with transport errors. No successful run should be inferred from the code path.
- **Token Factory explanation and repair candidate selection:** implemented, but availability depends on the feature flag, credential, and provider access. A successful current deployment request has not been confirmed here.
- **Package installation and import smoke checks:** implemented as separate Sandbox stages, but current live success and retained-image availability are unverified.
- **Project-specific tests:** not run — project code not provided.

## Local validation commands

```powershell
Set-Location backend
.venv/Scripts/python.exe -m pytest -q
Set-Location ..
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

## Separate ConTree smoke test

`test_sandbox.py` is a separate root-level smoke script using the legacy SDK. It is not the production adapter. It runs only a harmless fixed Python command and requires explicit `SANDBOX_ENABLED=true`; a smoke-test pass does not verify dependency compatibility. Review account usage terms and approve a specific remote operation before enabling or running it.
