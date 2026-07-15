"""Account wizard endpoints — connect/inspect each service uniformly."""

import html
import re
from dataclasses import asdict

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse

from ...engine.targets import is_peer
from ...services.accounts import CONNECTORS
from ...services.accounts.base import ConnStatus, DeviceCode

router = APIRouter()


def _conn(request: Request, cid: str):
    return CONNECTORS[cid](request.app.state.settings)


def _redirect_uri(request: Request, cid: str) -> str:
    base = str(request.base_url).rstrip("/")
    # Spotify (and increasingly others) reject `localhost` for http loopback
    # OAuth redirects — the explicit 127.0.0.1 loopback IP is required over http.
    # Force it here so the redirect works no matter how the app is opened.
    base = re.sub(r"://localhost(?=[:/]|$)", "://127.0.0.1", base, count=1)
    return base + f"/oauth/{cid}/callback"


@router.get("/api/accounts")
def list_accounts(request: Request):
    out = []
    for cid, cls in CONNECTORS.items():
        c = cls(request.app.state.settings)
        st = c.status()
        out.append({
            "id": cid, "name": c.name, "auth_kind": c.auth_kind,
            "fields": [asdict(f) for f in c.config_fields],
            "state": st.state, "detail": st.detail,
            # Browse-only services (Jellyfin) can't be a sync/transfer peer — the
            # UI filters its source/destination pickers on this.
            "transferable": is_peer(cid),
        })
    return out


@router.post("/api/accounts/{cid}/config")
def save_config(cid: str, request: Request, values: dict = Body(...)):
    request.app.state.settings.save(values)
    return {"ok": True}


@router.post("/api/accounts/{cid}/connect")
async def connect(cid: str, request: Request):
    c = _conn(request, cid)
    if c.auth_kind == "oauth_redirect":
        uri = _redirect_uri(request, cid)
        return {"kind": "redirect", "url": c.begin_redirect(uri), "redirect_uri": uri}
    if c.auth_kind == "oauth_device":
        return {"kind": "device", **asdict(c.begin_device())}
    st = c.submit(await request.json())  # token_paste / api_key
    return {"kind": c.auth_kind, "state": st.state, "detail": st.detail}


@router.get("/oauth/{cid}/callback")
def oauth_callback(cid: str, request: Request):
    # The provider can bounce back with ?error=... (the user denied, or a
    # provider-side failure like Spotify's "server_error") instead of a code.
    # Treat that as a failed connection and, likewise, catch any token-exchange
    # error — the callback must never 500 and show a raw "Internal Server Error".
    err = request.query_params.get("error")
    if err:
        st = ConnStatus("error", f"{CONNECTORS[cid].name} returned '{err}' — nothing was authorized.")
    else:
        try:
            st = _conn(request, cid).complete_redirect({"url": str(request.url)})
        except Exception as e:
            st = ConnStatus("error", f"could not finish authorization ({e})")
    return HTMLResponse(
        f"<body style='font-family:system-ui;padding:2rem'>"
        f"<h2>{html.escape(CONNECTORS[cid].name)}: {html.escape(st.state)}</h2>"
        f"<p>{html.escape(st.detail or '')}</p>"
        f"<p>You can close this tab and return to the app.</p></body>"
    )


@router.post("/api/accounts/{cid}/poll")
async def poll(cid: str, request: Request):
    body = await request.json()
    dc = DeviceCode("", "", body["device_code"], body.get("interval", 5))
    st = _conn(request, cid).poll_device(dc)
    return {"state": st.state, "detail": st.detail}


@router.delete("/api/accounts/{cid}")
def disconnect(cid: str, request: Request):
    c = _conn(request, cid)
    request.app.state.settings.save({f.key: "" for f in c.config_fields})  # blanks -> unconfigured
    return {"ok": True}


@router.post("/api/accounts/ytmusic/browser")
async def ytmusic_enable_browser(request: Request, body: dict = Body(...)):
    """Turn on YouTube Music's no-quota (browser cookies) backend from pasted
    music.youtube.com request headers — the fix for large backfills hitting the
    Data API quota."""
    st = _conn(request, "ytmusic").enable_browser(body.get("headers", ""))
    return {"state": st.state, "detail": st.detail}


@router.delete("/api/accounts/ytmusic/browser")
def ytmusic_disable_browser(request: Request):
    """Revert YouTube Music to the durable OAuth Data API."""
    st = _conn(request, "ytmusic").disable_browser()
    return {"state": st.state, "detail": st.detail}
