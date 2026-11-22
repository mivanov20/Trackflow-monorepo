import { apiRequest } from "./api-client";

/** The ChatSession entity of the chat (services/routers/chat.py): the same field names as the API and the WebSocket contract. */
export interface ChatSession {
  session_id: string;
  agent_id: string;
  user_id: string;
  client_id: string;
  status: "active" | "interrupted" | "closed";
  created_at: string;
}

export const SESSION_STORAGE_KEY = "trackflow-chat-session";

export const chatApi = {
  create(token: string): Promise<ChatSession> {
    return apiRequest<ChatSession>("/chat/sessions", { method: "POST", token, body: {}, timeoutMs: 20_000, serviceName: "chat" });
  },
  get(token: string, sessionId: string): Promise<ChatSession> {
    return apiRequest<ChatSession>(`/chat/sessions/${encodeURIComponent(sessionId)}`, { token, timeoutMs: 20_000, serviceName: "chat" });
  },
  close(token: string, sessionId: string): Promise<ChatSession> {
    return apiRequest<ChatSession>(`/chat/sessions/${encodeURIComponent(sessionId)}/close`, { method: "POST", token, timeoutMs: 20_000, serviceName: "chat" });
  },
};

/** The session the browser was in, so a page reload (or a tab that comes back) re-attaches to the same conversation instead of starting an empty one. */
export function rememberSession(sessionId: string | null): void {
  try {
    if (sessionId) window.sessionStorage.setItem(SESSION_STORAGE_KEY, sessionId);
    else window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    // storage blocked: the chat still works, it just starts a new conversation after a reload
  }
}

export function rememberedSession(): string | null {
  try {
    return window.sessionStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}
