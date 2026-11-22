"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE_URL } from "../../lib/api-client";
import { getAccessToken } from "../../lib/auth";
import { rfpApi } from "../../lib/rfp";
import { RfpStreamClient, feedTicketsFromList, mergeTickets, type FeedTicket, type StreamStatus } from "../../lib/rfp-stream";

/** How long a new ticket stays highlighted. */
const FRESH_MS = 12_000;

/**
 * The live feed of RFP tickets: the SSE stream read with `fetch` (JWT in the Authorization header), reconnected with progressive backoff, and recovered after every (re)connection by refetching the
 * list ONCE (not on every event). The same ticket never appears twice: everything is merged by `ticket_id`.
 */
export function useRfpTicketFeed() {
  const [tickets, setTickets] = useState<FeedTicket[]>([]);
  const [status, setStatus] = useState<StreamStatus>({ state: "connecting", attempt: 0, nextRetryMs: null });
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    const client = new RfpStreamClient({
      url: `${API_BASE_URL}/rfp/events`,
      getToken: getAccessToken,
      onEvent: (ticket) => setTickets((current) => mergeTickets(current, [{ ...ticket, fresh: true, source: "stream" }])),
      onStatus: setStatus,
      // Recovery: what was registered while the screen was disconnected is not lost. The list is refetched once per connection; on a RECONNECTION the tickets that were not on screen yet are highlighted.
      onConnected: async ({ reconnect }) => {
        const token = getAccessToken();
        if (!token) return;
        const rows = await rfpApi.list(token);
        setTickets((current) => mergeTickets(current, feedTicketsFromList(rows).map((ticket) => ({ ...ticket, fresh: reconnect }))));
      },
      isOnline: () => (typeof navigator === "undefined" ? true : navigator.onLine),
    });
    void client.start();
    const onOnline = () => client.nudge(); // the network is back: do not wait out the backoff
    window.addEventListener("online", onOnline);
    return () => {
      window.removeEventListener("online", onOnline);
      client.stop();
    };
  }, []);

  // the highlight fades by itself
  useEffect(() => {
    if (!tickets.some((ticket) => ticket.fresh)) return;
    const timer = setTimeout(() => setTickets((current) => current.map((ticket) => (ticket.fresh ? { ...ticket, fresh: false } : ticket))), FRESH_MS);
    return () => clearTimeout(timer);
  }, [tickets]);

  const dismiss = useCallback((ticketId: string) => setDismissed((current) => new Set(current).add(ticketId)), []);
  return { tickets: tickets.filter((ticket) => !dismissed.has(ticket.ticket_id)), status, dismiss };
}
