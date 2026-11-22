"use client";

import { useCallback, useEffect, useReducer, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError, API_BASE_URL, GENERIC_ERROR_MESSAGE, messageFor } from "../../../lib/api-client";
import { getAccessToken } from "../../../lib/auth";
import { chatApi, rememberSession, rememberedSession } from "../../../lib/chat";
import { applyEvent, initialChatState, streamingMessage, type ChatMessage, type ChatState, type ServerEvent } from "../../../lib/chat-state";
import { ChatSocketClient, socketStatusText, socketUrl, type SocketStatus } from "../../../lib/chat-socket";
import { EXAMPLE_QUESTIONS, cleanQuestion } from "../../../lib/knowledge";

type Action = { type: "reset"; sessionId: string | null } | { type: "event"; event: ServerEvent };

function reduce(state: ChatState, action: Action): ChatState {
  return action.type === "reset" ? initialChatState(action.sessionId) : applyEvent(state, action.event);
}

function Bubble({ message }: { message: ChatMessage }) {
  const assistant = message.role === "assistant";
  return (
    <li className={`chat-msg ${assistant ? "chat-assistant" : "chat-user"} chat-${message.status}`} data-message-id={message.message_id} data-status={message.status}>
      <span className="chat-who">{assistant ? "TrackFlow support" : "You"}</span>
      <div className="chat-text">
        {message.text}
        {message.status === "streaming" && <span className="chat-caret" aria-hidden="true" />}
        {message.status === "streaming" && message.text === "" && <span className="chat-thinking">Thinking…</span>}
      </div>
      {message.status === "interrupted" && <span className="chat-badge chat-badge-interrupted">Interrupted: you changed the subject</span>}
      {message.status === "failed" && <span className="chat-badge chat-badge-failed">The agent could not finish this answer</span>}
    </li>
  );
}

/**
 * The support agent as a conversation: its answer arrives token by token over a WebSocket and you can interrupt it mid-sentence (typing a new message while it answers redirects it).
 * An interrupted answer stays on screen, marked, and the redirected reply is a new message. If the connection drops it reconnects by itself and restores the conversation.
 */
