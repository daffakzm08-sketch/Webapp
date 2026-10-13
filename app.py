"""Web app untuk y2mate-api: Flask backend + satu halaman frontend."""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from functools import wraps
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request

from y2mate_api import first_query, session
from y2mate_api.main import utils

app = Flask(__name__)
lock = threading.Lock()  # session curl_cffi dipakai bersama, jadi serialkan akses

VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CONVERT_URL = "https://www.y2mate.com/mates/convertV2/index"
BLOCKED = (
    "y2mate menolak permintaan. Kemungkinan cookie cf_clearance belum diisi "
    "atau sudah kedaluwarsa. Perbarui lewat menu Cookie."
)

if os.getenv("Y2MATE_CF_CLEARANCE"):
    session.cookies.update({"cf_clearance": os.environ["Y2MATE_CF_CLEARANCE"]})


# ---------------------------------------------- Silent refresh cf_clearance
# Cookie diperbarui otomatis lewat FlareSolverr (FLARESOLVERR_URL), tanpa input manual:
# terjadwal di latar belakang, dan otomatis sekali lagi bila y2mate menolak permintaan.
FS_URL = os.getenv("FLARESOLVERR_URL", "").rstrip("/")
REFRESH_MINUTES = int(os.getenv("REFRESH_MINUTES", "25"))
_refresh_lock = threading.Lock()
cf_state = {"tried": 0.0, "ok": False, "at": None, "error": None, "expires": None}


def refresh_cookie():
    """Ambil cf_clearance baru. True bila berhasil. Dibatasi 1x per 30 detik."""
    if not FS_URL:
        return False
    with _refresh_lock:
        if time.time() - cf_state["tried"] < 30:
            return cf_state["ok"]
        cf_state["tried"] = time.time()
        try:
            r = requests.post(
                f"{FS_URL}/v1",
                json={"cmd": "request.get", "url": "https://www.y2mate.com/", "maxTimeout": 60000},
                timeout=90,
            )
            body = r.json()
            sol = body.get("solution") or {}
            ck = next((c for c in sol.get("cookies", []) if c.get("name") == "cf_clearance"), None)
            if not ck:
                raise RuntimeError(body.get("message") or "cf_clearance tidak ditemukan")
            with lock:
                session.cookies.update({"cf_clearance": ck["value"]})
                if sol.get("userAgent"):  # cookie terikat pada User-Agent yang sama
                    session.headers.update({"User-Agent": sol["userAgent"]})
            cf_state.update(ok=True, at=int(time.time()), error=None, expires=ck.get("expires"))
        except Exception as e:  # noqa: BLE001
            cf_state.update(ok=False, error=str(e)[:200])
        return cf_state["ok"]


def _refresher_loop():
    while True:
        ok = refresh_cookie()
        wait = 120  # gagal: coba lagi sebentar lagi
        if ok:
            exp = cf_state.get("expires") or 0
            wait = (exp - time.time()) * 0.8 if exp > 0 else REFRESH_MINUTES * 60
            wait = min(max(wait, 300), 6 * 3600)
        time.sleep(wait)


def run_first(query):
    with lock:
        q1 = first_query(query)()
    if not q1.processed and refresh_cookie():
        with lock:
            q1 = first_query(query)()
    return q1


def run_post(data):
    with lock:
        ok, resp = utils.post(CONVERT_URL, data=data, timeout=30)
    if not ok and refresh_cookie():
        with lock:
            ok, resp = utils.post(CONVERT_URL, data=data, timeout=30)
    return ok, resp


def fail(message, code=400, error="bad_request"):
    return jsonify({"ok": False, "error": error, "message": message}), code


def shape(group):
    return [
        {"k": v.get("k"), "q": v.get("q"), "f": v.get("f"), "size": v.get("size")}
        for v in (group or {}).values()
    ]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    return jsonify(
        {
            "has_cookie": bool(session.cookies.get("cf_clearance")),
            "auto": bool(FS_URL),
            "last_refresh": cf_state["at"],
            "refresh_error": cf_state["error"],
        }
    )


