# Superior Property Reports – Starter Repo

Production-safe setup for Superior Consultation LLC:
- **Front-end** (`web/index.html`) – uses your Stripe Payment Links, never exposes API keys.
- **Backend** (`server/main.py`) – FastAPI service that keeps Google Maps + RentCast + Stripe keys **server-side**.
- **Stripe webhook** records purchases, and the `/api/full-report` endpoint returns data only for verified buyers.
- **Render deploy** via `render.yaml` (Python environment), with a persistent disk for purchase records.

## Quick Start (Render, no Docker)

1. **Create a new GitHub repo** and upload this folder.
2. **Create a new Web Service on Render** connected to your repo.
   - Build Command: `pip install -r server/requirements.txt`
   - Start Command: `uvicorn server.main:app --host 0.0.0.0 --port $PORT`
3. **Environment Variables (Render → Environment):**
   - `GOOGLE_MAPS_API_KEY` = (your Google Maps key)
   - `RENTCAST_API_KEY`    = (your RentCast key)
   - `STRIPE_SECRET_KEY`   = (your Stripe secret key)
   - `STRIPE_WEBHOOK_SECRET` = (Stripe → Developers → Webhooks → Signing secret)
   - `ALLOWED_ORIGIN`      = `https://superiorllc.org`
   - `PURCHASE_DB`         = `/data/purchases.db`
4. **Add a persistent disk** (1–2 GB) mounted at `/data`.
5. **Deploy** and note the API URL (e.g., `https://superior-api.onrender.com`).
6. **Set up Stripe webhook** to `https://<YOUR-API>/api/stripe/webhook` with event `checkout.session.completed`.

## Front-End

Edit `web/index.html`:
- Set `const API = "https://api.superiorllc.org"` (or your Render URL) near the bottom.
- The footer already uses `https://superiorllc.org/policies`.

## Local Run (for testing)

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GOOGLE_MAPS_API_KEY=...
export RENTCAST_API_KEY=...
export STRIPE_SECRET_KEY=...
export STRIPE_WEBHOOK_SECRET=...
export ALLOWED_ORIGIN=http://127.0.0.1:5500
export PURCHASE_DB=/tmp/purchases.db
uvicorn main:app --reload
```

Serve `web/index.html` with a simple static server (VS Code Live Server or `python -m http.server`), then set `API` to your local FastAPI URL (e.g., `http://127.0.0.1:8000`).

## Files

```
superior-property-reports-starter/
├─ web/
│  └─ index.html
├─ server/
│  ├─ main.py
│  ├─ requirements.txt
│  └─ __init__.py
├─ render.yaml
├─ .env.example
└─ .gitignore
```

**Security:** Never commit real keys. Use Render environment variables or a secrets manager.
