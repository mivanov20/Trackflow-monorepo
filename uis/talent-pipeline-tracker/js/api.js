/* Talent Pipeline Tracker — REST client for the candidate pipeline API. */
(function (global) {
  "use strict";

  const BASE_URL = "https://playground.4geeks.com/tracker/api/v1";
  const REQUEST_TIMEOUT_MS = 15000;

  const STATUSES = [
    { value: "received", label: "Received" },
    { value: "in_progress", label: "In progress" },
    { value: "selected", label: "Selected" },
    { value: "discarded", label: "Discarded" }
  ];

  const STAGES = [
    { value: "pending", label: "Pending" },
    { value: "review", label: "Review" },
    { value: "personal_interview", label: "Personal interview" },
    { value: "technical_interview", label: "Technical interview" },
    { value: "offer_presented", label: "Offer presented" }
  ];

  class ApiError extends Error {
    constructor(message, status, details) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.details = details || null;
    }
  }

  function buildQuery(params) {
    const search = new URLSearchParams();
    Object.keys(params || {}).forEach((key) => {
      const value = params[key];
      if (value !== undefined && value !== null && String(value).trim() !== "") {
        search.set(key, String(value).trim());
      }
    });
    const qs = search.toString();
    return qs ? `?${qs}` : "";
  }

  // FastAPI returns `detail` either as a string or as a list of validation errors.
  function readErrorMessage(payload, status) {
    // 5xx: never forward the server text (it may be a stack trace or an HTML error page).
    if (status >= 500) return "The server is having trouble right now. Please try again.";
    if (payload && typeof payload.detail === "string") return payload.detail;
    if (payload && Array.isArray(payload.detail)) {
      const first = payload.detail[0];
      if (first && first.msg) {
        const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : "";
        return field
          ? `Please check the ${String(field).replace(/_/g, " ")} field and try again.`
          : "Some of the information is not valid. Please review the form and try again.";
      }
    }
    if (status === 404) return "The requested record no longer exists.";
    return "The request could not be completed. Please try again.";
  }

  function plainFieldMessage(item) {
    const type = String((item && item.type) || "");
    if (type === "missing") return "This field is required.";
    if (type.includes("email")) return "Enter a valid email address.";
    if (type.includes("url")) return "Enter a valid http(s) link.";
    if (type.includes("int") || type.includes("float") || type.includes("number")) return "Enter a valid number.";
    if (type.includes("too_short") || type.includes("too_long")) return "This value has the wrong length.";
    return "This value is not valid.";
  }

  function fieldErrorsFrom(payload) {
    if (!payload || !Array.isArray(payload.detail)) return {};
    return payload.detail.reduce((acc, item) => {
      const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null;
      // Plain language per field: raw Pydantic text ("value is not a valid ...") is never shown.
      if (field && !acc[field]) acc[field] = plainFieldMessage(item);
      return acc;
    }, {});
  }

  async function request(path, options) {
    const config = options || {};
    const controller = new AbortController();
    const externalSignal = config.signal;
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener("abort", () => controller.abort(), { once: true });
    }
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    let response;
    try {
      response = await fetch(`${BASE_URL}${path}`, {
        method: config.method || "GET",
        headers: config.body ? { "Content-Type": "application/json" } : undefined,
        body: config.body ? JSON.stringify(config.body) : undefined,
        signal: controller.signal
      });
    } catch (error) {
      clearTimeout(timeoutId);
      if (error && error.name === "AbortError") {
        if (externalSignal && externalSignal.aborted) throw error;
        throw new ApiError("The request took too long. Check your connection and try again.", 0);
      }
      throw new ApiError("We couldn't reach the pipeline API. Check your connection and try again.", 0);
    }
    clearTimeout(timeoutId);

    if (response.status === 204) return null;

    let payload = null;
    const text = await response.text();
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        payload = null;
      }
    }

    if (!response.ok) {
      throw new ApiError(readErrorMessage(payload, response.status), response.status, fieldErrorsFrom(payload));
    }

    return payload;
  }

  const api = {
    BASE_URL,
    STATUSES,
    STAGES,
    ApiError,

    statusLabel(value) {
      const found = STATUSES.find((item) => item.value === value);
      return found ? found.label : value || "—";
    },

    stageLabel(value) {
      const found = STAGES.find((item) => item.value === value);
      return found ? found.label : value || "—";
    },

    listRecords(params, signal) {
      return request(`/records${buildQuery(params)}`, { signal });
    },

    getRecord(id, signal) {
      return request(`/records/${encodeURIComponent(id)}`, { signal });
    },

    createRecord(payload) {
      return request("/records", { method: "POST", body: payload });
    },

    replaceRecord(id, payload) {
      return request(`/records/${encodeURIComponent(id)}`, { method: "PUT", body: payload });
    },

    patchRecord(id, payload) {
      return request(`/records/${encodeURIComponent(id)}`, { method: "PATCH", body: payload });
    },

    deleteRecord(id) {
      return request(`/records/${encodeURIComponent(id)}`, { method: "DELETE" });
    },

    listNotes(id, signal) {
      return request(`/records/${encodeURIComponent(id)}/notes`, { signal });
    },

    addNote(id, content) {
      return request(`/records/${encodeURIComponent(id)}/notes`, { method: "POST", body: { content } });
    },

    deleteNote(id, noteId) {
      return request(`/records/${encodeURIComponent(id)}/notes/${encodeURIComponent(noteId)}`, { method: "DELETE" });
    }
  };

  global.TrackerApi = api;
})(window);
