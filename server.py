"""
AutoPiE（MocoStudio）商業授權原型 HTTP 伺服器
純 Python 3.12 標準庫實作，可直接在本機執行，或透過 Dockerfile 一鍵部署至 Render / Fly.io / Railway / AWS App Runner。

環境變數：
  HOST             預設 0.0.0.0
  PORT             預設 8790（雲端平台會自動注入 PORT）
  PUBLIC_BASE_URL  對外公開網址（例如 https://autopie-license.onrender.com），未設定則自動依請求 Host 推斷
  DATA_DIR         SQLite 與簽章金鑰存放目錄，預設為本程式目錄下的 state/
"""

from __future__ import annotations

import json
import os
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from license_core import LicenseError, LicenseStore
from web_ui import render_admin_page, render_client_page, render_oauth_page

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8790"))
DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent / "state")))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")


def make_handler(store: LicenseStore):
    class LicenseRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[license-proto] {self.address_string()} - {format % args}")

        def _client_ip(self) -> str:
            xff = self.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[0].strip()
            return self.client_address[0]

        def _base_url(self) -> str:
            if os.environ.get("PUBLIC_BASE_URL"):
                return os.environ["PUBLIC_BASE_URL"].rstrip("/")
            proto = self.headers.get("X-Forwarded-Proto", "http")
            host = self.headers.get("Host", f"127.0.0.1:{PORT}")
            return f"{proto}://{host}"

        def _bearer_token(self) -> str:
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            return ""

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def _read_form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else ""
            parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
            return {k: v[0] for k, v in parsed.items()}

        def _send_json(self, status: int, data: dict[str, Any]) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, status: int, content: str) -> None:
            body = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _handle_error(self, err: Exception) -> None:
            if isinstance(err, LicenseError):
                payload = {"code": err.code, "message": err.message, **err.extra}
                self._send_json(err.status, payload)
            else:
                self._send_json(500, {"code": "INTERNAL_ERROR", "message": str(err)})

        def do_OPTIONS(self) -> None:
            self._send_json(200, {"ok": True})

        def do_GET(self) -> None:
            try:
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                qs = urllib.parse.parse_qs(parsed.query)

                if path == "/":
                    self._send_html(200, render_client_page(self._base_url()))
                elif path == "/admin":
                    self._send_html(200, render_admin_page())
                elif path == "/health":
                    self._send_json(200, {"status": "ok", "server_time": store.now()})
                elif path == "/.well-known/jwks.json":
                    self._send_json(200, {"keys": [store.keypair.public_jwk()]})
                elif path == "/oauth/authorize":
                    challenge = qs.get("code_challenge", [""])[0]
                    redirect_uri = qs.get("redirect_uri", [self._base_url() + "/"])[0]
                    overview = store.admin_overview()
                    users = [item["user"] for item in overview["users"]]
                    self._send_html(200, render_oauth_page(challenge, redirect_uri, users))
                elif path == "/v1/account/status":
                    res = store.get_account_status(access_token=self._bearer_token())
                    self._send_json(200, res)
                elif path == "/v1/admin/overview":
                    self._send_json(200, store.admin_overview())
                elif path == "/v1/debug/clock":
                    self._send_json(
                        200,
                        {"server_time": store.now(), "clock_offset_hours": store.get_clock_offset_hours()},
                    )
                else:
                    self._send_json(404, {"code": "NOT_FOUND", "message": f"未知路徑：{path}"})
            except Exception as err:
                self._handle_error(err)

        def do_POST(self) -> None:
            try:
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                ip = self._client_ip()

                if path == "/oauth/authorize":
                    form = self._read_form()
                    res = store.oauth_authorize(
                        email=form.get("email", ""),
                        code_challenge=form.get("code_challenge", ""),
                        redirect_uri=form.get("redirect_uri", self._base_url() + "/"),
                    )
                    sep = "&" if "?" in form.get("redirect_uri", "") else "?"
                    target = f"{form.get('redirect_uri', self._base_url() + '/')}{sep}code={urllib.parse.quote(res['code'])}"
                    self._send_redirect(target)
                    return

                body = self._read_json()

                if path == "/v1/auth/oauth/exchange":
                    res = store.oauth_exchange(
                        code=body.get("code", ""),
                        code_verifier=body.get("code_verifier", ""),
                        ip=ip,
                    )
                    self._send_json(200, res)
                elif path == "/v1/license/activate":
                    res = store.activate_or_transfer(
                        access_token=self._bearer_token(),
                        device_id=body.get("device_id", ""),
                        device_label=body.get("device_label", "MocoStudio PC"),
                        use_emergency_transfer=bool(body.get("use_emergency_transfer", False)),
                        ip=ip,
                    )
                    self._send_json(200, res)
                elif path == "/v1/license/renew":
                    res = store.renew_lease(
                        access_token=self._bearer_token(),
                        device_id=body.get("device_id", ""),
                        offline_grace_used_at=body.get("offline_grace_used_at"),
                        clock_anomaly_flag=bool(body.get("clock_anomaly_flag", False)),
                        ip=ip,
                    )
                    self._send_json(200, res)
                elif path.startswith("/v1/rewards/") and path.endswith("/claim"):
                    reward_id = path[len("/v1/rewards/") : -len("/claim")]
                    res = store.claim_reward(
                        access_token=self._bearer_token(),
                        reward_id=reward_id,
                        ip=ip,
                    )
                    self._send_json(200, res)
                elif path == "/v1/admin/subscriptions":
                    res = store.admin_create_or_activate_subscription(
                        email=body.get("email", ""),
                        display_name=body.get("display_name", ""),
                        plan=body.get("plan", "quarterly"),
                        ip=ip,
                    )
                    self._send_json(200, res)
                elif path == "/v1/admin/rewards/grant":
                    res = store.admin_grant_reward(
                        user_id=body.get("user_id", ""),
                        extend_days=int(body.get("extend_days", 14)),
                        reason=body.get("reason", "官方延長獎勵券"),
                        ip=ip,
                    )
                    self._send_json(200, res)
                elif path == "/v1/admin/rewards/revoke":
                    res = store.admin_revoke_reward(reward_id=body.get("reward_id", ""), ip=ip)
                    self._send_json(200, res)
                elif path == "/v1/admin/subscriptions/past-due":
                    res = store.admin_set_past_due(user_id=body.get("user_id", ""), ip=ip)
                    self._send_json(200, res)
                elif path == "/v1/admin/seats/revoke":
                    res = store.admin_toggle_revoke_seat(
                        seat_id=body.get("seat_id", ""),
                        revoke=bool(body.get("revoke", True)),
                        reason=body.get("reason", "管理員手動撤銷"),
                        ip=ip,
                    )
                    self._send_json(200, res)
                elif path == "/v1/admin/seats/reset-cooldown":
                    res = store.admin_reset_transfer_cooldown(seat_id=body.get("seat_id", ""), ip=ip)
                    self._send_json(200, res)
                elif path == "/v1/debug/clock/advance":
                    ts = store.advance_clock_hours(float(body.get("hours", 0)))
                    self._send_json(200, {"server_time": ts, "clock_offset_hours": store.get_clock_offset_hours()})
                elif path == "/v1/debug/clock/reset":
                    ts = store.reset_clock()
                    self._send_json(200, {"server_time": ts, "clock_offset_hours": 0})
                else:
                    self._send_json(404, {"code": "NOT_FOUND", "message": f"未知路徑：{path}"})
            except Exception as err:
                self._handle_error(err)

    return LicenseRequestHandler


def main() -> None:
    store = LicenseStore(data_dir=DATA_DIR, issuer_url=PUBLIC_BASE_URL)
    server = ThreadingHTTPServer((HOST, PORT), make_handler(store))
    print(f"AutoPiE License Cloud Prototype 已啟動：http://{HOST}:{PORT}")
    print(f"  - 模擬客戶端：   http://127.0.0.1:{PORT}/")
    print(f"  - 營運管理後台： http://127.0.0.1:{PORT}/admin")
    print(f"  - JWKS 公鑰：    http://127.0.0.1:{PORT}/.well-known/jwks.json")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n伺服器已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