@app.post("/api/cookie")
def set_cookie():
    value = (request.get_json(silent=True) or {}).get("cf_clearance", "").strip()
    if not value or len(value) > 2048 or re.search(r"\s", value):
        return fail("Nilai cf_clearance tidak valid.")
    with lock:
        session.cookies.update({"cf_clearance": value})
    return jsonify({"ok": True})


@app.post("/api/search")
def search():
    q = (request.get_json(silent=True) or {}).get("query", "").strip()
    if not q or len(q) > 300:
        return fail("Masukkan judul, link, atau ID video.")
    q1 = run_first(q)
    if not q1.processed:
        return fail(BLOCKED, 502, "blocked")
    raw = q1.raw
    if "vitems" in raw:  # hasil pencarian kata kunci
        items = [{"vid": i.get("v"), "title": i.get("t")} for i in raw["vitems"]]
        return jsonify({"ok": True, "kind": "search", "items": items})
    # input berupa link/ID: respons langsung berisi daftar format
    return jsonify(
        {
            "ok": True,
            "kind": "video",
            "vid": raw.get("vid"),
            "title": raw.get("title"),
            "video": shape(raw.get("links", {}).get("mp4")),
            "audio": shape(raw.get("links", {}).get("mp3")),
        }
    )


@app.post("/api/formats")
def formats():
    vid = (request.get_json(silent=True) or {}).get("vid", "")
    if not VID_RE.match(vid):
        return fail("ID video tidak valid.")
    q1 = run_first(f"https://www.youtube.com/watch?v={vid}")
    if not q1.processed:
        return fail(BLOCKED, 502, "blocked")
    raw = q1.raw
    links = raw.get("links", {})
    return jsonify(
        {
            "ok": True,
            "vid": raw.get("vid", vid),
            "title": raw.get("title"),
            "video": shape(links.get("mp4")),
            "audio": shape(links.get("mp3")),
        }
    )


@app.post("/api/convert")
def convert():
    body = request.get_json(silent=True) or {}
    vid, k = body.get("vid", ""), body.get("k", "")
    if not VID_RE.match(vid) or not k or len(k) > 1024:
        return fail("Permintaan konversi tidak valid.")
    ok, resp = run_post({"k": k, "vid": vid})
    if not ok:
        return fail(BLOCKED, 502, "blocked")
    data = resp.json()
    return jsonify(
        {
            "ok": True,
            "state": data.get("c_status"),  # CONVERTING / CONVERTED
            "dlink": data.get("dlink"),
            "title": data.get("title"),
            "ftype": data.get("ftype"),
            "fquality": data.get("fquality"),
        }
    )


# ---------------------------------------------------------------- API key
DATA = Path(os.getenv("DATA_DIR", Path(__file__).parent / "data"))
DATA.mkdir(parents=True, exist_ok=True)
KEYS_FILE = DATA / "keys.json"
keys_lock = threading.Lock()


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def _same(a, b):
    return hmac.compare_digest(a.encode(), b.encode())


def load_keys():
    try:
        return json.loads(KEYS_FILE.read_text())
    except Exception:
        return []


def save_keys(keys):
    tmp = KEYS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(keys, indent=2))
    tmp.replace(KEYS_FILE)


def get_admin_password():
    if os.getenv("ADMIN_PASSWORD"):
        return os.environ["ADMIN_PASSWORD"]
    f = DATA / "admin_password.txt"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(12))
    return f.read_text().strip()


ADMIN_PW = get_admin_password()


def require_admin(fn):
    @wraps(fn)
    def inner(*a, **kw):
        if not _same(request.headers.get("X-Admin-Password", ""), ADMIN_PW):
            return fail("Password admin salah.", 401, "unauthorized")
        return fn(*a, **kw)

    return inner


def require_key(fn):
    @wraps(fn)
    def inner(*a, **kw):
        auth = request.headers.get("Authorization", "")
        key = request.headers.get("X-API-Key") or (
            auth[7:] if auth.startswith("Bearer ") else ""
        )
        with keys_lock:
            keys = load_keys()
            hit = next((k for k in keys if key and _same(k["hash"], _hash(key))), None)
            if hit:
                hit["last_used"] = int(time.time())
                save_keys(keys)
        if not hit:
            return fail("API key tidak ada atau tidak valid.", 401, "unauthorized")
        return fn(*a, **kw)

    return inner


