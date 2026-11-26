"""The audit log: every permission change, admin action and access to sensitive data. Append-only."""
import json
import time

from flask import g, has_request_context, request

from pb.db import fetch_all, insert_id

AUDIT_PAGE = 200


def current_actor():
    """Who is acting in this request: "staff:<id>", "staff:root" (the /admin password), "user:<phone>" or "system"."""
    if has_request_context():
        return getattr(g, "principal", None) or (f"user:{g.phone}" if getattr(g, "phone", None) else "system")
    return "system"


def _ip():
    if not has_request_context():
        return None
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()[:64] or None


def audit(action, tenant_id=None, target=None, details=None, actor=None, channel=None):
    """Record one action. target: (type, id). details: anything JSON-serialisable (never PINs or passwords)."""
    target_type, target_id = target if target else (None, None)
    from pb import analytics
    if analytics.enabled():
        who = actor or current_actor()
        analytics.track(action, analytics.actor_id(who), tenant_id, kind="admin_action",
                        channel=channel or (getattr(g, "channel", None) if has_request_context() else None))
    return insert_id(
        "INSERT INTO audit_log (tenant_id, actor, action, target_type, target_id, details, ip, channel, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tenant_id,
            actor or current_actor(),
            action,
            target_type,
            None if target_id is None else str(target_id),
            json.dumps(details, default=str, sort_keys=True) if details is not None else None,
            _ip(),
            channel or getattr(g, "channel", None) if has_request_context() else channel,
            int(time.time()),
        ),
    )


def audit_entries(tenant_id=None, actor=None, action=None, platform_only=False, limit=AUDIT_PAGE):
    where, params = [], []
    if tenant_id is not None:
        where.append("tenant_id = ?")
        params.append(tenant_id)
    elif platform_only:
        where.append("tenant_id IS NULL")
    if actor:
        where.append("actor LIKE ?")
        params.append(f"%{actor}%")
    if action:
        where.append("action LIKE ?")
        params.append(f"{action}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = fetch_all(
        f"SELECT id, tenant_id, actor, action, target_type, target_id, details, ip, channel, created_at FROM audit_log "
        f"{clause} ORDER BY id DESC LIMIT {int(limit)}",
        tuple(params),
    )
    return [dict(r) for r in rows]
