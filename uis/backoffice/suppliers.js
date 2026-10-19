const API_URL = window.SUPPLIER_API_URL || "http://localhost:8000";

const els = {
  rows: document.querySelector("#supplierRows"),
  emptyState: document.querySelector("#emptyState"),
  countryFilter: document.querySelector("#countryFilter"),
  categoryFilter: document.querySelector("#categoryFilter"),
  form: document.querySelector("#supplierForm"),
  formError: document.querySelector("#formError"),
  categoryOptions: document.querySelector("#categoryOptions"),
  apiError: document.querySelector("#apiError"),
  apiErrorText: document.querySelector("#apiErrorText"),
};

const rateFormat = new Intl.NumberFormat("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const dateFormat = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" });

/** Calls the API and returns parsed JSON; throws an Error with a readable message on failure. */
async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
    throw new Error(`Cannot reach the supplier API at ${API_URL}. Is the service running?`);
  }
  if (response.status === 204) return null;
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(describeError(body, response.status));
  return body;
}

/** Turns FastAPI error payloads (string or list of validation errors) into one message. */
function describeError(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((issue) => `${issue.loc.slice(1).join(".") || "request"}: ${issue.msg}`)
      .join("; ");
  }
  return `Request failed (${status})`;
}

function showApiError(message) {
  els.apiErrorText.textContent = message;
  els.apiError.hidden = !message;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function renderRow(supplier) {
  const row = document.createElement("tr");

  const nameCell = document.createElement("td");
  nameCell.append(el("span", "order-id", supplier.name), el("span", "order-brand", `ID ${supplier.id}`));

  const categoriesCell = document.createElement("td");
  categoriesCell.append(...supplier.categories.map((category) => el("span", "tag", category)));

  const rateCell = document.createElement("td");
  const rateForm = el("form", "rate-form");
  const rateInput = el("input");
  rateInput.type = "number";
  rateInput.min = "0";
  rateInput.step = "0.01";
  rateInput.value = supplier.unit_rate_eur;
  rateInput.setAttribute("aria-label", `Rate for ${supplier.name}`);
  const rateButton = el("button", "small-button", "Update");
  rateButton.type = "submit";
  rateForm.append(rateInput, rateButton);
  rateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const rate = Number(rateInput.value);
    if (!(rate > 0)) {
      showApiError(`Rate for ${supplier.name} must be greater than zero.`);
      return;
    }
    await mutate(`/suppliers/${supplier.id}/rate`, { unit_rate_eur: rate });
  });
  rateCell.append(rateForm);

  const isActive = supplier.status === "active";
  const statusCell = document.createElement("td");
  const badge = el("span", `status ${isActive ? "active" : "suspended"}`, isActive ? "Active" : "Suspended");
  const toggle = el("button", "small-button", isActive ? "Suspend" : "Activate");
  toggle.type = "button";
  toggle.addEventListener("click", () =>
    mutate(`/suppliers/${supplier.id}/status`, { status: isActive ? "suspended" : "active" }),
  );
  statusCell.append(badge, toggle);

  row.append(
    nameCell,
    el("td", "destination", supplier.country),
    categoriesCell,
    rateCell,
    statusCell,
    el("td", "time-cell", dateFormat.format(new Date(supplier.updated_at))),
  );
  if (!isActive) row.classList.add("row-suspended");
  return row;
}

/** Sends a PATCH and reloads the list so the table reflects the stored values. */
async function mutate(path, body) {
  try {
    await api(path, { method: "PATCH", body: JSON.stringify(body) });
    showApiError("");
    await loadSuppliers();
  } catch (error) {
    showApiError(error.message);
  }
}

function selectedQuery() {
  const params = new URLSearchParams();
  if (els.countryFilter.value) params.set("country", els.countryFilter.value);
  if (els.categoryFilter.value) params.set("category", els.categoryFilter.value);
  const query = params.toString();
  return query ? `?${query}` : "";
}

let latestLoad = 0;

