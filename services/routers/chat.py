"""The WebSocket chat with the first-line CX agent (Real Time Systems, part 2), on the same API and the same JWT as the rest of the backoffice.

    POST /chat/sessions                      create a ChatSession (`session_id`, `agent_id: first_line_cx`, `user_id`, `client_id`, `status`, `created_at`)
    GET  /chat/sessions                      the signed-in user's sessions
    GET  /chat/sessions/{session_id}         one session with its message history (the REST way to rehydrate)
    POST /chat/sessions/{session_id}/close   close it (aborts a running generation)
    WS   /chat/sessions/{session_id}/ws      the persistent, bidirectional connection of the session (contract: services/chat/protocol.py)

The socket is bound to ONE existing session by the URL and to its owner by the token. The JWT travels as `?token=<jwt>` (checked BEFORE the handshake is accepted: a bad token never gets a
socket) or, when it is not in the URL (which ends up in access logs), as the first frame `{"event": "auth", "data": {"token": ...}}`, which must arrive within 5 s. Until the client is authenticated
and the session is its own, no chat event is sent. The router only moves frames: the agent runs in `services/chat/service.py`, which publishes to the session's pub/sub channel (`chat.<session_id>`),
and this socket forwards what it receives from that channel.
"""

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..accounts import User
from ..auth import get_current_user
from ..chat import protocol, store
from ..chat.service import ChatError, ChatService, service_for
from ..database import get_session

logger = logging.getLogger("trackflow.chat")
router = APIRouter(prefix="/chat", tags=["chat"])

AUTH_TIMEOUT_SECONDS = 5.0


def chat_engine(session: Session = Depends(get_session)):
    return session.get_bind()


def allowed_origins() -> set[str]:
    """The browser origins that may open the socket: the same list as the API's CORS policy (`CORS_ORIGINS`). A WebSocket handshake is not covered by CORS, so it is checked here."""
    return {origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3001,http://127.0.0.1:3001,http://localhost:3000,http://127.0.0.1:3000").split(",") if origin.strip()}


def max_socket_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("CHAT_WS_MAX_SECONDS", "1800")))
    except ValueError:
        return 1800.0


class NewSession(BaseModel):
    client_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The conversation was not found.")


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_chat_session(body: NewSession = Body(default_factory=NewSession), user: User = Depends(get_current_user), engine=Depends(chat_engine)) -> dict[str, Any]:
    return service_for(engine).create_session(user.id, body.client_id)


@router.get("/sessions")
def list_chat_sessions(user: User = Depends(get_current_user), engine=Depends(chat_engine)) -> dict[str, Any]:
    return {"sessions": store.list_sessions(engine, user.id)}


@router.get("/sessions/{session_id}")
def read_chat_session(session_id: str, user: User = Depends(get_current_user), engine=Depends(chat_engine)) -> dict[str, Any]:
    service = service_for(engine)
    found = service.session_for(user.id, session_id)
    if found is None:
        raise _not_found()
    return {**found, "messages": service.snapshot(session_id)["messages"]}


@router.post("/sessions/{session_id}/close")
def close_chat_session(session_id: str, user: User = Depends(get_current_user), engine=Depends(chat_engine)) -> dict[str, Any]:
    service = service_for(engine)
    if service.session_for(user.id, session_id) is None:
        raise _not_found()
    service.close(session_id)
    return {**store.get_session(engine, session_id)}


# ------------------------------------------------------------------------------------------------ the WebSocket
def _authenticate(token: str) -> User | None:
    try:
        return get_current_user(token)
    except HTTPException:
        return None


async def _first_frame_user(websocket: WebSocket) -> User | None:
    """No token in the URL: the first frame must be `auth` with the JWT, within 5 s. Nothing else is accepted before it."""
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=AUTH_TIMEOUT_SECONDS)
        frame = protocol.parse_client_frame(raw)
    except (asyncio.TimeoutError, WebSocketDisconnect, protocol.FrameError):
        return None
    token = frame.data.get("token")
    if frame.event != protocol.AUTH or not isinstance(token, str):
        return None
    return await run_in_threadpool(_authenticate, token)


