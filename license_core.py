"""
AutoPiE（MocoStudio）商業授權原型伺服器 — 核心業務邏輯與 SQLite 資料層
完整實作 COMMERCIAL_LICENSE_DESIGN_20261126.md 定案規格，支援本機與雲端部署。
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from crypto_rs256 import RsaKeyPair, b64url_encode

LEASE_SECONDS = 72 * 3600              # 日常滑動租約：72 小時
GRACE_EXTEND_HOURS = 24                # 本機緊急延長：+24 小時
GRACE_COOLDOWN_SECONDS = 30 * 86400    # 緊急延長冷卻：30 天
TRANSFER_COOLDOWN_SECONDS = 144 * 3600 # 正常換機冷卻：144 小時（6 天）
EMERGENCY_SWAP_WINDOW_SECONDS = 90 * 86400  # 緊急換機視窗：90 天（每 3 個月 1 次，不累加）
DUNNING_GRACE_DAYS = 4                 # 付款失敗寬限期：4 天

PLAN_DAYS = {
    "quarterly": 90,   # 3 個月期
    "annual": 365,     # 1 年期
}


class LicenseError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


class LicenseStore:
    def __init__(self, data_dir: Path, issuer_url: str = "http://127.0.0.1:8790"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.issuer_url = issuer_url.rstrip("/")
        self.db_path = self.data_dir / "license_prototype.db"
        self.keypair = RsaKeyPair.load_or_create(self.data_dir / "kms_signing_key.json")
        self._lock = threading.RLock()
        self._clock_offset_seconds = 0
        self._init_db()
        self._seed_demo_data()

    def now(self) -> int:
        return int(time.time()) + self._clock_offset_seconds

    def get_clock_offset_hours(self) -> float:
        return round(self._clock_offset_seconds / 3600.0, 2)

    def advance_clock_hours(self, hours: float) -> int:
        with self._lock:
            self._clock_offset_seconds += int(hours * 3600)
            return self.now()

    def reset_clock(self) -> int:
        with self._lock:
            self._clock_offset_seconds = 0
            return self.now()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except LicenseError:
            # 業務拒絕（如冷卻攔截、撤銷拒絕）前寫入的風控稽核日誌必須保留
            conn.commit()
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    first_sub_reward_issued INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    plan TEXT NOT NULL,              -- quarterly | annual
                    status TEXT NOT NULL,            -- active | past_due | canceled | expired | refunded
                    current_period_start INTEGER NOT NULL,
                    current_period_end INTEGER NOT NULL,
                    grace_until INTEGER,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS seats (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
                    user_id TEXT NOT NULL REFERENCES users(id),
                    active_device_id TEXT,
                    active_device_label TEXT,
                    last_transfer_at INTEGER,
                    last_emergency_transfer_at INTEGER,
                    last_offline_grace_at INTEGER,
                    revoked_at INTEGER,
                    revoke_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS oauth_codes (
                    code TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    code_challenge TEXT NOT NULL,
                    redirect_uri TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    access_token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS account_rewards (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    extend_days INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,            -- available | claimed | expired | revoked
                    claim_before INTEGER,
                    claimed_at INTEGER,
                    claimed_sub_id TEXT,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscription_adjustments (
                    id TEXT PRIMARY KEY,
                    subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
                    source_type TEXT NOT NULL,       -- reward | manual | clawback
                    source_id TEXT,
                    delta_days INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS license_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    seat_id TEXT,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'info',  -- info | warn | alert
                    old_device_id TEXT,
                    new_device_id TEXT,
                    ip_address TEXT,
                    detail_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                """
            )

    def _seed_demo_data(self) -> None:
        """若資料庫為空，自動建立一個預設示範租戶，方便立刻測試。"""
        with self._lock, self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if count > 0:
                return
        self.admin_create_or_activate_subscription(
            email="studio-demo@autopie.io",
            display_name="Demo Motion Studio",
            plan="annual",
            ip="127.0.0.1",
        )

    def _log(
        self,
        conn: sqlite3.Connection,
        *,
        user_id: str | None,
        seat_id: str | None,
        event_type: str,
        severity: str = "info",
        old_device_id: str | None = None,
        new_device_id: str | None = None,
        ip_address: str = "127.0.0.1",
        detail: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO license_audit_logs
                (user_id, seat_id, event_type, severity, old_device_id, new_device_id, ip_address, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                seat_id,
                event_type,
                severity,
                old_device_id,
                new_device_id,
                ip_address,
                json.dumps(detail or {}, ensure_ascii=False),
                self.now(),
            ),
        )

    def _sync_expirations(self, conn: sqlite3.Connection, user_id: str | None = None) -> None:
        """同步訂閱到期狀態，並將訂閱已到期的帳號內未領取獎勵券標記為 expired。"""
        ts = self.now()
        where_u = " AND user_id = ?" if user_id else ""
        params_u: tuple[Any, ...] = (user_id,) if user_id else ()

        # 一般 active 訂閱到期 → expired
        conn.execute(
            f"UPDATE subscriptions SET status = 'expired' WHERE status = 'active' AND current_period_end <= ?{where_u}",
            (ts, *params_u),
        )
        # 寬限期 past_due 到期 → expired
        conn.execute(
            f"UPDATE subscriptions SET status = 'expired' WHERE status = 'past_due' AND COALESCE(grace_until, current_period_end) <= ?{where_u}",
            (ts, *params_u),
        )
        # 超過領取截止日的獎勵券 → expired
        conn.execute(
            f"UPDATE account_rewards SET status = 'expired' WHERE status = 'available' AND claim_before IS NOT NULL AND claim_before <= ?{where_u}",
            (ts, *params_u),
        )
        # 規則：訂閱已到期時，帳號內未領取的獎勵券自動失效
        conn.execute(
            f"""
            UPDATE account_rewards
            SET status = 'expired'
            WHERE status = 'available'
              AND NOT EXISTS (
                  SELECT 1 FROM subscriptions s
                  WHERE s.user_id = account_rewards.user_id
                    AND s.status IN ('active', 'past_due')
              )
              {where_u}
            """,
            params_u,
        )

    def _get_latest_sub_and_seat(
        self, conn: sqlite3.Connection, user_id: str
    ) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
        sub = conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not sub:
            return None, None
        seat = conn.execute(
            "SELECT * FROM seats WHERE subscription_id = ? LIMIT 1",
            (sub["id"],),
        ).fetchone()
        return sub, seat

    def _can_use_offline_grace(self, seat: sqlite3.Row, ts: int) -> bool:
        last_g = seat["last_offline_grace_at"]
        return last_g is None or (ts - last_g) >= GRACE_COOLDOWN_SECONDS

    def _emergency_transfer_remaining(self, seat: sqlite3.Row, ts: int) -> int:
        last_et = seat["last_emergency_transfer_at"]
        if last_et is None or (ts - last_et) >= EMERGENCY_SWAP_WINDOW_SECONDS:
            return 1
        return 0

    def _issue_lease_jwt(
        self,
        conn: sqlite3.Connection,
        user: sqlite3.Row,
        sub: sqlite3.Row,
        seat: sqlite3.Row,
        device_id: str,
    ) -> tuple[str, dict[str, Any]]:
        ts = self.now()
        if sub["status"] == "past_due":
            hard_end = sub["grace_until"] or sub["current_period_end"]
            lease_exp = min(ts + 24 * 3600, hard_end)
        else:
            hard_end = sub["current_period_end"]
            lease_exp = min(ts + LEASE_SECONDS, hard_end)

        if lease_exp <= ts:
            raise LicenseError(403, "SUBSCRIPTION_EXPIRED", "訂閱已到期，請續訂後再登入。")

        avail_rewards = conn.execute(
            "SELECT COUNT(*) FROM account_rewards WHERE user_id = ? AND status = 'available'",
            (user["id"],),
        ).fetchone()[0]

        payload = {
            "iss": self.issuer_url,
            "aud": "mocostudio-client",
            "sub": user["id"],
            "email": user["email"],
            "name": user["display_name"],
            "seat_id": seat["id"],
            "device": device_id,
            "plan": sub["plan"],
            "sub_status": sub["status"],
            "sub_end": sub["current_period_end"],
            "iat": ts,
            "nbf": ts,
            "exp": lease_exp,
            "server_time": ts,
            "login_hours": 72,
            "grace_available": self._can_use_offline_grace(seat, ts) and sub["status"] == "active",
            "grace_hours": GRACE_EXTEND_HOURS,
            "emergency_transfer_remaining": self._emergency_transfer_remaining(seat, ts),
            "available_rewards_count": avail_rewards,
        }
        return self.keypair.sign_jwt(payload), payload

    # ==================== Admin 管理操作 ====================

    def admin_create_or_activate_subscription(
        self,
        *,
        email: str,
        display_name: str,
        plan: str,
        ip: str = "127.0.0.1",
    ) -> dict[str, Any]:
        email = email.strip().lower()
        if not email or "@" not in email:
            raise LicenseError(400, "INVALID_EMAIL", "請輸入有效的 Email。")
        if plan not in PLAN_DAYS:
            raise LicenseError(400, "INVALID_PLAN", "方案必須為 quarterly（3 個月）或 annual（1 年）。")

        with self._lock, self._connect() as conn:
            ts = self.now()
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if not user:
                user_id = f"usr_{secrets.token_hex(6)}"
                conn.execute(
                    "INSERT INTO users (id, email, display_name, first_sub_reward_issued, created_at) VALUES (?, ?, ?, 0, ?)",
                    (user_id, email, display_name.strip() or email.split("@")[0], ts),
                )
                user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            else:
                user_id = user["id"]

            self._sync_expirations(conn, user_id)
            days = PLAN_DAYS[plan]
            sub_id = f"sub_{secrets.token_hex(6)}"
            seat_id = f"seat_{secrets.token_hex(6)}"

            # 若已有舊訂閱，將舊訂閱標為 canceled 並延續換機紀錄以防重開洗冷卻
            old_sub, old_seat = self._get_latest_sub_and_seat(conn, user_id)
            if old_sub and old_sub["status"] in ("active", "past_due"):
                conn.execute("UPDATE subscriptions SET status = 'canceled' WHERE id = ?", (old_sub["id"],))

            conn.execute(
                """
                INSERT INTO subscriptions
                    (id, user_id, plan, status, current_period_start, current_period_end, grace_until, created_at)
                VALUES (?, ?, ?, 'active', ?, ?, NULL, ?)
                """,
                (sub_id, user_id, plan, ts, ts + days * 86400, ts),
            )
            conn.execute(
                """
                INSERT INTO seats
                    (id, subscription_id, user_id, active_device_id, active_device_label,
                     last_transfer_at, last_emergency_transfer_at, last_offline_grace_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seat_id,
                    sub_id,
                    user_id,
                    old_seat["active_device_id"] if old_seat else None,
                    old_seat["active_device_label"] if old_seat else None,
                    old_seat["last_transfer_at"] if old_seat else None,
                    old_seat["last_emergency_transfer_at"] if old_seat else None,
                    old_seat["last_offline_grace_at"] if old_seat else None,
                ),
            )

            reward_issued = False
            if user["first_sub_reward_issued"] == 0:
                rew_id = f"rew_{secrets.token_hex(6)}"
                conn.execute(
                    """
                    INSERT INTO account_rewards
                        (id, user_id, extend_days, reason, status, claim_before, created_at)
                    VALUES (?, ?, 14, '新用戶首購禮（14 天延長獎勵券）', 'available', NULL, ?)
                    """,
                    (rew_id, user_id, ts),
                )
                conn.execute("UPDATE users SET first_sub_reward_issued = 1 WHERE id = ?", (user_id,))
                reward_issued = True

            self._log(
                conn,
                user_id=user_id,
                seat_id=seat_id,
                event_type="subscription_activated",
                ip_address=ip,
                detail={"plan": plan, "days": days, "new_user_reward_14d": reward_issued},
            )
            return {
                "user_id": user_id,
                "email": email,
                "subscription_id": sub_id,
                "seat_id": seat_id,
                "plan": plan,
                "new_user_reward_issued": reward_issued,
            }

    def admin_grant_reward(
        self, *, user_id: str, extend_days: int, reason: str, ip: str = "127.0.0.1"
    ) -> dict[str, Any]:
        if extend_days not in (14, 30) and not (1 <= extend_days <= 90):
            raise LicenseError(400, "INVALID_DAYS", "延長天數需介於 1～90 天（常用 14 或 30 天）。")
        with self._lock, self._connect() as conn:
            self._sync_expirations(conn, user_id)
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                raise LicenseError(404, "USER_NOT_FOUND", "找不到該使用者。")
            sub, seat = self._get_latest_sub_and_seat(conn, user_id)
            if not sub or sub["status"] not in ("active", "past_due"):
                raise LicenseError(400, "NO_ACTIVE_SUB", "該帳號目前無有效訂閱，無法發放延長獎勵券。")
            ts = self.now()
            rew_id = f"rew_{secrets.token_hex(6)}"
            conn.execute(
                """
                INSERT INTO account_rewards
                    (id, user_id, extend_days, reason, status, claim_before, created_at)
                VALUES (?, ?, ?, ?, 'available', NULL, ?)
                """,
                (rew_id, user_id, extend_days, reason.strip() or f"官方獎勵（{extend_days} 天延長券）", ts),
            )
            self._log(
                conn,
                user_id=user_id,
                seat_id=seat["id"] if seat else None,
                event_type="reward_granted",
                ip_address=ip,
                detail={"reward_id": rew_id, "extend_days": extend_days, "reason": reason},
            )
            return {"reward_id": rew_id, "user_id": user_id, "extend_days": extend_days}

    def admin_revoke_reward(self, *, reward_id: str, ip: str = "127.0.0.1") -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            rew = conn.execute("SELECT * FROM account_rewards WHERE id = ?", (reward_id,)).fetchone()
            if not rew:
                raise LicenseError(404, "REWARD_NOT_FOUND", "找不到該獎勵券。")
            if rew["status"] != "available":
                raise LicenseError(409, "REWARD_NOT_AVAILABLE", f"此獎勵券目前狀態為 {rew['status']}，無法撤銷。")
            conn.execute("UPDATE account_rewards SET status = 'revoked' WHERE id = ?", (reward_id,))
            self._log(
                conn,
                user_id=rew["user_id"],
                seat_id=None,
                event_type="reward_revoked",
                severity="warn",
                ip_address=ip,
                detail={"reward_id": reward_id},
            )
            return {"reward_id": reward_id, "status": "revoked"}

    def admin_set_past_due(self, *, user_id: str, ip: str = "127.0.0.1") -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            self._sync_expirations(conn, user_id)
            sub, seat = self._get_latest_sub_and_seat(conn, user_id)
            if not sub or sub["status"] != "active":
                raise LicenseError(400, "NOT_ACTIVE", "目前沒有進行中的 active 訂閱。")
            ts = self.now()
            grace_until = ts + DUNNING_GRACE_DAYS * 86400
            conn.execute(
                "UPDATE subscriptions SET status = 'past_due', grace_until = ? WHERE id = ?",
                (grace_until, sub["id"]),
            )
            self._log(
                conn,
                user_id=user_id,
                seat_id=seat["id"] if seat else None,
                event_type="billing_past_due_simulated",
                severity="warn",
                ip_address=ip,
                detail={"grace_until": grace_until, "grace_days": DUNNING_GRACE_DAYS},
            )
            return {"subscription_id": sub["id"], "status": "past_due", "grace_until": grace_until}

    def admin_toggle_revoke_seat(
        self, *, seat_id: str, revoke: bool, reason: str = "管理員手動撤銷", ip: str = "127.0.0.1"
    ) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            seat = conn.execute("SELECT * FROM seats WHERE id = ?", (seat_id,)).fetchone()
            if not seat:
                raise LicenseError(404, "SEAT_NOT_FOUND", "找不到該席位。")
            ts = self.now()
            if revoke:
                conn.execute("UPDATE seats SET revoked_at = ?, revoke_reason = ? WHERE id = ?", (ts, reason, seat_id))
                self._log(
                    conn,
                    user_id=seat["user_id"],
                    seat_id=seat_id,
                    event_type="seat_revoked",
                    severity="alert",
                    ip_address=ip,
                    detail={"reason": reason},
                )
            else:
                conn.execute("UPDATE seats SET revoked_at = NULL, revoke_reason = NULL WHERE id = ?", (seat_id,))
                self._log(
                    conn,
                    user_id=seat["user_id"],
                    seat_id=seat_id,
                    event_type="seat_restored",
                    severity="info",
                    ip_address=ip,
                    detail={},
                )
            return {"seat_id": seat_id, "revoked": revoke}

    def admin_reset_transfer_cooldown(self, *, seat_id: str, ip: str = "127.0.0.1") -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            seat = conn.execute("SELECT * FROM seats WHERE id = ?", (seat_id,)).fetchone()
            if not seat:
                raise LicenseError(404, "SEAT_NOT_FOUND", "找不到該席位。")
            conn.execute("UPDATE seats SET last_transfer_at = NULL WHERE id = ?", (seat_id,))
            self._log(
                conn,
                user_id=seat["user_id"],
                seat_id=seat_id,
                event_type="admin_transfer_cooldown_reset",
                severity="warn",
                ip_address=ip,
                detail={"note": "客服人工特批解除 144 小時換機冷卻"},
            )
            return {"seat_id": seat_id, "cooldown_reset": True}

    def admin_overview(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            self._sync_expirations(conn)
            ts = self.now()
            users = conn.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
            result_users = []
            for u in users:
                sub, seat = self._get_latest_sub_and_seat(conn, u["id"])
                rewards = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM account_rewards WHERE user_id = ? ORDER BY created_at DESC",
                        (u["id"],),
                    ).fetchall()
                ]
                sub_dict = dict(sub) if sub else None
                seat_dict = dict(seat) if seat else None
                if seat_dict:
                    seat_dict["emergency_transfer_remaining"] = self._emergency_transfer_remaining(seat, ts)
                    seat_dict["grace_available"] = self._can_use_offline_grace(seat, ts)
                    if seat["last_transfer_at"]:
                        rem = max(0, TRANSFER_COOLDOWN_SECONDS - (ts - seat["last_transfer_at"]))
                        seat_dict["transfer_cooldown_remaining_seconds"] = rem
                    else:
                        seat_dict["transfer_cooldown_remaining_seconds"] = 0
                result_users.append(
                    {
                        "user": dict(u),
                        "subscription": sub_dict,
                        "seat": seat_dict,
                        "rewards": rewards,
                    }
                )
            logs = [
                {**dict(row), "detail": json.loads(row["detail_json"])}
                for row in conn.execute(
                    "SELECT * FROM license_audit_logs ORDER BY id DESC LIMIT 80"
                ).fetchall()
            ]
            return {
                "server_time": ts,
                "clock_offset_hours": self.get_clock_offset_hours(),
                "users": result_users,
                "audit_logs": logs,
            }

    # ==================== OAuth 2.0 PKCE 登入模擬 ====================

    def oauth_authorize(self, *, email: str, code_challenge: str, redirect_uri: str) -> dict[str, Any]:
        email = email.strip().lower()
        if not code_challenge or len(code_challenge) < 16:
            raise LicenseError(400, "INVALID_PKCE", "缺少有效的 PKCE code_challenge。")
        with self._lock, self._connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if not user:
                raise LicenseError(404, "ACCOUNT_NOT_FOUND", f"找不到帳號 {email}，請先在管理後台開通訂閱。")
            code = f"ac_{secrets.token_urlsafe(24)}"
            ts = self.now()
            conn.execute(
                """
                INSERT INTO oauth_codes (code, user_id, code_challenge, redirect_uri, expires_at, used)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (code, user["id"], code_challenge, redirect_uri, ts + 300),
            )
            return {"code": code, "user_id": user["id"], "email": user["email"]}

    def oauth_exchange(self, *, code: str, code_verifier: str, ip: str = "127.0.0.1") -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM oauth_codes WHERE code = ?", (code,)).fetchone()
            ts = self.now()
            if not row or row["used"] == 1 or row["expires_at"] <= ts:
                raise LicenseError(400, "INVALID_AUTH_CODE", "授權碼無效或已過期，請重新登入。")
            computed = b64url_encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
            if not secrets.compare_digest(computed, row["code_challenge"]):
                raise LicenseError(400, "PKCE_MISMATCH", "PKCE 驗證失敗（code_verifier 不符）。")
            conn.execute("UPDATE oauth_codes SET used = 1 WHERE code = ?", (code,))
            token = f"at_{secrets.token_urlsafe(32)}"
            conn.execute(
                "INSERT INTO sessions (access_token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (token, row["user_id"], ts + 365 * 86400, ts),
            )
            user = conn.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchone()
            self._log(
                conn,
                user_id=user["id"],
                seat_id=None,
                event_type="oauth_login_success",
                ip_address=ip,
                detail={"email": user["email"]},
            )
            return {
                "access_token": token,
                "token_type": "Bearer",
                "user": dict(user),
            }

    def _require_user_by_token(self, conn: sqlite3.Connection, token: str) -> sqlite3.Row:
        if not token:
            raise LicenseError(401, "UNAUTHORIZED", "請先完成登入。")
        ts = self.now()
        sess = conn.execute(
            "SELECT * FROM sessions WHERE access_token = ? AND expires_at > ?",
            (token, ts),
        ).fetchone()
        if not sess:
            raise LicenseError(401, "SESSION_EXPIRED", "登入憑證已失效，請重新透過瀏覽器登入。")
        user = conn.execute("SELECT * FROM users WHERE id = ?", (sess["user_id"],)).fetchone()
        if not user:
            raise LicenseError(401, "USER_NOT_FOUND", "帳號不存在。")
        return user

    # ==================== 用戶端啟用、換機、續期 ====================

    def activate_or_transfer(
        self,
        *,
        access_token: str,
        device_id: str,
        device_label: str = "Studio PC",
        use_emergency_transfer: bool = False,
        ip: str = "127.0.0.1",
    ) -> dict[str, Any]:
        device_id = device_id.strip().upper()
        if len(device_id) < 8:
            raise LicenseError(400, "INVALID_DEVICE_ID", "裝置 ID 格式不正確。")

        with self._lock, self._connect() as conn:
            user = self._require_user_by_token(conn, access_token)
            self._sync_expirations(conn, user["id"])
            sub, seat = self._get_latest_sub_and_seat(conn, user["id"])
            if not sub or not seat:
                raise LicenseError(403, "NO_SUBSCRIPTION", "此帳號尚未訂閱。")
            if seat["revoked_at"] is not None:
                raise LicenseError(403, "SEAT_REVOKED", f"此授權席位已被撤銷：{seat['revoke_reason'] or '請聯繫客服'}。")
            if sub["status"] not in ("active", "past_due"):
                raise LicenseError(403, "SUBSCRIPTION_EXPIRED", "訂閱已到期，請續訂後再使用。")

            ts = self.now()
            old_dev = seat["active_device_id"]
            event_type = "device_reactivated"
            transfer_notice = None

            if old_dev is None:
                # 首次綁機：不觸發 144 小時冷卻
                conn.execute(
                    "UPDATE seats SET active_device_id = ?, active_device_label = ? WHERE id = ?",
                    (device_id, device_label, seat["id"]),
                )
                event_type = "first_activation"
            elif old_dev != device_id:
                # 換機流程
                last_t = seat["last_transfer_at"]
                in_cooldown = last_t is not None and (ts - last_t) < TRANSFER_COOLDOWN_SECONDS
                if in_cooldown:
                    rem_sec = TRANSFER_COOLDOWN_SECONDS - (ts - last_t)
                    et_rem = self._emergency_transfer_remaining(seat, ts)
                    if not use_emergency_transfer:
                        self._log(
                            conn,
                            user_id=user["id"],
                            seat_id=seat["id"],
                            event_type="transfer_cooldown_blocked",
                            severity="warn",
                            old_device_id=old_dev,
                            new_device_id=device_id,
                            ip_address=ip,
                            detail={"cooldown_remaining_hours": round(rem_sec / 3600, 1), "emergency_remaining": et_rem},
                        )
                        raise LicenseError(
                            409,
                            "TRANSFER_COOLDOWN",
                            f"距離上次換機仍在 144 小時冷卻期內（剩餘 {rem_sec // 3600} 小時 {(rem_sec % 3600) // 60} 分鐘）。",
                            {
                                "cooldown_remaining_seconds": rem_sec,
                                "emergency_transfer_remaining": et_rem,
                                "old_device_id": old_dev,
                                "old_device_label": seat["active_device_label"],
                            },
                        )
                    # 嘗試使用免冷卻緊急換機（每 90 天 1 次，不累加）
                    if et_rem <= 0:
                        next_avail = seat["last_emergency_transfer_at"] + EMERGENCY_SWAP_WINDOW_SECONDS
                        self._log(
                            conn,
                            user_id=user["id"],
                            seat_id=seat["id"],
                            event_type="emergency_transfer_denied",
                            severity="alert",
                            old_device_id=old_dev,
                            new_device_id=device_id,
                            ip_address=ip,
                            detail={"next_available_at": next_avail},
                        )
                        raise LicenseError(
                            403,
                            "EMERGENCY_TRANSFER_EXHAUSTED",
                            "本季（90 天內）免冷卻緊急換機額度已用完（不可累加），請等待冷卻結束或聯繫客服特批。",
                            {"next_emergency_transfer_at": next_avail},
                        )
                    conn.execute(
                        """
                        UPDATE seats
                        SET active_device_id = ?, active_device_label = ?,
                            last_transfer_at = ?, last_emergency_transfer_at = ?
                        WHERE id = ?
                        """,
                        (device_id, device_label, ts, ts, seat["id"]),
                    )
                    event_type = "emergency_transfer_used"
                    transfer_notice = f"已動用本季免冷卻緊急換機，舊機（{old_dev}）已撤銷，並已寄發安全通知信至 {user['email']}。"
                else:
                    # 正常換機：撤銷舊機並開啟 144 小時冷卻
                    conn.execute(
                        """
                        UPDATE seats
                        SET active_device_id = ?, active_device_label = ?, last_transfer_at = ?
                        WHERE id = ?
                        """,
                        (device_id, device_label, ts, seat["id"]),
                    )
                    event_type = "normal_transfer"
                    transfer_notice = f"已正常換機至 {device_label}，舊機（{old_dev}）已撤銷，新機進入 144 小時換機冷卻。"

            seat = conn.execute("SELECT * FROM seats WHERE id = ?", (seat["id"],)).fetchone()
            jwt_token, payload = self._issue_lease_jwt(conn, user, sub, seat, device_id)
            self._log(
                conn,
                user_id=user["id"],
                seat_id=seat["id"],
                event_type=event_type,
                severity="warn" if event_type == "emergency_transfer_used" else "info",
                old_device_id=old_dev,
                new_device_id=device_id,
                ip_address=ip,
                detail={"device_label": device_label, "lease_exp": payload["exp"]},
            )
            return {
                "lease_jwt": jwt_token,
                "payload": payload,
                "event_type": event_type,
                "notice": transfer_notice,
            }

    def renew_lease(
        self,
        *,
        access_token: str,
        device_id: str,
        offline_grace_used_at: int | None = None,
        clock_anomaly_flag: bool = False,
        ip: str = "127.0.0.1",
    ) -> dict[str, Any]:
        device_id = device_id.strip().upper()
        with self._lock, self._connect() as conn:
            user = self._require_user_by_token(conn, access_token)
            self._sync_expirations(conn, user["id"])
            sub, seat = self._get_latest_sub_and_seat(conn, user["id"])
            if not sub or not seat:
                raise LicenseError(403, "NO_SUBSCRIPTION", "此帳號尚未訂閱。")
            if seat["revoked_at"] is not None:
                raise LicenseError(403, "SEAT_REVOKED", f"此授權席位已被撤銷：{seat['revoke_reason'] or '請聯繫客服'}。")
            if seat["active_device_id"] != device_id:
                self._log(
                    conn,
                    user_id=user["id"],
                    seat_id=seat["id"],
                    event_type="renew_rejected_device_mismatch",
                    severity="warn",
                    old_device_id=seat["active_device_id"],
                    new_device_id=device_id,
                    ip_address=ip,
                    detail={"reason": "舊機嘗試續期或未綁定"},
                )
                raise LicenseError(
                    403,
                    "DEVICE_REVOKED_OR_REPLACED",
                    "此裝置已非目前綁定裝置（可能已在其他電腦換機），請重新登入啟用。",
                )
            if sub["status"] not in ("active", "past_due"):
                raise LicenseError(403, "SUBSCRIPTION_EXPIRED", "訂閱已到期，無法續期。")

            ts = self.now()
            if offline_grace_used_at is not None:
                was_allowed = self._can_use_offline_grace(seat, ts)
                conn.execute("UPDATE seats SET last_offline_grace_at = ? WHERE id = ?", (ts, seat["id"]))
                self._log(
                    conn,
                    user_id=user["id"],
                    seat_id=seat["id"],
                    event_type="offline_grace_reported" if was_allowed else "offline_grace_anomaly",
                    severity="info" if was_allowed else "alert",
                    new_device_id=device_id,
                    ip_address=ip,
                    detail={"reported_at": offline_grace_used_at, "within_policy": was_allowed},
                )
                seat = conn.execute("SELECT * FROM seats WHERE id = ?", (seat["id"],)).fetchone()

            if clock_anomaly_flag:
                self._log(
                    conn,
                    user_id=user["id"],
                    seat_id=seat["id"],
                    event_type="client_clock_rollback_detected",
                    severity="alert",
                    new_device_id=device_id,
                    ip_address=ip,
                    detail={"note": "用戶端回報多點時鐘浮水印異常（疑似調回系統時間或還原快取）"},
                )

            jwt_token, payload = self._issue_lease_jwt(conn, user, sub, seat, device_id)
            self._log(
                conn,
                user_id=user["id"],
                seat_id=seat["id"],
                event_type="lease_renewed",
                new_device_id=device_id,
                ip_address=ip,
                detail={"lease_exp": payload["exp"], "sub_status": sub["status"]},
            )
            return {"lease_jwt": jwt_token, "payload": payload}

    # ==================== 客戶中心與獎勵券領取 ====================

    def get_account_status(self, *, access_token: str) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            user = self._require_user_by_token(conn, access_token)
            self._sync_expirations(conn, user["id"])
            ts = self.now()
            sub, seat = self._get_latest_sub_and_seat(conn, user["id"])
            rewards = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM account_rewards WHERE user_id = ? ORDER BY created_at DESC",
                    (user["id"],),
                ).fetchall()
            ]
            adjustments = []
            if sub:
                adjustments = [
                    dict(a)
                    for a in conn.execute(
                        "SELECT * FROM subscription_adjustments WHERE subscription_id = ? ORDER BY created_at DESC",
                        (sub["id"],),
                    ).fetchall()
                ]
            seat_dict = dict(seat) if seat else None
            if seat_dict:
                seat_dict["emergency_transfer_remaining"] = self._emergency_transfer_remaining(seat, ts)
                seat_dict["grace_available"] = self._can_use_offline_grace(seat, ts)
            return {
                "server_time": ts,
                "user": dict(user),
                "subscription": dict(sub) if sub else None,
                "seat": seat_dict,
                "rewards": rewards,
                "adjustments": adjustments,
            }

    def claim_reward(self, *, access_token: str, reward_id: str, ip: str = "127.0.0.1") -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            user = self._require_user_by_token(conn, access_token)
            self._sync_expirations(conn, user["id"])
            sub, seat = self._get_latest_sub_and_seat(conn, user["id"])
            if not sub or sub["status"] not in ("active", "past_due"):
                raise LicenseError(
                    403,
                    "SUBSCRIPTION_REQUIRED",
                    "訂閱已到期，延長獎勵券僅限於訂閱有效期間內套用（到期已自動失效）。",
                )

            ts = self.now()
            # 原子更新：確認綁定本帳號、狀態為 available、且未過截止日
            cur = conn.execute(
                """
                UPDATE account_rewards
                SET status = 'claimed', claimed_at = ?, claimed_sub_id = ?
                WHERE id = ? AND user_id = ? AND status = 'available'
                  AND (claim_before IS NULL OR claim_before > ?)
                """,
                (ts, sub["id"], reward_id, user["id"], ts),
            )
            if cur.rowcount != 1:
                raise LicenseError(
                    409,
                    "REWARD_UNAVAILABLE",
                    "此獎勵券無法領取（可能已領取、已過期、已撤銷或不屬於此帳號）。",
                )

            rew = conn.execute("SELECT * FROM account_rewards WHERE id = ?", (reward_id,)).fetchone()
            new_end = sub["current_period_end"] + rew["extend_days"] * 86400
            conn.execute(
                "UPDATE subscriptions SET current_period_end = ? WHERE id = ?",
                (new_end, sub["id"]),
            )
            adj_id = f"adj_{secrets.token_hex(6)}"
            conn.execute(
                """
                INSERT INTO subscription_adjustments
                    (id, subscription_id, source_type, source_id, delta_days, note, created_at)
                VALUES (?, ?, 'reward', ?, ?, ?, ?)
                """,
                (adj_id, sub["id"], reward_id, rew["extend_days"], rew["reason"], ts),
            )
            self._log(
                conn,
                user_id=user["id"],
                seat_id=seat["id"] if seat else None,
                event_type="reward_claimed",
                ip_address=ip,
                detail={
                    "reward_id": reward_id,
                    "extend_days": rew["extend_days"],
                    "new_subscription_end": new_end,
                    "billing_next_charge_shifted": True,
                },
            )
            return {
                "reward_id": reward_id,
                "extend_days": rew["extend_days"],
                "new_subscription_end": new_end,
            }
