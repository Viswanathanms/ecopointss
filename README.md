# EcoPoints (full-stack)

Python 3.8+ only. No installs.

    python3 server.py        # then open http://localhost:8000

First run prints the admin username/password once (or set ADMIN_USER / ADMIN_PASSWORD before first run).
Students sign up in the app. Demo QR codes to type: ECO-LIB-01, ECO-CAN-02, ECO-HOS-03 (admin screen shows all).

Env vars: PORT, HOST (0.0.0.0 to expose), ECO_DATA (db + photos folder), ALLOW_DEMO=0 (disable fake camera),
SEED_DEMO=0 (no fake leaderboard users), COOKIE_SECURE=1 (when served over HTTPS).

Camera and QR scanning need HTTPS or localhost. For phones, put it behind an HTTPS reverse proxy (Caddy/nginx).

Server-side: accounts (PBKDF2), HttpOnly sessions, admin role checks, QR proof check, duplicate-photo / daily-limit /
cooldown rules, atomic points ledger and redemptions, audit log, photo storage (data/uploads), login rate limit.

Not done: real image classifier (hook: classify() in server.py), printable QR images, password reset,
admin screen for fulfilling redemptions (API exists: POST /api/admin/redemptions/<id>).
Client-sent camera data can be faked by a determined user; the QR codes and review queue are the real safeguards.

## Deploy as a website
Needs a host that runs a normal always-on server with a persistent disk (NOT Vercel/Netlify, which are serverless).
- Render: push to GitHub, New -> Blueprint (uses render.yaml), set ADMIN_PASSWORD.
- Railway / Fly.io / any VPS: use the Dockerfile (or `python3 server.py`). Set HOST=0.0.0.0, COOKIE_SECURE=1,
  ADMIN_PASSWORD, and mount a persistent volume at the ECO_DATA folder (Docker default: /data).
- Docker locally: docker build -t ecopoints . && docker run -p 8000:8000 -e ADMIN_PASSWORD=change-me -v ecodata:/data ecopoints
  (set COOKIE_SECURE=0 when testing over plain http://localhost)