/** Loads the filtered list; responses that arrive after a newer request started are ignored. */
async function loadSuppliers() {
  const loadId = ++latestLoad;
  try {
    const suppliers = await api(`/suppliers${selectedQuery()}`);
    if (loadId !== latestLoad) return;
    showApiError("");
    els.rows.replaceChildren(...suppliers.map(renderRow));
    els.emptyState.hidden = suppliers.length > 0;
    document.querySelector("#totalCount").textContent = pad(suppliers.length);
    document.querySelector("#activeCount").textContent = pad(suppliers.filter((s) => s.status === "active").length);
    document.querySelector("#suspendedCount").textContent = pad(suppliers.filter((s) => s.status === "suspended").length);
    document.querySelector("#resultSummary").textContent = `Showing ${suppliers.length} supplier${suppliers.length === 1 ? "" : "s"}`;
  } catch (error) {
    if (loadId !== latestLoad) return;
    showApiError(error.message);
    els.rows.replaceChildren();
    document.querySelector("#resultSummary").textContent = "Suppliers could not be loaded";
  }
}

/** Rebuilds the country filter from the full directory, keeping the current selection. */
async function loadCountries() {
  const all = await api("/suppliers");
  const countries = [...new Set(all.map((supplier) => supplier.country))].sort();
  const current = els.countryFilter.value;
  els.countryFilter.replaceChildren(new Option("All countries", ""), ...countries.map((c) => new Option(c, c)));
  els.countryFilter.value = countries.includes(current) ? current : "";
  document.querySelector("#countryCount").textContent = pad(countries.length);
}

async function loadCategories() {
  const categories = await api("/categories");
  els.categoryFilter.replaceChildren(new Option("All categories", ""), ...categories.map((c) => new Option(c, c)));
  els.categoryOptions.replaceChildren(
    ...categories.map((category) => {
      const label = el("label", "check");
      const input = el("input");
      input.type = "checkbox";
      input.name = "categories";
      input.value = category;
      label.append(input, document.createTextNode(` ${category}`));
      return label;
    }),
  );
}

function setFormOpen(open) {
  els.form.hidden = !open;
  document.querySelector("#toggleFormButton").setAttribute("aria-expanded", String(open));
  if (open) document.querySelector("#nameInput").focus();
}

function showFormError(message) {
  els.formError.textContent = message;
  els.formError.hidden = !message;
}

/** Client-side checks that mirror the API rules; returns an error message or null. */
function validateForm(data) {
  if (!data.name) return "Name is required.";
  if (!data.country) return "Country is required.";
  if (data.categories.length === 0) return "Select at least one product category.";
  if (!(data.unit_rate_eur > 0)) return "Rate must be a number greater than zero.";
  return null;
}

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = {
    name: document.querySelector("#nameInput").value.trim(),
    country: document.querySelector("#countryInput").value.trim(),
    categories: [...els.form.querySelectorAll("input[name=categories]:checked")].map((box) => box.value),
    unit_rate_eur: Number(document.querySelector("#rateInput").value),
    status: document.querySelector("#statusInput").value,
  };
  const problem = validateForm(data);
  if (problem) {
    showFormError(problem);
    return;
  }
  try {
    await api("/suppliers", { method: "POST", body: JSON.stringify(data) });
    showFormError("");
    els.form.reset();
    setFormOpen(false);
    await loadCountries();
    await loadSuppliers();
  } catch (error) {
    showFormError(error.message);
  }
});

document.querySelector("#toggleFormButton").addEventListener("click", () => setFormOpen(els.form.hidden));
document.querySelector("#cancelFormButton").addEventListener("click", () => {
  els.form.reset();
  showFormError("");
  setFormOpen(false);
});
els.countryFilter.addEventListener("change", loadSuppliers);
els.categoryFilter.addEventListener("change", loadSuppliers);
document.querySelector("#refreshButton").addEventListener("click", async () => {
  await loadCountries().catch(() => {});
  await loadSuppliers();
});

(async function init() {
  try {
    await Promise.all([loadCategories(), loadCountries()]);
  } catch (error) {
    showApiError(error.message);
  }
  await loadSuppliers();
})();
