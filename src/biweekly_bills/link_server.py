from __future__ import annotations

from threading import Event

from flask import Flask, jsonify, request
import plaid

from .plaid_sdk import (
    build_client,
    create_link_token,
    create_update_link_token,
    exchange_public_token,
    get_item,
)
from .production_guard import (
    initial_link_session_reserved,
    mark_production_item_created,
    release_initial_link_session,
)
from .production_readiness import (
    assert_initial_production_link_allowed,
    assert_production_update_allowed,
)
from .secure_store import (
    clear_pending_production_exchange,
    load_pending_production_exchange,
    preflight_store,
    save_credentials,
    save_pending_production_exchange,
)
from .settings import Settings


def create_app(
    settings: Settings,
    *,
    update_mode: bool = False,
    access_token: str | None = None,
    completion_event: Event | None = None,
) -> Flask:
    preflight_store()
    if update_mode and not access_token:
        raise RuntimeError("Update mode requires the existing Plaid access token.")

    if settings.environment == "production":
        if update_mode:
            assert_production_update_allowed()
        else:
            if not initial_link_session_reserved():
                raise RuntimeError(
                    "Production Initial Link must be launched through the guarded "
                    "one-session workflow. No Production Link session is reserved."
                )
            assert_initial_production_link_allowed(
                allow_reserved_session=True,
            )

    app = Flask(__name__)
    client = build_client(settings)
    pending: dict[str, str] = {}

    mode_label = "Repair Bank Connection" if update_mode else "Connect Bank"
    mode_text = (
        "This repairs the existing Plaid Item using Link update mode. "
        "The existing access token is retained and no new Item is created."
        if update_mode
        else
        "This creates the one persistent Plaid Item used for read-only Transactions/Balance access. "
        "Do not repeat Production Link after the Item is created."
    )

    @app.get("/")
    @app.get("/oauth-return")
    def index():
        update_js = "true" if update_mode else "false"
        return f'''<!doctype html>
<html><head><meta charset="utf-8"><title>Bi-Weekly Bills — Plaid Link</title>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<style>
body{{font-family:system-ui,sans-serif;max-width:760px;margin:3rem auto;padding:0 1rem;color:#1f2937}}
.card{{border:1px solid #d7e1ea;border-radius:18px;padding:2rem;box-shadow:0 10px 30px rgba(15,43,68,.08)}}
h1{{color:#17365d;margin-top:0}}
button{{padding:.8rem 1.1rem;font-size:1rem;border:0;border-radius:10px;background:#1f4e78;color:white;font-weight:700;cursor:pointer}}
button.secondary{{background:#64748b}}
pre{{background:#f4f7fa;padding:1rem;border-radius:10px;white-space:pre-wrap}}
</style>
</head><body><div class="card">
<h1>Bi-Weekly Bills</h1>
<p>{mode_text}</p>
<button id="link">{mode_label}</button>
<button id="retry" class="secondary" style="display:none">Retry secure save</button>
<pre id="out">Ready.</pre>
</div>
<script>
const updateMode={update_js};
const out=document.getElementById('out');
async function token(){{
 const returning=location.search.includes('oauth_state_id=');
 const saved=sessionStorage.getItem('plaid_link_token');
 if(returning && saved) return saved;
 const r=await fetch('/api/link-token',{{method:'POST'}}); const j=await r.json();
 if(!r.ok) throw new Error(j.error||'Could not create Link token');
 sessionStorage.setItem('plaid_link_token',j.link_token); return j.link_token;
}}
async function openLink(){{
 try {{
  const t=await token();
  const opts={{token:t,onSuccess:async(public_token,metadata)=>{{
   if(updateMode){{
    out.textContent='Update completed. Verifying the existing Item…';
    const r=await fetch('/api/update-success',{{method:'POST'}});
    const j=await r.json();
    if(!r.ok){{out.textContent=j.error||'Update verification failed.';return;}}
    sessionStorage.removeItem('plaid_link_token');
    out.textContent='Success. Existing Item repaired. No new Item or access token was created.\\n\\nYou may close this tab.';
    return;
   }}
   out.textContent='Connected. Persisting the Item credential locally…';
   const r=await fetch('/api/exchange',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{public_token,metadata}})}});
   const j=await r.json();
   if(!r.ok){{out.textContent=j.error||'Secure save failed. Keep this process open.';document.getElementById('retry').style.display='inline-block';return;}}
   sessionStorage.removeItem('plaid_link_token');
   out.textContent='Success. Item credential persisted locally.\\nItem ID: '+j.item_id+'\\n\\nYou may close this tab.';
  }},onExit:async(err)=>{{
   if(err) out.textContent=JSON.stringify(err,null,2);
   try {{ await fetch('/api/cancel',{{method:'POST'}}); }} catch(e) {{}}
  }}}};
  if(location.search.includes('oauth_state_id=')) opts.receivedRedirectUri=location.href;
  Plaid.create(opts).open();
 }} catch(e){{out.textContent=String(e)}}
}}
document.getElementById('link').onclick=openLink;
document.getElementById('retry').onclick=async()=>{{const r=await fetch('/api/retry-save',{{method:'POST'}});const j=await r.json();out.textContent=r.ok?'Credential saved. You may close this tab.':(j.error||'Retry failed');}};
if(location.search.includes('oauth_state_id=')) openLink();
</script></body></html>'''

    @app.post("/api/link-token")
    def api_link_token():
        try:
            if settings.environment == "production":
                if update_mode:
                    assert_production_update_allowed()
                else:
                    if not initial_link_session_reserved():
                        raise RuntimeError(
                            "Production Initial Link reservation is missing. "
                            "Refusing to create a Link token."
                        )
                    assert_initial_production_link_allowed(
                        allow_reserved_session=True,
                    )
            response = (
                create_update_link_token(client, settings, str(access_token))
                if update_mode
                else create_link_token(client, settings)
            )
            return jsonify({"link_token": response["link_token"]})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except plaid.ApiException as exc:
            return jsonify({"error": str(exc)}), 502

    def signal_complete() -> None:
        if completion_event is not None:
            completion_event.set()

    def persist_pending() -> None:
        token = pending.get("access_token")
        item_id = pending.get("item_id")

        if (not token or not item_id) and settings.environment == "production":
            disk_pending = load_pending_production_exchange()
            token = token or str(disk_pending.get("access_token") or "")
            item_id = item_id or str(disk_pending.get("item_id") or "")

        if not token or not item_id:
            raise RuntimeError("No exchanged Plaid credential is waiting to be saved.")

        save_credentials(access_token=token, item_id=item_id, environment=settings.environment)
        if settings.environment == "production":
            mark_production_item_created(item_id)
            clear_pending_production_exchange()
            release_initial_link_session()
        pending.clear()

    @app.post("/api/exchange")
    def api_exchange():
        if update_mode:
            return jsonify({"error": "Token exchange is disabled in update mode."}), 409

        payload = request.get_json(silent=True) or {}
        public_token = str(payload.get("public_token") or "")
        if not public_token:
            return jsonify({"error": "public_token is required"}), 400
        if settings.environment == "production":
            try:
                if not initial_link_session_reserved():
                    raise RuntimeError(
                        "Production Initial Link reservation disappeared before token exchange. "
                        "Refusing to continue."
                    )
                assert_initial_production_link_allowed(
                    allow_reserved_session=True,
                )
            except RuntimeError as exc:
                return jsonify({"error": str(exc)}), 409

        try:
            response = exchange_public_token(client, public_token)
            exchanged_token = str(response["access_token"])
            item_id = str(response["item_id"])
            pending["access_token"] = exchanged_token
            pending["item_id"] = item_id

            if settings.environment == "production":
                mark_production_item_created(item_id)
                save_pending_production_exchange(
                    access_token=exchanged_token,
                    item_id=item_id,
                )

            persist_pending()
            signal_complete()
            return jsonify({"ok": True, "item_id": item_id})
        except plaid.ApiException as exc:
            return jsonify({"error": str(exc)}), 502
        except Exception as exc:
            return jsonify({
                "error": (
                    "Plaid created the Item, but final local credential persistence failed. "
                    "DO NOT run Link again. Use Retry secure save while this window is open, "
                    "or run biweekly-bills recover-production if this was Production. "
                    + str(exc)
                )
            }), 500

    @app.post("/api/retry-save")
    def api_retry_save():
        if update_mode:
            return jsonify({"error": "Retry secure save is not used in update mode."}), 409
        try:
            persist_pending()
            signal_complete()
            return jsonify({"ok": True})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.post("/api/cancel")
    def api_cancel():
        signal_complete()
        return jsonify({"ok": True})

    @app.post("/api/update-success")
    def api_update_success():
        if not update_mode or not access_token:
            return jsonify({"error": "This Link session is not in update mode."}), 409
        try:
            payload = get_item(client, access_token)
            item = payload.get("item") or {}
            error = item.get("error")
            if error:
                return jsonify({"error": f"Plaid Item still reports an error: {error}"}), 409
            signal_complete()
            return jsonify({"ok": True, "item_id": item.get("item_id")})
        except plaid.ApiException as exc:
            return jsonify({"error": str(exc)}), 502

    return app
