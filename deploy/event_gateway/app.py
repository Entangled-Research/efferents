"""Event-only model proxy and sanitized heartbeat registry.

This service intentionally lives under deploy/: it is not part of the generic
single-lab domain model.  It stores opaque token hashes, append-only heartbeat
history, a current projection index, and usage accounting.  Prompt bodies are
proxied but never logged or persisted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


from efferents.journals import INTERDISCIPLINARY_EVERY, journal_for_domain, related_stem_domains


PROTOCOL = "efferents-event/v1"
CLIENT_MODEL = "openai/event-model"
EVENT_MODELS = {
    "openai/event-fast": "FAST",
    CLIENT_MODEL: "STANDARD",
    "openai/event-deep": "DEEP",
}
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_EVENT_BODY = 32_768
MAX_MODEL_BODY = 2 * 1024 * 1024
MAX_REASONING_BODY = 200_000  # below Azure GPT-5.6 long-context pricing threshold
log = logging.getLogger("efferents.event_gateway")


def is_journal_publication(item: dict) -> bool:
    """Standalone gateway protocol check; historic direct messages stay archival."""
    scores = item.get("review_scores")
    return (item.get("kind") == "publication" and item.get("publication_status") == "accepted"
            and isinstance(item.get("campaign_id"), str) and bool(item["campaign_id"])
            and isinstance(item.get("journal"), str) and bool(item["journal"])
            and isinstance(scores, dict) and set(scores) == {"critical", "neutral", "optimistic"}
            and all(type(value) is int and 1 <= value <= 10 for value in scores.values()))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def azure_deployments() -> dict[str, dict[str, Any]]:
    """Operator allowlist; deployment names and prices never come from clients."""
    result = {}
    for alias, tier in EVENT_MODELS.items():
        deployment = os.environ.get(f"EVENT_AZURE_{tier}_DEPLOYMENT", "").strip()
        if not deployment or not IDENTIFIER.fullmatch(deployment):
            raise RuntimeError(f"EVENT_AZURE_{tier}_DEPLOYMENT must be a deployment name")
        input_price = env_float(f"EVENT_AZURE_{tier}_INPUT_USD_PER_MTOK", 0)
        output_price = env_float(f"EVENT_AZURE_{tier}_OUTPUT_USD_PER_MTOK", 0)
        if not 0 < input_price < 1000 or not 0 < output_price < 1000:
            raise RuntimeError(f"EVENT_AZURE_{tier} prices must be positive and below $1000/MTok")
        result[alias] = {"deployment": deployment, "input": input_price, "output": output_price}
    return result


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str, *, code: str = "event_error"):
        self.status = status
        self.code = code
        super().__init__(message)


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    enrollment_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    total_cap_usd REAL NOT NULL,
                    spent_usd REAL NOT NULL DEFAULT 0,
                    reserved_usd REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    closed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    token_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL REFERENCES events(event_id),
                    lab_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    cap_usd REAL NOT NULL,
                    spent_usd REAL NOT NULL DEFAULT 0,
                    reserved_usd REAL NOT NULL DEFAULT 0,
                    requests INTEGER NOT NULL DEFAULT 0,
                    rate_window INTEGER NOT NULL DEFAULT 0,
                    rate_count INTEGER NOT NULL DEFAULT 0,
                    domain TEXT NOT NULL,
                    topic TEXT,
                    approach TEXT,
                    created_at TEXT NOT NULL,
                    last_sync_at TEXT,
                    UNIQUE(event_id, lab_id)
                );
                CREATE TABLE IF NOT EXISTS snapshot_history (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    lab_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(token_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS snapshot_current (
                    event_id TEXT NOT NULL,
                    lab_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(event_id, lab_id)
                );
                CREATE TABLE IF NOT EXISTS model_requests (
                    request_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS findings (
                    event_id TEXT NOT NULL, id TEXT NOT NULL, lab_id TEXT NOT NULL,
                    domain TEXT NOT NULL, goal TEXT NOT NULL, created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL, PRIMARY KEY(event_id,id)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    event_id TEXT NOT NULL, token_id TEXT NOT NULL, finding_id TEXT NOT NULL,
                    delivered_at TEXT NOT NULL, observed_at TEXT,
                    PRIMARY KEY(event_id,token_id,finding_id)
                );
                """
            )
            # In-place migration for an event database created by the starter
            # deployment before durable closure existed.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
            if "closed_at" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN closed_at TEXT")
            token_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tokens)")}
            for name, definition in (("goal", "TEXT NOT NULL DEFAULT ''"),
                                     ("share_findings", "INTEGER NOT NULL DEFAULT 0"),
                                     ("exchange_visits", "INTEGER NOT NULL DEFAULT 0")):
                if name not in token_columns:
                    conn.execute(f"ALTER TABLE tokens ADD COLUMN {name} {definition}")

    def configure_event(self) -> None:
        event_id = os.environ["EVENT_ID"]
        enrollment = os.environ["EVENT_ENROLLMENT_CODE"]
        admin_key = os.environ["EVENT_ADMIN_KEY"]
        public_url = os.environ["EVENT_PUBLIC_URL"]
        upstream_base = os.environ["EVENT_AZURE_OPENAI_BASE"]
        upstream_key = os.environ["EVENT_AZURE_OPENAI_API_KEY"]
        expires_at = os.environ["EVENT_EXPIRES_AT"]
        if not IDENTIFIER.fullmatch(event_id):
            raise RuntimeError("EVENT_ID is invalid")
        if len(enrollment) < 24 or len(admin_key) < 24 or enrollment == admin_key:
            raise RuntimeError("event enrollment and admin secrets must be distinct and at least 24 characters")
        parsed_public = urlparse(public_url)
        if (
            parsed_public.scheme != "https" or not parsed_public.hostname
            or parsed_public.username is not None or parsed_public.password is not None
            or parsed_public.path not in {"", "/"} or parsed_public.query or parsed_public.fragment
        ):
            raise RuntimeError("EVENT_PUBLIC_URL must be an HTTPS origin")
        parsed_upstream = urlparse(upstream_base)
        if (
            not parsed_upstream.hostname or parsed_upstream.username is not None
            or parsed_upstream.password is not None or parsed_upstream.query or parsed_upstream.fragment
            or (parsed_upstream.scheme != "https" and not (
                parsed_upstream.scheme == "http" and parsed_upstream.hostname in {"127.0.0.1", "localhost"}
            ))
        ):
            raise RuntimeError("EVENT_AZURE_OPENAI_BASE must use HTTPS (or loopback HTTP for testing)")
        if parsed_upstream.scheme == "https" and not parsed_upstream.hostname.endswith(
            (".openai.azure.com", ".services.ai.azure.com")
        ):
            raise RuntimeError("EVENT_AZURE_OPENAI_BASE must be an Azure OpenAI resource")
        if parsed_upstream.scheme == "https" and parsed_upstream.path.rstrip("/") != "/openai/v1":
            raise RuntimeError("EVENT_AZURE_OPENAI_BASE must end in /openai/v1")
        if not upstream_key.strip():
            raise RuntimeError("EVENT_AZURE_OPENAI_API_KEY must not be blank")
        azure_deployments()
        total = env_float("EVENT_TOTAL_CAP_USD", 50.0)
        token_cap = env_float("EVENT_TOKEN_CAP_USD", 3.0)
        if total <= 0 or token_cap <= 0 or token_cap > total:
            raise RuntimeError("event spending caps must be positive and token cap must not exceed total cap")
        if env_int("EVENT_REQUESTS_PER_MINUTE", 30) < 1:
            raise RuntimeError("EVENT_REQUESTS_PER_MINUTE must be positive")
        if env_int("EVENT_MAX_OUTPUT_TOKENS", 16384) < 1:
            raise RuntimeError("EVENT_MAX_OUTPUT_TOKENS must be positive")
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT enrollment_hash, expires_at, total_cap_usd, closed_at FROM events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is None:
                if epoch(expires_at) <= time.time():
                    raise RuntimeError("EVENT_EXPIRES_AT must be in the future")
                conn.execute(
                    "INSERT INTO events(event_id,enrollment_hash,expires_at,total_cap_usd,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (event_id, digest(enrollment), expires_at, total, now_iso()),
                )
            elif existing["closed_at"] is None:
                conn.execute(
                    "UPDATE events SET enrollment_hash=?, expires_at=?, total_cap_usd=? WHERE event_id=?",
                    (digest(enrollment), expires_at, total, event_id),
                )

    def _token(self, conn: sqlite3.Connection, bearer: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT t.*, e.total_cap_usd, e.spent_usd AS event_spent_usd, "
            "e.reserved_usd AS event_reserved_usd, e.expires_at AS event_expires_at "
            "FROM tokens t JOIN events e USING(event_id) WHERE token_hash=?",
            (digest(bearer),),
        ).fetchone()
        if row is None:
            raise ApiError(401, "event token is invalid", code="event_token_invalid")
        if row["status"] == "revoked":
            raise ApiError(403, "event token revoked", code="event_token_revoked")
        if row["status"] == "left":
            raise ApiError(403, "event token left the event", code="event_token_left")
        if epoch(row["expires_at"]) <= time.time() or epoch(row["event_expires_at"]) <= time.time():
            raise ApiError(403, "event token expired", code="event_token_expired")
        return row

    def authenticate(self, bearer: str, *, rate_limit: bool = False) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._token(conn, bearer)
            if rate_limit:
                window = int(time.time() // 60)
                count = row["rate_count"] if row["rate_window"] == window else 0
                limit = env_int("EVENT_REQUESTS_PER_MINUTE", 30)
                if count >= limit:
                    raise ApiError(429, "event token rate limited", code="event_rate_limited")
                conn.execute(
                    "UPDATE tokens SET rate_window=?, rate_count=? WHERE token_id=?",
                    (window, count + 1, row["token_id"]),
                )
            return dict(row)

    def join(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "protocol", "event_id", "enrollment_code", "lab_id", "domain", "topic", "approach",
            "goal", "share_findings",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ApiError(400, f"unknown join fields: {sorted(unknown)}")
        for key in ("event_id", "lab_id", "domain"):
            if not isinstance(payload.get(key), str) or not IDENTIFIER.fullmatch(payload[key]):
                raise ApiError(400, f"{key} is invalid")
        if payload.get("protocol") != PROTOCOL:
            raise ApiError(400, f"protocol must be {PROTOCOL}")
        if type(payload.get("share_findings", False)) is not bool:
            raise ApiError(400, "share_findings must be boolean")
        for key in ("topic", "approach", "goal"):
            value = payload.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 160):
                raise ApiError(400, f"{key} must be a string of at most 160 characters")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = conn.execute(
                "SELECT * FROM events WHERE event_id=?", (payload["event_id"],)
            ).fetchone()
            if event is None:
                raise ApiError(404, "unknown event")
            if event["closed_at"] is not None:
                raise ApiError(410, "event enrollment is closed", code="event_closed")
            supplied = str(payload.get("enrollment_code") or "")
            if not hmac.compare_digest(event["enrollment_hash"], digest(supplied)):
                raise ApiError(403, "enrollment code is invalid")
            if epoch(event["expires_at"]) <= time.time():
                raise ApiError(410, "event enrollment has expired")
            if conn.execute(
                "SELECT 1 FROM tokens WHERE event_id=? AND lab_id=?",
                (payload["event_id"], payload["lab_id"]),
            ).fetchone():
                raise ApiError(409, "this lab is already enrolled; use its existing credential or arrange a new lab identity with the organizer")
            secret = "evt_" + secrets.token_urlsafe(32)
            token_id = "tok_" + secrets.token_hex(6)
            cap = env_float("EVENT_TOKEN_CAP_USD", 3.0)
            conn.execute(
                "INSERT INTO tokens(token_id,event_id,lab_id,token_hash,status,expires_at,cap_usd,"
                "domain,topic,approach,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    token_id, payload["event_id"], payload["lab_id"], digest(secret), "active",
                    event["expires_at"], cap, payload["domain"], payload.get("topic"),
                    payload.get("approach"), now_iso(),
                ),
            )
            conn.execute("UPDATE tokens SET goal=?,share_findings=? WHERE token_id=?",
                         (payload.get("goal") or "", int(payload.get("share_findings", False)), token_id))
        public_url = os.environ["EVENT_PUBLIC_URL"].rstrip("/")
        return {
            "protocol": PROTOCOL,
            "event_id": payload["event_id"],
            "lab_id": payload["lab_id"],
            "token_id": token_id,
            "token": secret,
            "expires_at": event["expires_at"],
            "model": CLIENT_MODEL,
            "models": {alias: {"input": cfg["input"], "output": cfg["output"]}
                       for alias, cfg in azure_deployments().items()},
            "api_base": f"{public_url}/v1",
            "share_findings": payload.get("share_findings", False),
        }

    def exchange(self, bearer: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Bounded, opt-in publication and durable delivery receipts within one event."""
        if (set(payload) - {"receive"} != {"protocol", "publications", "observed"}
                or payload.get("protocol") != PROTOCOL or type(payload.get("receive", True)) is not bool):
            raise ApiError(400, "invalid exchange fields")
        publications, observed = payload["publications"], payload["observed"]
        if not isinstance(publications, list) or len(publications) > 6:
            raise ApiError(400, "at most six publications per exchange")
        if (not isinstance(observed, list) or len(observed) > 200
                or any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item) for item in observed)):
            raise ApiError(400, "invalid observation receipts")
        self.authenticate(bearer, rate_limit=True)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            token = dict(self._token(conn, bearer))
            if not token["share_findings"]:
                raise ApiError(403, "finding exchange was not enabled at join")
            event_id = token["event_id"]
            for item in publications:
                allowed = {"kind", "body", "campaign_id", "publication_status", "review_scores", "journal"}
                if (not isinstance(item, dict) or set(item) - allowed
                        or not is_journal_publication(item)
                        or not isinstance(item.get("body"), str) or not 1 <= len(item["body"]) <= 4000):
                    raise ApiError(400, "invalid bounded journal publication; direct messages are prohibited")
                for key in ("campaign_id", "journal"):
                    if len(item[key]) > 160:
                        raise ApiError(400, f"invalid publication {key}")
                if item["journal"] != journal_for_domain(token["domain"]):
                    raise ApiError(400, "publications must enter the lab own home journal")
                talk = {**item, "lab_id": token["lab_id"], "domain": token["domain"],
                        "goal": token["goal"], "venue": event_id}
                talk["id"] = digest(json.dumps(talk, sort_keys=True))
                conn.execute("INSERT OR IGNORE INTO findings VALUES(?,?,?,?,?,?,?)",
                             (event_id, talk["id"], token["lab_id"], token["domain"], token["goal"],
                              now_iso(), json.dumps(talk, sort_keys=True)))
            for finding_id in observed:
                conn.execute("UPDATE deliveries SET observed_at=COALESCE(observed_at,?) "
                             "WHERE event_id=? AND token_id=? AND finding_id=?",
                             (now_iso(), event_id, token["token_id"], finding_id))
            visit = token["exchange_visits"] + int(payload.get("receive", True))
            conn.execute("UPDATE tokens SET exchange_visits=? WHERE token_id=?", (visit, token["token_id"]))
            rows = conn.execute(
                "SELECT f.* FROM findings f WHERE f.event_id=? AND f.lab_id!=? AND NOT EXISTS "
                "(SELECT 1 FROM deliveries d WHERE d.event_id=f.event_id AND d.finding_id=f.id "
                "AND d.token_id=? AND d.observed_at IS NOT NULL) ORDER BY f.created_at DESC LIMIT 200",
                (event_id, token["lab_id"], token["token_id"]),
            ).fetchall()
            same, cross = [], []
            for row in rows:
                publication = json.loads(row["payload_json"])
                if (not is_journal_publication(publication)
                        or publication["journal"] != journal_for_domain(row["domain"])):
                    continue
                related = journal_for_domain(row["domain"]) == journal_for_domain(token["domain"])
                if related or related_stem_domains(row["domain"], token["domain"]):
                    (same if related else cross).append(row)
            selected = (same[:3] + (cross[:1] if visit % INTERDISCIPLINARY_EVERY == 0 else [])) if payload.get("receive", True) else []
            talks = []
            for row in selected:
                conn.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?,NULL)",
                             (event_id, token["token_id"], row["id"], now_iso()))
                talks.append({**json.loads(row["payload_json"]),
                              "track": "field" if row in same else "interdisciplinary"})
        return {"protocol": PROTOCOL, "visit": visit, "talks": talks}

    def sync(self, bearer: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
        validate_snapshot(payload)
        if not key or len(key) > 240:
            raise ApiError(400, "Idempotency-Key is required and must be at most 240 characters")
        token = self.authenticate(bearer, rate_limit=True)
        if payload["event_id"] != token["event_id"] or payload["lab_id"] != token["lab_id"]:
            raise ApiError(403, "snapshot identity does not match the event token")
        received = now_iso()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Revocation can occur after the first rate-limit check. Confirm
            # authorization in the same transaction as the append.
            self._token(conn, bearer)
            existing = conn.execute(
                "SELECT sequence,received_at,payload_json FROM snapshot_history "
                "WHERE token_id=? AND idempotency_key=?",
                (token["token_id"], key),
            ).fetchone()
            if existing:
                sequence = existing["sequence"]
                received = existing["received_at"]
                status = json.loads(existing["payload_json"])["runtime_status"]
            else:
                cursor = conn.execute(
                    "INSERT INTO snapshot_history(event_id,lab_id,token_id,idempotency_key,received_at,payload_json) "
                    "VALUES(?,?,?,?,?,?)",
                    (token["event_id"], token["lab_id"], token["token_id"], key, received, encoded),
                )
                sequence = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO snapshot_current(event_id,lab_id,sequence,received_at,payload_json) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(event_id,lab_id) DO UPDATE SET "
                    "sequence=excluded.sequence,received_at=excluded.received_at,payload_json=excluded.payload_json",
                    (token["event_id"], token["lab_id"], sequence, received, encoded),
                )
                conn.execute(
                    "UPDATE tokens SET last_sync_at=? WHERE token_id=?", (received, token["token_id"])
                )
                status = payload["runtime_status"]
        return {
            "protocol": PROTOCOL,
            "event_id": token["event_id"],
            "lab_id": token["lab_id"],
            "runtime_status": status,
            "sequence": sequence,
            "received_at": received,
        }

    def status(self, bearer: str) -> dict[str, Any]:
        token = self.authenticate(bearer)
        return {
            "protocol": PROTOCOL,
            "event_id": token["event_id"],
            "lab_id": token["lab_id"],
            "token_id": token["token_id"],
            "status": token["status"],
            "expires_at": token["expires_at"],
            "spend_usd": round(token["spent_usd"], 6),
            "cap_usd": token["cap_usd"],
            "requests": token["requests"],
            "last_sync_at": token["last_sync_at"],
        }

    def leave(self, bearer: str) -> dict[str, Any]:
        token = self.authenticate(bearer)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._token(conn, bearer)
            conn.execute(
                "UPDATE tokens SET status='left' WHERE token_id=? AND status='active'", (token["token_id"],)
            )
        return {"event_id": token["event_id"], "lab_id": token["lab_id"], "status": "left"}

    def network(self, event_id: str) -> dict[str, Any]:
        stale_s = env_int("EVENT_STALE_AFTER_SECONDS", 180)
        with self.connect() as conn:
            event = conn.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            if event is None:
                raise ApiError(404, "unknown event")
            current = conn.execute(
                "SELECT * FROM snapshot_current WHERE event_id=? ORDER BY lab_id", (event_id,)
            ).fetchall()
            tokens = conn.execute(
                "SELECT token_id,lab_id,status,expires_at,cap_usd,spent_usd,requests,last_sync_at "
                "FROM tokens WHERE event_id=? ORDER BY created_at", (event_id,)
            ).fetchall()
            findings = conn.execute("SELECT payload_json FROM findings WHERE event_id=? ORDER BY created_at DESC LIMIT 150",
                                    (event_id,)).fetchall()
            observations = conn.execute(
                "SELECT f.lab_id AS source,t.lab_id AS target,d.finding_id,d.observed_at AS at "
                "FROM deliveries d JOIN findings f ON f.event_id=d.event_id AND f.id=d.finding_id "
                "JOIN tokens t ON t.token_id=d.token_id WHERE d.event_id=? AND d.observed_at IS NOT NULL "
                "ORDER BY d.observed_at DESC LIMIT 200", (event_id,),
            ).fetchall()
        publications = [json.loads(row["payload_json"]) for row in findings
                        if is_journal_publication(json.loads(row["payload_json"]))
                        and json.loads(row["payload_json"])["journal"] == journal_for_domain(
                            json.loads(row["payload_json"]).get("domain", ""))]
        publication_ids = {item["id"] for item in publications}
        labs = []
        for row in current:
            item = json.loads(row["payload_json"])
            status = item["runtime_status"]
            if status not in {"stopped", "paused"} and time.time() - epoch(row["received_at"]) > stale_s:
                status = "stale"
            item["runtime_status"] = status
            item["received_at"] = row["received_at"]
            item["sequence"] = row["sequence"]
            labs.append(item)
        return {
            "protocol": PROTOCOL,
            "event": {
                "event_id": event_id,
                "expires_at": event["expires_at"],
                "closed_at": event["closed_at"],
                "total_cap_usd": event["total_cap_usd"],
                "spent_usd": round(event["spent_usd"], 6),
                "token_count": len(tokens),
            },
            "labs": labs,
            "tokens": [dict(row) for row in tokens],
            "findings": publications,
            "observations": [{**dict(row), "kind": "observation"} for row in observations if row["finding_id"] in publication_ids],
            "generated_at": now_iso(),
        }

    def revoke(self, token_id: str) -> None:
        with self.connect() as conn:
            changed = conn.execute(
                "UPDATE tokens SET status='revoked' WHERE token_id=?", (token_id,)
            ).rowcount
        if not changed:
            raise ApiError(404, "unknown token id")

    def close_event(self, event_id: str) -> int:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone() is None:
                raise ApiError(404, "unknown event")
            changed = conn.execute(
                "UPDATE tokens SET status='revoked' WHERE event_id=? AND status='active'",
                (event_id,),
            ).rowcount
            conn.execute(
                "UPDATE events SET closed_at=COALESCE(closed_at,?) WHERE event_id=?",
                (now_iso(), event_id),
            )
        return changed

    def purge_tokens(self, event_id: str) -> int:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            event = conn.execute(
                "SELECT closed_at FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if event is None or event["closed_at"] is None:
                raise ApiError(409, "close the event before purging credentials")
            conn.execute("DELETE FROM model_requests WHERE event_id=?", (event_id,))
            conn.execute("DELETE FROM deliveries WHERE event_id=?", (event_id,))
            conn.execute("DELETE FROM snapshot_history WHERE event_id=?", (event_id,))
            return conn.execute("DELETE FROM tokens WHERE event_id=?", (event_id,)).rowcount

    def backup(self, event_id: str) -> Path:
        name = f"backup-{event_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.sqlite"
        destination = self.path.parent / name
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with closing(self.connect()) as source, closing(sqlite3.connect(destination)) as target:
                source.backup(target)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def reserve_model(self, bearer: str, estimate: float) -> dict[str, Any]:
        token = self.authenticate(bearer, rate_limit=True)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            token = dict(self._token(conn, bearer))
            if token["spent_usd"] + token["reserved_usd"] + estimate > token["cap_usd"]:
                raise ApiError(402, "event token quota exhausted", code="event_quota_exhausted")
            if token["event_spent_usd"] + token["event_reserved_usd"] + estimate > token["total_cap_usd"]:
                raise ApiError(402, "event total quota exhausted", code="event_quota_exhausted")
            conn.execute(
                "UPDATE tokens SET reserved_usd=reserved_usd+? WHERE token_id=?",
                (estimate, token["token_id"]),
            )
            conn.execute(
                "UPDATE events SET reserved_usd=reserved_usd+? WHERE event_id=?",
                (estimate, token["event_id"]),
            )
        token["estimate"] = estimate
        return token

    def settle_model(
        self, token: dict[str, Any], *, request_id: str, status: str,
        input_tokens: int = 0, output_tokens: int = 0, actual: float = 0.0,
    ) -> None:
        estimate = float(token["estimate"])
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE tokens SET reserved_usd=MAX(0,reserved_usd-?), "
                "spent_usd=spent_usd+?, requests=requests+1 WHERE token_id=?",
                (estimate, actual, token["token_id"]),
            )
            conn.execute(
                "UPDATE events SET reserved_usd=MAX(0,reserved_usd-?), spent_usd=spent_usd+? "
                "WHERE event_id=?",
                (estimate, actual, token["event_id"]),
            )
            conn.execute(
                "INSERT INTO model_requests VALUES(?,?,?,?,?,?,?,?)",
                (
                    request_id, token["event_id"], token["token_id"], now_iso(), status,
                    input_tokens, output_tokens, actual,
                ),
            )


def validate_snapshot(payload: dict[str, Any]) -> None:
    allowed = {
        "protocol", "event_id", "lab_id", "domain", "topic", "approach",
        "runtime_status", "last_activity_at", "headline", "run_count",
        "verdict_status", "budget_state",
        "goal",
    }
    unknown = set(payload) - allowed
    missing = allowed - {"topic", "approach", "goal"} - set(payload)
    if unknown or missing:
        raise ApiError(400, f"snapshot fields invalid; missing={sorted(missing)} unknown={sorted(unknown)}")
    if payload.get("protocol") != PROTOCOL:
        raise ApiError(400, f"protocol must be {PROTOCOL}")
    for key in ("event_id", "lab_id", "domain"):
        if not isinstance(payload.get(key), str) or not IDENTIFIER.fullmatch(payload[key]):
            raise ApiError(400, f"snapshot {key} is invalid")
    if payload.get("runtime_status") not in {"running", "paused", "stopped"}:
        raise ApiError(400, "runtime_status must be running, paused, or stopped")
    try:
        epoch(payload["last_activity_at"])
    except (KeyError, TypeError, ValueError):
        raise ApiError(400, "last_activity_at must be an ISO-8601 timestamp")
    if type(payload.get("run_count")) is not int or not 0 <= payload["run_count"] <= 10**9:
        raise ApiError(400, "run_count is invalid")
    if payload.get("budget_state") not in {"available", "near_limit", "exhausted"}:
        raise ApiError(400, "budget_state is invalid")
    headline = payload.get("headline")
    if not isinstance(headline, dict) or set(headline) != {"name", "direction", "latest_value"}:
        raise ApiError(400, "headline fields are invalid")
    if not isinstance(headline["name"], str) or not IDENTIFIER.fullmatch(headline["name"]):
        raise ApiError(400, "headline name is invalid")
    if headline["direction"] not in {"min", "max"}:
        raise ApiError(400, "headline direction is invalid")
    value = headline["latest_value"]
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise ApiError(400, "headline latest_value must be numeric or null")
    for key in ("topic", "approach", "goal", "verdict_status"):
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 160):
            raise ApiError(400, f"{key} is invalid")


def bearer(headers) -> str:
    prefix = "Bearer "
    value = headers.get("Authorization", "")
    if not value.startswith(prefix) or not value[len(prefix):]:
        raise ApiError(401, "Bearer event token is required", code="event_token_invalid")
    return value[len(prefix):]


class Handler(BaseHTTPRequestHandler):
    store: Store

    def do_GET(self):  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                return self.send_json({"ok": True})
            if parsed.path == "/v1/models":
                token = self.store.authenticate(bearer(self.headers))
                return self.send_json({
                    "object": "list",
                    "data": [{"id": alias, "object": "model"} for alias in EVENT_MODELS],
                    "event_token_id": token["token_id"],
                })
            if parsed.path == "/event/v1/status":
                return self.send_json(self.store.status(bearer(self.headers)))
            if parsed.path in {"/event/v1/network", "/event/v1/export"}:
                self.require_admin()
                event_id = parse_qs(parsed.query).get("event_id", [os.environ["EVENT_ID"]])[0]
                payload = self.store.network(event_id)
                if parsed.path.endswith("/export"):
                    # The retained event summary contains consented lab fields
                    # and aggregate spend, not per-participant credential rows.
                    payload.pop("tokens", None)
                return self.send_json(payload, download=parsed.path.endswith("/export"))
            raise ApiError(404, "not found")
        except ApiError as exc:
            self.send_api_error(exc)
        except Exception:
            log.exception("GET failed path=%s", self.path)
            self.send_api_error(ApiError(500, "event service request failed"))

    def do_POST(self):  # noqa: N802
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/event/v1/join":
                return self.send_json(self.store.join(self.read_json(MAX_EVENT_BODY)), status=201)
            if parsed.path == "/event/v1/sync":
                payload = self.read_json(MAX_EVENT_BODY)
                return self.send_json(self.store.sync(
                    bearer(self.headers), payload, self.headers.get("Idempotency-Key", "")
                ))
            if parsed.path == "/event/v1/exchange":
                return self.send_json(self.store.exchange(bearer(self.headers), self.read_json(MAX_EVENT_BODY)))
            if parsed.path == "/event/v1/leave":
                payload = self.read_json(MAX_EVENT_BODY)
                if set(payload) != {"protocol", "event_id"} or payload.get("protocol") != PROTOCOL:
                    raise ApiError(400, "leave payload is invalid")
                return self.send_json(self.store.leave(bearer(self.headers)))
            if parsed.path == "/v1/chat/completions":
                return self.proxy_model(self.read_json(MAX_MODEL_BODY), bearer(self.headers))
            raise ApiError(404, "not found")
        except ApiError as exc:
            self.send_api_error(exc)
        except Exception:
            log.exception("POST failed path=%s", self.path)
            self.send_api_error(ApiError(500, "event service request failed"))

    def require_admin(self) -> None:
        expected = os.environ["EVENT_ADMIN_KEY"]
        supplied = self.headers.get("X-Efferents-Event-Admin", "")
        if not hmac.compare_digest(expected, supplied):
            raise ApiError(403, "organizer authorization required")

    def read_json(self, maximum: int) -> dict[str, Any]:
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ApiError(415, "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(400, "Content-Length is invalid")
        if length <= 0 or length > maximum:
            raise ApiError(413, "request body is empty or too large")
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError(400, "request body is invalid JSON")
        if not isinstance(value, dict):
            raise ApiError(400, "request body must be a JSON object")
        return value

    def proxy_model(self, payload: dict[str, Any], token_value: str) -> None:
        allowed = {
            "model", "messages", "max_tokens", "max_completion_tokens", "tools",
            "tool_choice", "temperature", "top_p", "stop", "parallel_tool_calls", "n",
        }
        if set(payload) - allowed:
            raise ApiError(400, "event proxy accepts text chat and tool calls only")
        if payload.get("stream"):
            raise ApiError(400, "streaming is not enabled for the event proxy")
        if not isinstance(payload.get("messages"), list):
            raise ApiError(400, "messages must be a list")
        if any(not isinstance(message, dict) or not isinstance(message.get("content"), (str, type(None)))
               for message in payload["messages"]):
            raise ApiError(400, "event proxy accepts text messages only")
        if "max_tokens" in payload and "max_completion_tokens" in payload:
            raise ApiError(400, "choose one output token limit")
        model = payload.get("model")
        if isinstance(model, str) and f"openai/{model}" in EVENT_MODELS:
            model = f"openai/{model}"
        models = azure_deployments()
        if model not in models:
            raise ApiError(400, "model is not an enabled event tier")
        try:
            max_tokens = int(payload.get("max_completion_tokens") or payload.get("max_tokens") or 0)
        except (TypeError, ValueError):
            raise ApiError(400, "max_tokens is invalid")
        if max_tokens < 1 or max_tokens > env_int("EVENT_MAX_OUTPUT_TOKENS", 16384):
            raise ApiError(400, "max_tokens is outside the event limit")
        if payload.get("n", 1) != 1:
            raise ApiError(400, "event proxy permits one completion choice")
        upstream_payload = dict(payload)
        upstream_payload["model"] = models[model]["deployment"]
        if model != "openai/event-fast":
            if "temperature" in upstream_payload or "top_p" in upstream_payload:
                raise ApiError(400, "Luna and Sol do not accept sampling parameters")
            upstream_payload.pop("max_tokens", None)
            upstream_payload["max_completion_tokens"] = max_tokens
            # Azure Chat Completions rejects GPT-5.6 function tools with its
            # default reasoning effort. Tool-bearing calls can use the model
            # with effort=none; no-tool calls retain medium reasoning.
            upstream_payload["reasoning_effort"] = "none" if payload.get("tools") else "medium"
        raw = json.dumps(upstream_payload, separators=(",", ":")).encode()
        if model != "openai/event-fast" and len(raw) > MAX_REASONING_BODY:
            raise ApiError(413, "Luna and Sol requests exceed the event short-context limit")
        # A byte-count upper bound also covers tool schemas and other request
        # fields. It is intentionally conservative so a request cannot cross
        # an event cap merely because the proxy lacks the vendor's tokenizer.
        estimated_input = len(raw)
        in_price = models[model]["input"]
        out_price = models[model]["output"]
        estimate = estimated_input * in_price / 1_000_000 + max_tokens * out_price / 1_000_000
        token = self.store.reserve_model(token_value, estimate)
        request_id = "req_" + secrets.token_hex(8)
        request = urllib.request.Request(
            os.environ["EVENT_AZURE_OPENAI_BASE"].rstrip("/") + "/chat/completions",
            data=raw,
            method="POST",
            headers={
                "Authorization": f"Bearer {os.environ['EVENT_AZURE_OPENAI_API_KEY']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "efferents-event-proxy/1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=env_float("EVENT_UPSTREAM_TIMEOUT_S", 120)) as response:
                response_body = response.read(MAX_MODEL_BODY * 4)
                response_status = response.status
        except urllib.error.HTTPError as exc:
            response_body = exc.read(MAX_EVENT_BODY)
            response_status = exc.code
            self.store.settle_model(token, request_id=request_id, status=f"upstream_{exc.code}")
            log.warning("model request token_id=%s request_id=%s status=%s", token["token_id"], request_id, response_status)
            return self.send_raw(response_body, status=response_status)
        except (urllib.error.URLError, TimeoutError, OSError):
            self.store.settle_model(token, request_id=request_id, status="upstream_unavailable")
            log.warning("model request token_id=%s request_id=%s status=unavailable", token["token_id"], request_id)
            raise ApiError(503, "event model proxy is temporarily unavailable", code="event_proxy_unavailable")
        try:
            response_json = json.loads(response_body)
            usage = response_json.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or 0)
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            input_tokens = output_tokens = 0
        actual = input_tokens * in_price / 1_000_000 + output_tokens * out_price / 1_000_000
        self.store.settle_model(
            token, request_id=request_id, status="ok", input_tokens=input_tokens,
            output_tokens=output_tokens, actual=actual,
        )
        log.info(
            "model request token_id=%s request_id=%s status=ok input_tokens=%s output_tokens=%s cost_usd=%.6f",
            token["token_id"], request_id, input_tokens, output_tokens, actual,
        )
        self.send_raw(response_body, status=response_status)

    def send_api_error(self, exc: ApiError) -> None:
        self.send_json({"error": {"message": str(exc), "type": exc.code, "code": exc.code}}, status=exc.status)

    def send_json(self, payload: Any, *, status: int = 200, download: bool = False) -> None:
        body = json.dumps(payload, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download:
            self.send_header("Content-Disposition", f"attachment; filename=event-{os.environ['EVENT_ID']}.json")
        self.end_headers()
        self.wfile.write(body)

    def send_raw(self, body: bytes, *, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


def admin(store: Store, argv: list[str]) -> int:
    if not argv or argv[0] == "tokens":
        payload = store.network(os.environ["EVENT_ID"])
        print(json.dumps({"event": payload["event"], "tokens": payload["tokens"]}, indent=2))
        return 0
    if argv[0] == "revoke" and len(argv) == 2:
        store.revoke(argv[1])
        print(f"revoked token_id={argv[1]}")
        return 0
    if argv[0] == "export":
        payload = store.network(os.environ["EVENT_ID"])
        payload.pop("tokens", None)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if argv[0] == "revoke-all":
        with store.connect() as conn:
            changed = conn.execute(
                "UPDATE tokens SET status='revoked' WHERE event_id=? AND status='active'",
                (os.environ["EVENT_ID"],),
            ).rowcount
        print(f"revoked {changed} active event tokens")
        return 0
    if argv[0] == "close":
        changed = store.close_event(os.environ["EVENT_ID"])
        print(f"closed event and revoked {changed} active event tokens")
        return 0
    if argv[0] == "purge-tokens":
        changed = store.purge_tokens(os.environ["EVENT_ID"])
        print(f"deleted {changed} event credential hashes and token accounting rows")
        return 0
    if argv[0] == "backup":
        print(f"backup={store.backup(os.environ['EVENT_ID'])}")
        return 0
    print(
        "usage: app.py admin [tokens | revoke TOKEN_ID | revoke-all | close | export | purge-tokens | backup]",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    store = Store(os.environ.get("EVENT_DB", "/data/event/event.sqlite"))
    store.configure_event()
    if argv and argv[0] == "admin":
        return admin(store, argv[1:])
    Handler.store = store
    port = env_int("EVENT_PORT", 8801)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    log.info("event gateway listening on 127.0.0.1:%s event_id=%s", port, os.environ["EVENT_ID"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
