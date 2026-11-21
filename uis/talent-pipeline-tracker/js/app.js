/* Talent Pipeline Tracker — application controller. */
(function (global) {
  "use strict";

  const api = global.TrackerApi;
  const ui = global.TrackerUI;

  const PAGE_SIZE = 12;

  const state = {
    filters: { search: "", status: "", stage: "" },
    page: 1,
    total: 0,
    records: [],
    loading: false,
    current: null,
    listRequest: null,
    detailRequest: null,
    formMode: "create",
    lastFocused: null
  };

  const el = {
    summaryList: document.getElementById("summaryList"),
    filtersForm: document.getElementById("filtersForm"),
    searchInput: document.getElementById("searchInput"),
    statusFilter: document.getElementById("statusFilter"),
    stageFilter: document.getElementById("stageFilter"),
    clearFiltersBtn: document.getElementById("clearFiltersBtn"),
    resultsSummary: document.getElementById("resultsSummary"),
    list: document.getElementById("candidateList"),
    listError: document.getElementById("listError"),
    listErrorMessage: document.getElementById("listErrorMessage"),
    listEmpty: document.getElementById("listEmpty"),
    retryBtn: document.getElementById("retryBtn"),
    pagination: document.getElementById("pagination"),
    prevPageBtn: document.getElementById("prevPageBtn"),
    nextPageBtn: document.getElementById("nextPageBtn"),
    pageIndicator: document.getElementById("pageIndicator"),
    newCandidateBtn: document.getElementById("newCandidateBtn"),
    detailOverlay: document.getElementById("detailOverlay"),
    detailPanel: document.getElementById("detailPanel"),
    detailName: document.getElementById("detailName"),
    detailPosition: document.getElementById("detailPosition"),
    detailBody: document.getElementById("detailBody"),
    formOverlay: document.getElementById("formOverlay"),
    formModal: document.getElementById("formModal"),
    formTitle: document.getElementById("formTitle"),
    form: document.getElementById("candidateForm"),
    formError: document.getElementById("formError"),
    formSubmitBtn: document.getElementById("formSubmitBtn")
  };

  /* ---------------------------------------------------------------- list */

  function readFiltersFromUrl() {
    const params = new URLSearchParams(global.location.search);
    state.filters.search = params.get("search") || "";
    state.filters.status = params.get("status") || "";
    state.filters.stage = params.get("stage") || "";
    const page = parseInt(params.get("page"), 10);
    state.page = Number.isInteger(page) && page > 0 ? page : 1;
  }

  function writeFiltersToUrl() {
    const params = new URLSearchParams();
    if (state.filters.search) params.set("search", state.filters.search);
    if (state.filters.status) params.set("status", state.filters.status);
    if (state.filters.stage) params.set("stage", state.filters.stage);
    if (state.page > 1) params.set("page", String(state.page));
    const qs = params.toString();
    global.history.replaceState(null, "", qs ? `?${qs}` : global.location.pathname);
  }

  function totalPages() {
    return Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  }

  function renderList() {
    if (state.loading) {
      el.list.innerHTML = ui.skeletonRows(6);
      el.list.setAttribute("aria-busy", "true");
      el.listEmpty.classList.add("hidden");
      el.pagination.classList.add("hidden");
      return;
    }

    el.list.setAttribute("aria-busy", "false");

    if (!state.records.length) {
      el.list.innerHTML = "";
      el.listEmpty.classList.remove("hidden");
      el.pagination.classList.add("hidden");
      return;
    }

    el.listEmpty.classList.add("hidden");
    el.list.innerHTML = state.records
      .map(
        (record) => `
        <li>
          <article
            class="group flex cursor-pointer flex-wrap items-center gap-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-indigo-300 hover:shadow-md"
            data-record-id="${ui.escapeHtml(record.id)}"
          >
            <div class="min-w-0 flex-1">
              <h3 class="truncate text-base font-semibold text-slate-900">${ui.escapeHtml(record.full_name)}</h3>
              <p class="truncate text-sm text-slate-500">${ui.escapeHtml(record.position)} · ${ui.escapeHtml(
          record.email
        )}</p>
            </div>
            <div class="flex flex-wrap items-center gap-2">
              ${ui.statusBadge(record.status)}
              ${ui.stageBadge(record.stage)}
            </div>
            <div class="hidden text-right text-xs text-slate-400 sm:block">
              <p>Applied ${ui.escapeHtml(ui.formatDate(record.applied_at))}</p>
              <p>${record.notes_count || 0} note${record.notes_count === 1 ? "" : "s"}</p>
            </div>
            <button
              type="button"
              class="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700 transition group-hover:border-indigo-400 group-hover:text-indigo-600"
              data-open-detail="${ui.escapeHtml(record.id)}"
            >
              View
            </button>
          </article>
        </li>`
      )
      .join("");

    const pages = totalPages();
    el.pageIndicator.textContent = `Page ${state.page} of ${pages}`;
    el.prevPageBtn.disabled = state.page <= 1;
    el.nextPageBtn.disabled = state.page >= pages;
    el.pagination.classList.toggle("hidden", pages <= 1);
    el.pagination.classList.toggle("flex", pages > 1);
  }

  function renderResultsSummary() {
    if (state.loading) {
      el.resultsSummary.textContent = "Loading candidates…";
      return;
    }
    const filtered = state.filters.search || state.filters.status || state.filters.stage;
    el.resultsSummary.textContent = `${state.total} candidate${state.total === 1 ? "" : "s"}${
      filtered ? " matching the current filters" : " in the pipeline"
    }`;
  }

  async function loadRecords() {
    if (state.listRequest) state.listRequest.abort();
    const controller = new AbortController();
    state.listRequest = controller;

    state.loading = true;
    el.listError.classList.add("hidden");
    renderList();
    renderResultsSummary();

    try {
      const result = await api.listRecords(
        {
          search: state.filters.search,
          status: state.filters.status,
          stage: state.filters.stage,
          page: state.page,
          limit: PAGE_SIZE
        },
        controller.signal
      );
      state.records = Array.isArray(result && result.data) ? result.data : [];
      state.total = (result && result.total) || 0;

      // The API can report a page past the end after deletions or filter changes.
      if (!state.records.length && state.page > 1 && state.total > 0) {
        state.loading = false;
        state.page = Math.min(state.page, totalPages());
        writeFiltersToUrl();
        return loadRecords();
      }

      state.loading = false;
      renderList();
      renderResultsSummary();
    } catch (error) {
      if (error && error.name === "AbortError") return;
      state.loading = false;
      state.records = [];
      el.list.innerHTML = "";
      el.list.setAttribute("aria-busy", "false");
      el.listEmpty.classList.add("hidden");
      el.pagination.classList.add("hidden");
      el.listErrorMessage.textContent = error.message;
      el.listError.classList.remove("hidden");
      el.resultsSummary.textContent = "Could not load candidates.";
    } finally {
      if (state.listRequest === controller) state.listRequest = null;
    }
  }

  /* ------------------------------------------------------------- summary */

  function renderSummary(items) {
    el.summaryList.innerHTML = items
      .map(
        (item) => `
        <li class="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
          <p class="text-xs font-semibold uppercase tracking-wide text-slate-500">${ui.escapeHtml(item.label)}</p>
          <p class="mt-1 text-2xl font-bold text-slate-900">${
            item.count === null ? '<span class="skeleton inline-block h-6 w-10 rounded"></span>' : item.count
          }</p>
        </li>`
      )
      .join("");
  }

  async function loadSummary() {
    const buckets = [{ value: "", label: "Total" }].concat(api.STATUSES);
    renderSummary(buckets.map((bucket) => ({ label: bucket.label, count: null })));

    const results = await Promise.all(
      buckets.map((bucket) =>
        api
          .listRecords({ status: bucket.value, limit: 1 })
          .then((res) => (res && res.total) || 0)
          .catch(() => null)
      )
    );

    renderSummary(
      buckets.map((bucket, index) => ({
        label: bucket.label,
        count: results[index] === null ? "—" : results[index]
      }))
    );
    // If any count failed, say so and offer a retry instead of silently showing dashes.
    if (results.some((value) => value === null)) {
      el.summaryList.insertAdjacentHTML(
        "beforeend",
        `<li class="col-span-full rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">
          Some pipeline totals could not be loaded. Check your connection and try again.
          <button type="button" data-retry-summary class="ml-2 rounded-lg bg-red-600 px-3 py-1 font-semibold text-white hover:bg-red-700">Try again</button>
        </li>`
      );
    }
  }

  /* -------------------------------------------------------------- detail */

  function openDetail(id) {
    state.lastFocused = document.activeElement;
    el.detailOverlay.classList.remove("hidden");
    el.detailPanel.classList.remove("hidden");
    document.body.classList.add("no-scroll");
    el.detailPanel.focus();
    el.detailName.textContent = "Loading…";
    el.detailPosition.textContent = "";
    el.detailBody.innerHTML = ui.skeletonRows(3);
    loadDetail(id);
  }

  function closeDetail() {
    if (state.detailRequest) state.detailRequest.abort();
    el.detailOverlay.classList.add("hidden");
    el.detailPanel.classList.add("hidden");
    document.body.classList.remove("no-scroll");
    state.current = null;
    if (state.lastFocused && state.lastFocused.focus) state.lastFocused.focus();
  }

  async function loadDetail(id) {
    if (state.detailRequest) state.detailRequest.abort();
    const controller = new AbortController();
    state.detailRequest = controller;
    try {
      // The detail endpoint returns notes_count but not the notes themselves.
      const [record, notes] = await Promise.all([
        api.getRecord(id, controller.signal),
        api.listNotes(id, controller.signal).catch(() => null)
      ]);
      record.notes = normalizeNotes(notes);
      state.current = record;
      renderDetail(record);
    } catch (error) {
      if (error && error.name === "AbortError") return;
      el.detailName.textContent = "Candidate unavailable";
      el.detailBody.innerHTML = `
        <div class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800" role="alert">
          <p>${ui.escapeHtml(error.message)}</p>
          <button type="button" data-retry-detail="${ui.escapeHtml(
            id
          )}" class="mt-3 rounded-lg bg-red-600 px-3 py-1.5 font-semibold text-white hover:bg-red-700">Try again</button>
        </div>`;
    } finally {
      if (state.detailRequest === controller) state.detailRequest = null;
    }
  }

  function optionsHtml(options, selected) {
    return options
      .map(
        (option) =>
          `<option value="${ui.escapeHtml(option.value)}"${option.value === selected ? " selected" : ""}>${ui.escapeHtml(
            option.label
          )}</option>`
      )
      .join("");
  }

  function contactRow(label, value, href) {
    const safeHref = href ? ui.safeUrl(href) : null;
    const content = safeHref
      ? `<a class="text-indigo-600 hover:underline" href="${ui.escapeHtml(
          safeHref
        )}" target="_blank" rel="noopener noreferrer">${ui.escapeHtml(value)}</a>`
      : ui.escapeHtml(value || "—");
    return `<div><dt class="text-xs font-semibold uppercase tracking-wide text-slate-500">${ui.escapeHtml(
      label
    )}</dt><dd class="mt-0.5 break-words text-sm text-slate-800">${content}</dd></div>`;
  }

  function normalizeNotes(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.data)) return payload.data;
    return [];
  }

  function notesHtml(notes) {
    if (!notes || !notes.length) {
      return '<p class="rounded-lg border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-500">No internal notes yet.</p>';
    }
    return `<ul class="grid gap-2">${notes
      .map(
        (note) => `
        <li class="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <p class="whitespace-pre-wrap text-sm text-slate-800">${ui.escapeHtml(note.content)}</p>
          <div class="mt-2 flex items-center justify-between gap-3">
            <span class="text-xs text-slate-500">${ui.escapeHtml(ui.formatDateTime(note.created_at))}</span>
            <button type="button" data-delete-note="${ui.escapeHtml(
              note.id
            )}" class="text-xs font-semibold text-red-600 hover:underline">Delete</button>
          </div>
        </li>`
      )
      .join("")}</ul>`;
  }

  function renderDetail(record) {
    el.detailName.textContent = record.full_name;
    el.detailPosition.textContent = `${record.position} · ${record.experience_years} yrs of experience`;

    el.detailBody.innerHTML = `
      <section class="rounded-xl border border-slate-200 p-4">
        <h3 class="text-sm font-bold text-slate-900">Pipeline</h3>
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label for="detailStatus" class="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Status</label>
            <select id="detailStatus" class="form-input">${optionsHtml(api.STATUSES, record.status)}</select>
          </div>
          <div>
            <label for="detailStage" class="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">Stage</label>
            <select id="detailStage" class="form-input">${optionsHtml(api.STAGES, record.stage)}</select>
          </div>
        </div>
        <p id="pipelineFeedback" class="mt-2 text-xs text-slate-500" aria-live="polite">Changes are saved automatically.</p>
      </section>

      <section class="mt-4 rounded-xl border border-slate-200 p-4">
        <div class="flex items-center justify-between gap-3">
          <h3 class="text-sm font-bold text-slate-900">Candidate data</h3>
          <button type="button" id="editCandidateBtn" class="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50">Edit</button>
        </div>
        <dl class="mt-3 grid gap-3 sm:grid-cols-2">
          ${contactRow("Email", record.email, `mailto:${record.email}`)}
          ${contactRow("Phone", record.phone)}
          ${contactRow("LinkedIn", record.linkedin_url || "—", record.linkedin_url)}
          ${contactRow("CV", record.cv_url ? "Open CV" : "—", record.cv_url)}
          ${contactRow("Applied", ui.formatDate(record.applied_at))}
          ${contactRow("Last update", ui.formatDateTime(record.updated_at))}
        </dl>
      </section>

      <section class="mt-4 rounded-xl border border-slate-200 p-4">
        <h3 class="text-sm font-bold text-slate-900">Internal notes</h3>
        <form id="noteForm" class="mt-3 grid gap-2">
          <label class="sr-only" for="noteInput">New note</label>
          <textarea id="noteInput" rows="3" maxlength="1000" placeholder="Add an internal note…" class="form-input"></textarea>
          <div class="flex justify-end">
            <button id="noteSubmitBtn" type="submit" class="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-60">Add note</button>
          </div>
        </form>
        <div id="notesContainer" class="mt-3">${notesHtml(record.notes)}</div>
      </section>

      <section class="mt-4 rounded-xl border border-red-200 bg-red-50 p-4">
        <h3 class="text-sm font-bold text-red-900">Danger zone</h3>
        <p class="mt-1 text-xs text-red-800">Removing a candidate deletes their notes and history permanently.</p>
        <button type="button" id="deleteCandidateBtn" class="mt-3 rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-60">Delete candidate</button>
      </section>`;

    document.getElementById("detailStatus").addEventListener("change", (event) => {
      patchPipeline({ status: event.target.value });
    });
    document.getElementById("detailStage").addEventListener("change", (event) => {
      patchPipeline({ stage: event.target.value });
    });
    document.getElementById("editCandidateBtn").addEventListener("click", () => openForm("edit", state.current));
    document.getElementById("noteForm").addEventListener("submit", submitNote);
    document.getElementById("deleteCandidateBtn").addEventListener("click", deleteCandidate);
  }

  async function patchPipeline(changes) {
    if (!state.current) return;
    const feedback = document.getElementById("pipelineFeedback");
    const statusSelect = document.getElementById("detailStatus");
    const stageSelect = document.getElementById("detailStage");
    const previous = { status: state.current.status, stage: state.current.stage };

    statusSelect.disabled = true;
    stageSelect.disabled = true;
    feedback.textContent = "Saving…";
    feedback.className = "mt-2 text-xs text-slate-500";

    try {
      const updated = await api.patchRecord(state.current.id, changes);
      state.current = Object.assign({}, state.current, updated || changes);
      feedback.textContent = "Saved.";
      feedback.className = "mt-2 text-xs font-semibold text-emerald-600";
      ui.toast("Pipeline updated.", "success");
      loadRecords();
      loadSummary();
    } catch (error) {
      statusSelect.value = previous.status;
      stageSelect.value = previous.stage;
      feedback.textContent = error.message;
      feedback.className = "mt-2 text-xs font-semibold text-red-600";
      ui.toast(error.message, "error");
    } finally {
      statusSelect.disabled = false;
      stageSelect.disabled = false;
    }
  }

  async function refreshNotes() {
    const container = document.getElementById("notesContainer");
    if (!container || !state.current) return;
    try {
      const list = normalizeNotes(await api.listNotes(state.current.id));
      state.current.notes = list;
      container.innerHTML = notesHtml(list);
    } catch (error) {
      ui.toast(error.message, "error");
    }
  }

  async function submitNote(event) {
    event.preventDefault();
    if (!state.current) return;
    const input = document.getElementById("noteInput");
    const button = document.getElementById("noteSubmitBtn");
    const content = input.value.trim();
    if (!content) {
      ui.toast("Write something before adding the note.", "error");
      input.focus();
      return;
    }

    button.disabled = true;
    button.textContent = "Adding…";
    try {
      await api.addNote(state.current.id, content);
      input.value = "";
      await refreshNotes();
      ui.toast("Note added.", "success");
      loadRecords();
    } catch (error) {
      ui.toast(error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Add note";
    }
  }

  async function deleteNote(noteId, button) {
    if (!state.current) return;
    if (!global.confirm("Delete this note? This cannot be undone.")) return;
    button.disabled = true;
    button.textContent = "Deleting…";
    try {
      await api.deleteNote(state.current.id, noteId);
      await refreshNotes();
      ui.toast("Note deleted.", "success");
      loadRecords();
    } catch (error) {
      button.disabled = false;
      button.textContent = "Delete";
      ui.toast(error.message, "error");
    }
  }

  async function deleteCandidate() {
    if (!state.current) return;
    if (!global.confirm(`Delete ${state.current.full_name} from the pipeline? This cannot be undone.`)) return;
    const button = document.getElementById("deleteCandidateBtn");
    button.disabled = true;
    button.textContent = "Deleting…";
    try {
      await api.deleteRecord(state.current.id);
      closeDetail();
      ui.toast("Candidate deleted.", "success");
      loadRecords();
      loadSummary();
    } catch (error) {
      button.disabled = false;
      button.textContent = "Delete candidate";
      ui.toast(error.message, "error");
    }
  }

  /* ---------------------------------------------------------------- form */

  const FIELDS = ["full_name", "email", "phone", "position", "experience_years", "linkedin_url", "cv_url"];

  function clearFormErrors() {
    el.formError.classList.add("hidden");
    el.formError.textContent = "";
    FIELDS.forEach((field) => {
      const message = el.form.querySelector(`[data-error-for="${field}"]`);
      if (message) message.textContent = "";
      const input = el.form.elements[field];
      if (input) input.removeAttribute("aria-invalid");
    });
  }

  function setFieldError(field, message) {
    const target = el.form.querySelector(`[data-error-for="${field}"]`);
    if (target) target.textContent = message;
    const input = el.form.elements[field];
    if (input) input.setAttribute("aria-invalid", "true");
  }

  function openForm(mode, record) {
    state.formMode = mode;
    state.lastFocused = document.activeElement;
    clearFormErrors();
    el.form.reset();

    el.formTitle.textContent = mode === "edit" ? "Edit candidate" : "New candidate";
    el.formSubmitBtn.textContent = mode === "edit" ? "Save changes" : "Create candidate";

    if (mode === "edit" && record) {
      FIELDS.forEach((field) => {
        const input = el.form.elements[field];
        if (input) input.value = record[field] === null || record[field] === undefined ? "" : record[field];
      });
    }

    el.formOverlay.classList.remove("hidden");
    el.formModal.classList.remove("hidden");
    el.formModal.classList.add("flex");
    document.body.classList.add("no-scroll");
    el.form.elements.full_name.focus();
  }

  function closeForm() {
    el.formOverlay.classList.add("hidden");
    el.formModal.classList.add("hidden");
    el.formModal.classList.remove("flex");
    if (el.detailPanel.classList.contains("hidden")) document.body.classList.remove("no-scroll");
    if (state.lastFocused && state.lastFocused.focus) state.lastFocused.focus();
  }

  function readForm() {
    const values = {};
    FIELDS.forEach((field) => {
      const input = el.form.elements[field];
      values[field] = input ? input.value.trim() : "";
    });
    return values;
  }

  function validateForm(values) {
    const errors = {};
    if (!values.full_name) errors.full_name = "Full name is required.";
    if (!values.email) errors.email = "Email is required.";
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(values.email)) errors.email = "Enter a valid email address.";
    if (!values.phone) errors.phone = "Phone is required.";
    if (!values.position) errors.position = "Position is required.";
    const years = Number(values.experience_years);
    if (values.experience_years === "") errors.experience_years = "Years of experience is required.";
    else if (!Number.isFinite(years) || years < 0) errors.experience_years = "Enter a number of 0 or more.";
    if (values.linkedin_url && !ui.safeUrl(values.linkedin_url))
      errors.linkedin_url = "Enter a valid http(s) URL.";
    if (values.cv_url && !ui.safeUrl(values.cv_url)) errors.cv_url = "Enter a valid http(s) URL.";
    return errors;
  }

  async function submitForm(event) {
    event.preventDefault();
    clearFormErrors();

    const values = readForm();
    const errors = validateForm(values);
    const invalidFields = Object.keys(errors);
    if (invalidFields.length) {
      invalidFields.forEach((field) => setFieldError(field, errors[field]));
      const first = el.form.elements[invalidFields[0]];
      if (first) first.focus();
      return;
    }

    const payload = {
      full_name: values.full_name,
      email: values.email,
      phone: values.phone,
      position: values.position,
      experience_years: Number(values.experience_years),
      linkedin_url: values.linkedin_url || null,
      cv_url: values.cv_url || null
    };

    el.formSubmitBtn.disabled = true;
    const originalLabel = el.formSubmitBtn.textContent;
    el.formSubmitBtn.textContent = "Saving…";

    try {
      if (state.formMode === "edit" && state.current) {
        await api.replaceRecord(state.current.id, payload);
        ui.toast("Candidate updated.", "success");
        closeForm();
        await loadDetail(state.current.id);
      } else {
        const created = await api.createRecord(payload);
        ui.toast("Candidate registered.", "success");
        closeForm();
        if (created && created.id) openDetail(created.id);
      }
      loadRecords();
      loadSummary();
    } catch (error) {
      const details = error.details || {};
      const detailFields = Object.keys(details);
      if (detailFields.length) {
        detailFields.forEach((field) => setFieldError(field, details[field]));
      }
      el.formError.textContent = error.message;
      el.formError.classList.remove("hidden");
    } finally {
      el.formSubmitBtn.disabled = false;
      el.formSubmitBtn.textContent = originalLabel;
    }
  }

  /* ------------------------------------------------------------- wiring */

  function applyFilterChange() {
    state.page = 1;
    writeFiltersToUrl();
    loadRecords();
  }

  function init() {
    ui.fillSelect(el.statusFilter, api.STATUSES, "All statuses");
    ui.fillSelect(el.stageFilter, api.STAGES, "All stages");

    readFiltersFromUrl();
    el.searchInput.value = state.filters.search;
    el.statusFilter.value = state.filters.status;
    el.stageFilter.value = state.filters.stage;

    el.searchInput.addEventListener(
      "input",
      ui.debounce((event) => {
        state.filters.search = event.target.value.trim();
        applyFilterChange();
      }, 350)
    );

    el.statusFilter.addEventListener("change", (event) => {
      state.filters.status = event.target.value;
      applyFilterChange();
    });

    el.stageFilter.addEventListener("change", (event) => {
      state.filters.stage = event.target.value;
      applyFilterChange();
    });

    el.filtersForm.addEventListener("submit", (event) => event.preventDefault());

    el.clearFiltersBtn.addEventListener("click", () => {
      state.filters = { search: "", status: "", stage: "" };
      el.searchInput.value = "";
      el.statusFilter.value = "";
      el.stageFilter.value = "";
      applyFilterChange();
    });

    el.retryBtn.addEventListener("click", loadRecords);
    el.summaryList.addEventListener("click", (event) => {
      if (event.target.closest("[data-retry-summary]")) loadSummary();
    });

    el.prevPageBtn.addEventListener("click", () => {
      if (state.page <= 1) return;
      state.page -= 1;
      writeFiltersToUrl();
      loadRecords();
      global.scrollTo({ top: 0, behavior: "smooth" });
    });

    el.nextPageBtn.addEventListener("click", () => {
      if (state.page >= totalPages()) return;
      state.page += 1;
      writeFiltersToUrl();
      loadRecords();
      global.scrollTo({ top: 0, behavior: "smooth" });
    });

    el.list.addEventListener("click", (event) => {
      const card = event.target.closest("[data-record-id]");
      if (!card) return;
      openDetail(card.getAttribute("data-record-id"));
    });

    el.newCandidateBtn.addEventListener("click", () => openForm("create"));

    el.detailBody.addEventListener("click", (event) => {
      const noteButton = event.target.closest("[data-delete-note]");
      if (noteButton) {
        deleteNote(noteButton.getAttribute("data-delete-note"), noteButton);
        return;
      }
      const retry = event.target.closest("[data-retry-detail]");
      if (retry) loadDetail(retry.getAttribute("data-retry-detail"));
    });

    document.querySelectorAll("[data-close-detail]").forEach((node) => node.addEventListener("click", closeDetail));
    document.querySelectorAll("[data-close-form]").forEach((node) => node.addEventListener("click", closeForm));
    el.form.addEventListener("submit", submitForm);

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!el.formModal.classList.contains("hidden")) closeForm();
      else if (!el.detailPanel.classList.contains("hidden")) closeDetail();
    });

    loadRecords();
    loadSummary();
  }

  document.addEventListener("DOMContentLoaded", init);
})(window);
