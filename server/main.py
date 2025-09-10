# server/main.py
import os, time, hmac, json, sqlite3
from hashlib import sha256
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import stripe

# ==== ENV ====
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
RENTCAST_API_KEY    = os.getenv("RENTCAST_API_KEY")
STRIPE_SECRET_KEY   = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
ALLOWED_ORIGIN      = os.getenv("ALLOWED_ORIGIN", "https://superiorllc.org")

if not (GOOGLE_MAPS_API_KEY and RENTCAST_API_KEY and STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET):
    raise RuntimeError("Missing required env vars: GOOGLE_MAPS_API_KEY, RENTCAST_API_KEY, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_SECRET_KEY

# Map product identification -> internal package code
# You can match by product name, price, amount_total, or payment_link id.
PACKAGE_MATCHERS = {
    "snapshot": {"amount": 2900, "label_keywords": ["Snapshot"]},
    "investor": {"amount": 7900, "label_keywords": ["Investor"]},
    "consult":  {"amount": 19900, "label_keywords": ["Consultation", "Full"]},
}

DB_PATH = os.getenv("PURCHASE_DB", "/tmp/purchases.db")

# ==== DB SETUP ====
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        package TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )""")
    conn.commit()
    return conn

def record_purchase(email: str, package: str):
    conn = db()
    conn.execute("INSERT INTO purchases (email, package, created_at) VALUES (?, ?, ?)", (email.lower(), package, int(time.time())))
    conn.commit(); conn.close()

def has_recent_purchase(email: str, package: str, days: int = 30) -> bool:
    cutoff = int(time.time()) - days * 86400
    conn = db()
    cur = conn.execute("SELECT 1 FROM purchases WHERE email=? AND package=? AND created_at >= ? LIMIT 1",
                       (email.lower(), package, cutoff))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

# ==== APP ====
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==== MODELS ====
class FullReportReq(BaseModel):
    email: str
    package: str
    address: str
    lat: float
    lng: float

# ==== HELPERS ====
async def get_json(client: httpx.AsyncClient, url: str, headers: Optional[dict] = None):
    r = await client.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()

# ==== GOOGLE ENDPOINTS (server-side key) ====
@app.get("/api/geocode")
async def geocode(address: str):
    async with httpx.AsyncClient() as client:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": GOOGLE_MAPS_API_KEY}
        r = await client.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "OK" or not data.get("results"):
            raise HTTPException(400, "No results")
        res = data["results"][0]
        comps = res["address_components"]
        def comp(kind):
            for c in comps:
                if kind in c["types"]:
                    return c
            return None
        admin1 = comp("administrative_area_level_1")
        state = admin1["short_name"] if admin1 else ""
        city = (comp("locality") or comp("postal_town") or comp("administrative_area_level_3"))
        county = comp("administrative_area_level_2")
        loc = res["geometry"]["location"]
        return {
            "formatted_address": res["formatted_address"],
            "lat": loc["lat"], "lng": loc["lng"],
            "city": city["long_name"] if city else None,
            "county": county["long_name"] if county else None,
            "state": state or "VA"  # fallback
        }

@app.get("/api/static-map")
async def static_map(lat: float, lng: float):
    url = "https://maps.googleapis.com/maps/api/staticmap"
    params = {
        "key": GOOGLE_MAPS_API_KEY, "center": f"{lat},{lng}", "zoom": "16",
        "size": "640x400", "maptype": "roadmap", "markers": f"color:blue|{lat},{lng}",
        "style": "feature:poi|visibility:off"
    }
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params, timeout=20)
        r.raise_for_status()
        return Response(content=r.content, media_type="image/png")

@app.get("/api/street-view")
async def street_view(lat: float, lng: float):
    url = "https://maps.googleapis.com/maps/api/streetview"
    params = {"key": GOOGLE_MAPS_API_KEY, "size": "640x400", "location": f"{lat},{lng}", "fov":"80","pitch":"0","heading":"0"}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, params=params, timeout=20)
        r.raise_for_status()
        return Response(content=r.content, media_type="image/jpeg")

# ==== RENTCAST (server-side key) ====
@app.get("/api/rentcast/estimate")
async def rentcast_estimate(address: str):
    async with httpx.AsyncClient() as client:
        url = "https://api.rentcast.io/v1/rental-estimates"
        headers = {"X-Api-Key": RENTCAST_API_KEY}
        r = await client.get(url, params={"address": address}, headers=headers, timeout=20)
        r.raise_for_status()
        return r.json()

@app.get("/api/rentcast/comps")
async def rentcast_comps(lat: float, lng: float, radius: float = 1.0, limit: int = 6):
    async with httpx.AsyncClient() as client:
        url = "https://api.rentcast.io/v1/rental-comps"
        headers = {"X-Api-Key": RENTCAST_API_KEY}
        r = await client.get(url, params={"latitude": lat, "longitude": lng, "radius": radius, "limit": limit}, headers=headers, timeout=20)
        r.raise_for_status()
        return r.json()

# ==== STRIPE WEBHOOK (records purchases) ====
@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload=payload, sig_header=sig, secret=STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(400, f"Webhook signature verification failed: {e}")

    # We expect checkout.session.completed for Payment Links
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = (session.get("customer_details") or {}).get("email")
        amount_total = session.get("amount_total")  # in cents
        package = None

        # Try to inspect line items (more reliable than amount)
        try:
            line_items = stripe.checkout.Session.list_line_items(session["id"], limit=10)
            labels = " ".join([ (li.get("description") or "") for li in line_items.data ])
        except Exception:
            labels = ""

        # Match to a package
        for code, cfg in PACKAGE_MATCHERS.items():
            if amount_total == cfg["amount"]:
                package = code
            if not package and labels:
                if any(kw.lower() in labels.lower() for kw in cfg["label_keywords"]):
                    package = code

        if email and package:
            record_purchase(email, package)

    return {"received": True}

# ==== FULL REPORT (requires recorded purchase) ====
@app.post("/api/full-report")
async def full_report(req: FullReportReq):
    # Validate purchase via recorded webhook
    if not has_recent_purchase(req.email, req.package, days=30):
        raise HTTPException(402, "Purchase not found yet")

    async with httpx.AsyncClient() as client:
        # Fetch RentCast estimate + comps
        headers = {"X-Api-Key": RENTCAST_API_KEY}
        est_url = "https://api.rentcast.io/v1/rental-estimates"
        comps_url = "https://api.rentcast.io/v1/rental-comps"

        est_task = client.get(est_url, params={"address": req.address}, headers=headers, timeout=20)
        comps_task = client.get(comps_url, params={"latitude": req.lat, "longitude": req.lng, "radius": 1, "limit": 6}, headers=headers, timeout=20)

        est_res, comps_res = await httpx.AsyncClient.gather(est_task, comps_task)  # type: ignore

        if est_res.status_code >= 400: raise HTTPException(500, "RentCast estimate failed")
        if comps_res.status_code >= 400: raise HTTPException(500, "RentCast comps failed")

        estimate = est_res.json()
        comps = comps_res.json()

    return {"estimate": estimate, "comps": comps}