@router.websocket("/sessions/{session_id}/ws")
async def chat_socket(websocket: WebSocket, session_id: str, token: str | None = Query(default=None), engine=Depends(chat_engine)) -> None:
    service: ChatService = service_for(engine)
    user: User | None = None
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in allowed_origins():  # a page of another site cannot open this socket (a client that is not a browser sends no Origin)
        await websocket.close(code=1008)
        return
    if token is not None:  # checked before the handshake is accepted: a bad token or somebody else's session never gets a socket
        user = await run_in_threadpool(_authenticate, token)
        if user is None:
            await websocket.close(code=protocol.CLOSE_UNAUTHORIZED)
            return
        if await run_in_threadpool(service.session_for, user.id, session_id) is None:
            await websocket.close(code=protocol.CLOSE_NOT_FOUND)
            return
    await websocket.accept()
    if user is None:
        user = await _first_frame_user(websocket)
        if user is None:
            await websocket.close(code=protocol.CLOSE_UNAUTHORIZED)
            return
        if await run_in_threadpool(service.session_for, user.id, session_id) is None:
            await websocket.close(code=protocol.CLOSE_NOT_FOUND)
            return
    found = await run_in_threadpool(store.get_session, engine, session_id)
    if found["status"] == "closed":
        await websocket.close(code=protocol.CLOSE_CLOSED)
        return

    role = getattr(user.role, "value", str(user.role))
    send_lock = asyncio.Lock()

    async def send(event: str, data: dict) -> None:
        async with send_lock:
            await websocket.send_text(protocol.encode(event, data))

    subscription = service.bus.subscribe(session_id)  # attach to the session's channel BEFORE the snapshot, so no token falls between them
    try:
        await send(protocol.SESSION_SNAPSHOT, await run_in_threadpool(service.snapshot, session_id))  # rehydrate: the conversation as it is now, before any new token

        async def forward() -> None:
            """Pub/sub consumer: what the agent publishes to `chat.<session_id>` goes to this client."""
            deadline = asyncio.get_running_loop().time() + max_socket_seconds()
            while True:
                try:
                    message = await asyncio.wait_for(subscription.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if subscription.overflowed:
                        await websocket.close(code=protocol.CLOSE_OVERLOADED)
                        return
                    if asyncio.get_running_loop().time() > deadline:
                        await websocket.close(code=4000)  # the client reconnects (and presents its token again): a socket never outlives its session for long
                        return
                    continue
                await send(message["event"], message["data"])

        async def listen() -> None:
            while True:
                raw = await websocket.receive_text()
                try:
                    frame = protocol.parse_client_frame(raw)
                    await handle(frame)
                except (protocol.FrameError, ChatError) as error:
                    await send(protocol.ERROR, {"code": error.code, "message": str(error)})

        async def handle(frame: protocol.Frame) -> None:
            if frame.event == protocol.PING:
                await send(protocol.PONG, {})
                return
            if frame.event == protocol.AUTH:
                raise protocol.FrameError("already_authenticated", "This connection is already authenticated.")
            named = frame.data.get("session_id", session_id)
            if named != session_id:
                raise protocol.FrameError("session_mismatch", "This connection belongs to another conversation.")
            if frame.event == protocol.USER_MESSAGE:
                text = protocol.clean_text(frame.data.get("text"), "text", required=True)
                await run_in_threadpool(service.user_message, session_id, user.id, role, text)
            elif frame.event == protocol.INTERRUPT_REQUESTED:
                new_input = protocol.clean_text(frame.data.get("new_input"), "new_input", required=False)
                await run_in_threadpool(service.interrupt, session_id, user.id, role, new_input)

        tasks = {asyncio.create_task(forward()), asyncio.create_task(listen())}
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            error = task.exception()
            if error is not None and not isinstance(error, (WebSocketDisconnect, RuntimeError)):
                logger.error("chat socket %s ended with %s", session_id, type(error).__name__)
    finally:
        service.bus.unsubscribe(subscription)
