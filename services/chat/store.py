"""The chat's database access: sessions and messages (PostgreSQL / Supabase in production, SQLite in development), through SQLModel.

One re-entrant lock around every access: the generation runs in worker threads while requests read and write the same tables, and a development or test database shares one connection.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, select

from .protocol import AGENT_ID

_lock = threading.RLock()


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_session_id() -> str:
    return f"chat_{uuid.uuid4().hex[:8]}"


def new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:8]}"


def iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_dict(row) -> dict:
    return {"session_id": row.session_id, "agent_id": row.agent_id, "user_id": row.user_id, "client_id": row.client_id, "status": row.status, "created_at": iso(row.created_at)}


def message_dict(row) -> dict:
    return {"message_id": row.message_id, "role": row.role, "text": row.text, "status": row.status, "position": row.seq, "token_count": row.token_count, "run_id": row.run_id, "created_at": iso(row.created_at)}


def create_session(engine, user_id: str, client_id: str | None = None) -> dict:
    from services import models

    with _lock, Session(engine) as session:
        row = models.ChatSession(session_id=new_session_id(), agent_id=AGENT_ID, user_id=user_id, client_id=client_id or user_id)
        session.add(row)
        session.commit()
        session.refresh(row)
        return session_dict(row)


def get_session(engine, session_id: str) -> dict | None:
    from services import models

    with _lock, Session(engine) as session:
        row = session.get(models.ChatSession, session_id)
        return session_dict(row) if row else None


def list_sessions(engine, user_id: str, limit: int = 20) -> list[dict]:
    from services import models

    with _lock, Session(engine) as session:
        rows = session.exec(select(models.ChatSession).where(models.ChatSession.user_id == user_id).order_by(models.ChatSession.created_at.desc()).limit(limit))
        return [session_dict(row) for row in rows]


def set_session_status(engine, session_id: str, status: str) -> None:
    from services import models

    with _lock, Session(engine) as session:
        row = session.get(models.ChatSession, session_id)
        if row and row.status != "closed":
            row.status, row.updated_at = status, now()
            session.add(row)
            session.commit()


def close_session(engine, session_id: str) -> None:
    from services import models

    with _lock, Session(engine) as session:
        row = session.get(models.ChatSession, session_id)
        if row:
            row.status, row.updated_at = "closed", now()
            session.add(row)
            session.commit()


def add_message(engine, session_id: str, role: str, text: str, status: str = "completed", run_id: str | None = None) -> dict:
    from services import models

    with _lock, Session(engine) as session:
        last = session.exec(select(func.max(models.ChatMessage.seq)).where(models.ChatMessage.session_id == session_id)).one()
        row = models.ChatMessage(message_id=new_message_id(), session_id=session_id, seq=(last or 0) + 1, role=role, text=text, status=status, run_id=run_id)
        session.add(row)
        session.commit()
        session.refresh(row)
        return message_dict(row)


def update_message(engine, message_id: str, *, text: str | None = None, status: str | None = None, token_count: int | None = None) -> None:
    from services import models

    with _lock, Session(engine) as session:
        row = session.get(models.ChatMessage, message_id)
        if row is None:
            return
        if text is not None:
            row.text = text
        if status is not None:
            row.status = status
        if token_count is not None:
            row.token_count = token_count
        row.updated_at = now()
        session.add(row)
        session.commit()


def messages_of(engine, session_id: str) -> list[dict]:
    from services import models

    with _lock, Session(engine) as session:
        rows = session.exec(select(models.ChatMessage).where(models.ChatMessage.session_id == session_id).order_by(models.ChatMessage.seq))
        return [message_dict(row) for row in rows]


def interrupt_orphans(engine) -> int:
    """At startup: a message left `streaming` belongs to a generation that died with the previous process. It keeps the text it had and becomes `interrupted`."""
    from services import models

    with _lock, Session(engine) as session:
        rows = list(session.exec(select(models.ChatMessage).where(models.ChatMessage.status == "streaming")))
        for row in rows:
            row.status, row.updated_at = "interrupted", now()
            session.add(row)
            parent = session.get(models.ChatSession, row.session_id)
            if parent and parent.status == "active":
                parent.status = "interrupted"
                session.add(parent)
        session.commit()
        return len(rows)