@app.get("/admin/keys")
@require_admin
def list_keys():
    keys = [
        {k: v for k, v in e.items() if k != "hash"} for e in load_keys()
    ]
    return jsonify({"ok": True, "keys": keys})


@app.post("/admin/keys")
@require_admin
def create_key():
    name = (request.get_json(silent=True) or {}).get("name", "").strip()[:60]
    key = "y2m_" + secrets.token_urlsafe(32)
    entry = {
        "id": secrets.token_hex(4),
        "name": name or "tanpa nama",
        "hash": _hash(key),
        "prefix": key[:10],
        "created": int(time.time()),
        "last_used": None,
    }
    with keys_lock:
        keys = load_keys()
        keys.append(entry)
        save_keys(keys)
    return jsonify({"ok": True, "key": key, "id": entry["id"]})  # tampil sekali


@app.delete("/admin/keys/<kid>")
@require_admin
def delete_key(kid):
    with keys_lock:
        keys = load_keys()
        left = [k for k in keys if k["id"] != kid]
        save_keys(left)
    return jsonify({"ok": True, "deleted": len(keys) - len(left)})


# Endpoint untuk project lain: sama dengan /api/*, tapi wajib API key.
for _name, _fn in [("search", search), ("formats", formats), ("convert", convert)]:
    app.add_url_rule(f"/v1/{_name}", f"v1_{_name}", require_key(_fn), methods=["POST"])


@app.post("/v1/download")
@require_key
def v1_download():
    """Satu panggilan: link/ID -> dlink. Body: {url, type: video|audio, quality?}"""
    b = request.get_json(silent=True) or {}
    src = str(b.get("url", "")).strip()
    if VID_RE.match(src):
        src = f"https://www.youtube.com/watch?v={src}"
    if not src.startswith(("http://", "https://")):
        return fail("Isi 'url' dengan link atau ID video YouTube.")
    kind = "audio" if b.get("type") == "audio" else "video"
    q1 = run_first(src)
    if not q1.processed:
        return fail(BLOCKED, 502, "blocked")
    raw = q1.raw
    if "vitems" in raw:
        return fail("Berikan link atau ID video, bukan kata kunci.")
    group = raw.get("links", {}).get("mp3" if kind == "audio" else "mp4") or {}
    options = list(group.values())
    want = b.get("quality")
    pick = next((o for o in options if o.get("q") == want), None) if want else (options[0] if options else None)
    if not pick:
        avail = [o.get("q") for o in options]
        return fail(f"Kualitas tidak tersedia. Pilihan: {avail}", 404, "not_found")
    for _ in range(8):
        ok, resp = run_post({"k": pick["k"], "vid": raw["vid"]})
        if not ok:
            return fail(BLOCKED, 502, "blocked")
        d = resp.json()
        if d.get("dlink") and d.get("c_status") != "CONVERTING":
            return jsonify(
                {
                    "ok": True,
                    "vid": raw["vid"],
                    "title": raw.get("title"),
                    "type": kind,
                    "quality": pick.get("q"),
                    "format": pick.get("f"),
                    "size": pick.get("size"),
                    "dlink": d["dlink"],
                }
            )
        time.sleep(4)
    return fail("Konversi terlalu lama, coba lagi.", 504, "timeout")


@app.after_request
def cors(resp):
    if request.path.startswith("/v1/"):
        resp.headers["Access-Control-Allow-Origin"] = os.getenv("CORS_ORIGIN", "*")
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, Authorization"
    return resp


if FS_URL:
    threading.Thread(target=_refresher_loop, daemon=True).start()


if __name__ == "__main__":
    if not os.getenv("ADMIN_PASSWORD"):
        print(f"Password admin (panel API Key): {ADMIN_PW}  [disimpan di {DATA / 'admin_password.txt'}]")
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5000")),
        threaded=True,
    )
