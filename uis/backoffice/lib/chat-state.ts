/**
 * The state of the chat on the client, as a pure reducer over the WebSocket events (no React, no DOM: tested in Node).
 *
 * The rules that matter:
 *  - a token is applied only to a message that is `streaming` and only if its `sequence` is newer than the last one applied: after an interruption no token of the old message can change it,
 *    and the tokens that a snapshot already contains (a reconnection in the middle of an answer) are not applied twice;
 *  - an interrupted message is KEPT with the text it had and marked `interrupted`; the redirected reply is a new message;
 *  - `session_snapshot` REPLACES the local conversation: that is how a reconnection restores the thread (history, and the text so far of an answer still being written).
 */

export type MessageStatus = "completed" | "streaming" | "interrupted" | "failed";
export type SessionStatus = "active" | "interrupted" | "closed";

export interface ChatMessage {
  message_id: string;
  role: "user" | "assistant";
  text: string;
  status: MessageStatus;
  /** The last token `sequence` applied to this message (0 for a message that has none). */
  token_sequence: number;
}

export interface ChatState {
  sessionId: string | null;
  status: SessionStatus;
  messages: ChatMessage[];
  /** True once a snapshot of the conversation has been received on the current connection's behalf: the thread is restored. */
  hydrated: boolean;
  /** An assistant message is being written right now. */
  generating: boolean;
  lastError: string | null;
}

export interface ServerEvent {
  event: string;
  data: Record<string, unknown>;
}

export function initialChatState(sessionId: string | null = null): ChatState {
  return { sessionId, status: "active", messages: [], hydrated: false, generating: false, lastError: null };
}

function asMessage(raw: Record<string, unknown>): ChatMessage | null {
  if (typeof raw.message_id !== "string" || (raw.role !== "user" && raw.role !== "assistant")) return null;
  const status = (["completed", "streaming", "interrupted", "failed"] as const).find((candidate) => candidate === raw.status) ?? "completed";
  return { message_id: raw.message_id, role: raw.role, text: typeof raw.text === "string" ? raw.text : "", status, token_sequence: typeof raw.token_sequence === "number" ? raw.token_sequence : 0 };
}

function update(state: ChatState, messageId: string, change: (message: ChatMessage) => ChatMessage): ChatState {
  const messages = state.messages.map((message) => (message.message_id === messageId ? change(message) : message));
  return { ...state, messages, generating: messages.some((message) => message.status === "streaming") };
}

export function applyEvent(state: ChatState, event: ServerEvent): ChatState {
  const { data } = event;
  const id = typeof data.message_id === "string" ? data.message_id : "";
  switch (event.event) {
    case "session_snapshot": {
      const messages = (Array.isArray(data.messages) ? (data.messages as Record<string, unknown>[]) : []).map(asMessage).filter((message): message is ChatMessage => message !== null);
      const status = (["active", "interrupted", "closed"] as const).find((candidate) => candidate === data.status) ?? "active";
      return { ...state, sessionId: typeof data.session_id === "string" ? data.session_id : state.sessionId, status, messages, hydrated: true, generating: messages.some((message) => message.status === "streaming"), lastError: null };
    }
    case "user_message": {
      if (!id || state.messages.some((message) => message.message_id === id)) return state;
      return { ...state, status: "active", messages: [...state.messages, { message_id: id, role: "user", text: String(data.text ?? ""), status: "completed", token_sequence: 0 }], lastError: null };
    }
    case "generation_started": {
      if (!id || state.messages.some((message) => message.message_id === id)) return state;
      return { ...state, status: "active", generating: true, messages: [...state.messages, { message_id: id, role: "assistant", text: "", status: "streaming", token_sequence: 0 }] };
    }
    case "token_chunk": {
      const sequence = typeof data.sequence === "number" ? data.sequence : 0;
      const known = state.messages.find((message) => message.message_id === id);
      if (!known || known.status !== "streaming" || sequence <= known.token_sequence) return state; // an old or repeated token, or one of a message that is over: ignored
      return update(state, id, (message) => ({ ...message, text: message.text + String(data.token ?? ""), token_sequence: sequence }));
    }
    case "generation_completed":
      return update(state, id, (message) => ({ ...message, status: "completed", text: typeof data.text === "string" ? data.text : message.text }));
    case "generation_interrupted": {
      const next = update(state, id, (message) => ({ ...message, status: "interrupted", text: typeof data.text === "string" && data.text.length >= message.text.length ? data.text : message.text }));
      return { ...next, status: "interrupted" };
    }
    case "generation_failed":
      return { ...update(state, id, (message) => ({ ...message, status: "failed" })), lastError: typeof data.error === "string" ? data.error : "The agent could not answer." };
    case "error":
      return { ...state, lastError: typeof data.message === "string" ? data.message : "Something went wrong." };
    default:
      return state; // pong and anything this client does not know
  }
}

/** The message of the assistant that is being written, if any. */
export function streamingMessage(state: ChatState): ChatMessage | null {
  return state.messages.find((message) => message.status === "streaming") ?? null;
}
