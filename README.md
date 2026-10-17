# Bugsy

Bugsy is a Python dependency compatibility and repair assistant. This first milestone establishes the local React-to-FastAPI connection and a separate harmless ConTree sandbox smoke check. Dependency analysis and repair workflows are not implemented yet.

## Local development

1. If you do not already have a root `.env`, copy `.env.example` to `.env`; otherwise add only the needed settings to your existing file. Keep it local; never put secrets in `VITE_` variables.
2. Create the backend environment and install Bugsy's own development dependencies:

   ```powershell
   uv venv --python 3.12 backend/.venv
   Set-Location backend
   uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
   ```

3. Start FastAPI from `backend/`:

   ```powershell
   Set-Location backend
   .venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
   ```

4. In another terminal, install the frontend's dependencies and start Vite:

   ```powershell
   npm.cmd --prefix frontend install
   npm.cmd --prefix frontend run dev
   ```

The API health endpoint is `http://127.0.0.1:8000/health`; the frontend displays whether that API responds. This health response does not indicate sandbox availability or dependency compatibility.

## Local checks

```powershell
Set-Location backend
.venv/Scripts/python.exe -m pytest -q
Set-Location ..
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

## Separate ConTree smoke check

The smoke script runs only a harmless Python command in the remote image. It does not resolve, install, or import user-submitted packages. Confirm your ConTree account's current usage terms before invoking the remote call. Set `SANDBOX_ENABLED=true`, `NEBIUS_API_KEY`, and `CONTREE_BASE_URL` in the ignored root `.env`, then run:

```powershell
backend/.venv/Scripts/python.exe test_sandbox.py
```

The script prints the remote command output, exit code, detected OS, and Python version. It never prints the key. A successful smoke check only confirms that this harmless remote command ran; it is not a dependency verification result.