export default function KnowledgeAssistantPage() {
  const [state, dispatch] = useReducer(reduce, null, () => initialChatState(null));
  const [status, setStatus] = useState<SocketStatus>({ state: "connecting", attempt: 0, nextRetryMs: null });
  const [text, setText] = useState("");
  const [notice, setNotice] = useState("");
  const [startError, setStartError] = useState("");
  const [conversation, setConversation] = useState(0);
  const client = useRef<ChatSocketClient | null>(null);
  const log = useRef<HTMLOListElement>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  // Find (or create) the conversation, then attach the socket to it. A reload re-attaches to the same one: the server rehydrates it.
  useEffect(() => {
    let cancelled = false;
    let socket: ChatSocketClient | null = null;
    async function start() {
      const token = getAccessToken();
      if (!token) return;
      setStartError("");
      let sessionId = conversation === 0 ? rememberedSession() : null;
      try {
        if (sessionId) {
          try {
            const found = await chatApi.get(token, sessionId);
            if (found.status === "closed") sessionId = null;
          } catch (caught) {
            if (caught instanceof ApiError && caught.status === 404) sessionId = null;
            else throw caught;
          }
        }
        if (!sessionId) sessionId = (await chatApi.create(token)).session_id;
      } catch (caught) {
        if (!cancelled) setStartError(`The chat could not start. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
        return;
      }
      if (cancelled) return;
      rememberSession(sessionId);
      dispatch({ type: "reset", sessionId });
      socket = new ChatSocketClient({
        url: socketUrl(API_BASE_URL, sessionId),
        getToken: getAccessToken,
        onEvent: (event) => dispatch({ type: "event", event }),
        onStatus: setStatus,
        isOnline: () => (typeof navigator === "undefined" ? true : navigator.onLine),
      });
      client.current = socket;
      void socket.start();
    }
    void start();
    const onOnline = () => socket?.nudge();
    window.addEventListener("online", onOnline);
    return () => {
      cancelled = true;
      window.removeEventListener("online", onOnline);
      socket?.stop();
      client.current = null;
    };
  }, [conversation]);

  useEffect(() => {
    const element = log.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [state.messages]);

  const send = useCallback((event: string, data: Record<string, unknown>): boolean => client.current?.send(event, data) ?? false, []);

  function submit(raw: string) {
    const { question, problem } = cleanQuestion(raw);
    if (problem) {
      setNotice(problem);
      return;
    }
    const sessionId = stateRef.current.sessionId;
    if (!sessionId) return;
    // While the agent is answering, a new message IS the interruption: it aborts the answer and redirects the agent.
    const sent = stateRef.current.generating ? send("interrupt_requested", { session_id: sessionId, new_input: question }) : send("user_message", { session_id: sessionId, text: question });
    if (!sent) {
      setNotice("You are not connected right now, so this was not sent. The chat reconnects by itself; send it again in a moment.");
      return;
    }
    setNotice("");
    setText("");
  }

  function stop() {
    const sessionId = stateRef.current.sessionId;
    if (sessionId) send("interrupt_requested", { session_id: sessionId, new_input: "" });
  }

  async function newConversation() {
    const token = getAccessToken();
    const sessionId = stateRef.current.sessionId;
    if (token && sessionId) await chatApi.close(token, sessionId).catch(() => undefined);
    rememberSession(null);
    setText("");
    setNotice("");
    setConversation((value) => value + 1);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    submit(text);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit(text);
    }
  }

  const connected = status.state === "connected";
  const writing = streamingMessage(state) !== null;
  const banner = notice || state.lastError || startError;

  return (
    <>
      <header className="inventory-page-header">
        <div>
          <p className="inventory-eyebrow">SUPPORT AGENT · LIVE</p>
          <h1>Ask TrackFlow</h1>
          <p>Talk to the first-line support agent about tracking, returns and delivery. The answer appears as it is written; if it is heading the wrong way, just type what you meant and it changes course.</p>
        </div>
      </header>

      <section className="chat-panel" aria-label="Conversation with the support agent">
        <div className="chat-bar">
          <span className={`rfp-live-pill rfp-live-pill-${status.state}`} role="status" aria-live="polite"><span aria-hidden="true" />{socketStatusText(status)}</span>
          {connected && !state.hydrated && <span className="incident-muted">Restoring the conversation…</span>}
          <button className="inventory-secondary-button chat-new" type="button" onClick={() => void newConversation()}>New conversation</button>
        </div>

        <ol className="chat-log" ref={log} role="log" aria-live="polite" aria-label="Messages">
          {state.messages.length === 0 && <li className="chat-empty">{state.hydrated || !state.sessionId ? "Ask something to start. For example:" : "Restoring the conversation…"}</li>}
          {state.messages.map((message) => <Bubble key={message.message_id} message={message} />)}
        </ol>

        {banner && <p className="inventory-alert" role="alert">{banner}</p>}

        <form className="chat-form" onSubmit={onSubmit} noValidate>
          <label className="visually-hidden" htmlFor="chat-input">Your message</label>
          <textarea
            id="chat-input"
            rows={2}
            maxLength={1000}
            value={text}
            placeholder={writing ? "Type what you meant: it interrupts the answer and redirects the agent" : "Ask about tracking, returns or delivery"}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={onKeyDown}
            disabled={status.state === "unauthorized" || status.state === "unavailable"}
          />
          <div className="chat-actions">
            <button className="inventory-primary-button" type="submit" disabled={!text.trim() || !connected || !state.hydrated}>{writing ? "Interrupt and send" : "Send"}</button>
            {writing && <button className="inventory-secondary-button" type="button" onClick={stop}>Stop</button>}
            <span className="incident-muted">{text.length} / 1000</span>
          </div>
        </form>

        {state.messages.length === 0 && (
          <div className="analysis-period">
            {EXAMPLE_QUESTIONS.map((example) => (
              <button key={example} className="inventory-secondary-button" type="button" disabled={!connected || !state.hydrated} onClick={() => submit(example)}>{example}</button>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
