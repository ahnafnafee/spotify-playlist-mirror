"""Account wizard endpoints — connect/inspect each service uniformly."""

from dataclasses import asdict

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse

from ...accounts import CONNECTORS
from ...accounts.base import DeviceCode

router = APIRouter()


def _conn(request: Request, cid: str):
    return CONNECTORS[cid](request.app.state.settings)


def _redirect_uri(request: Request, cid: str) -> str:
    return str(request.base_url).rstrip("/") + f"/oauth/{cid}/callback"


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
    st = _conn(request, cid).complete_redirect({"url": str(request.url)})
    return HTMLResponse(
        f"<body style='font-family:system-ui;padding:2rem'>"
        f"<h2>{CONNECTORS[cid].name}: {st.state}</h2><p>{st.detail}</p>"
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
