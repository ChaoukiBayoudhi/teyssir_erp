// Thin API client for the Teyssir DRF backend (spec §5). Token auth; same-origin via the
// Vite dev proxy (and Caddy in production), so no CORS handling is needed here.
const BASE = "/api/v1";

export function getToken() {
  return localStorage.getItem("teyssir_token") || "";
}
export function setToken(t) {
  localStorage.setItem("teyssir_token", t);
}
export function clearToken() {
  localStorage.removeItem("teyssir_token");
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && getToken()) headers["Authorization"] = `Token ${getToken()}`;
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    // fetch only throws on a network-level failure (node unreachable) — treat as offline
    const err = new Error("offline");
    err.offline = true;
    throw err;
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.status === 204 ? null : res.json();
}

export async function login(username, password) {
  const data = await request("/auth/token", {
    method: "POST",
    auth: false,
    body: { username, password },
  });
  setToken(data.token);
  return data;
}

export const searchProducts = (q) =>
  request(`/catalog/products/?search=${encodeURIComponent(q)}`);

export const lookupBarcode = (code) =>
  request(`/catalog/products/?barcode=${encodeURIComponent(code)}`);

// Catalogue browser: paginated multi-criteria search + filters + sort.
export const catalogSearch = (params) => {
  const clean = Object.fromEntries(Object.entries(params).filter(([, v]) => v !== "" && v != null));
  return request(`/catalog/search?${new URLSearchParams(clean).toString()}`);
};
export const productDetail = (id) => request(`/catalog/products/${id}/detail`);
export const updateProduct = (id, payload) =>
  request(`/catalog/products/${id}/detail`, { method: "PATCH", body: payload });
export const deleteProduct = (id) =>
  request(`/catalog/products/${id}/detail`, { method: "DELETE" });
export const listCategories = () => request("/catalog/categories");
export const listTaxRates = () => request("/catalog/tax-rates/");
// Register any article (book or supply) from its scanned barcode.
export const barcodeLookup = (code) =>
  request(`/catalog/lookup?barcode=${encodeURIComponent(code)}`);
export const createProduct = (payload) =>
  request("/catalog/register", { method: "POST", body: payload });

// PDF -> Word (.docx). Tiny PDFs return the blob immediately (200); larger jobs return
// 202 {job_id} and must be polled, then downloaded (non-blocking Windows Hub path).
export async function convertPdf(file, { mode = "auto", forceAsync = false } = {}) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("mode", mode);
  if (forceAsync) fd.append("async", "1");
  const headers = {};
  if (getToken()) headers["Authorization"] = `Token ${getToken()}`;
  const res = await fetch(`${BASE}/tools/pdf-to-docx`, { method: "POST", headers, body: fd });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch { /* non-JSON */ }
    throw new Error(detail);
  }
  if (res.status === 202) {
    const meta = await res.json();
    const job = await pollConvertJob(meta.job_id);
    if (job.status === "failed") throw new Error(job.error || "conversion failed");
    return downloadConvertJob(job.job_id);
  }
  return res.blob();
}

export async function pollConvertJob(jobId, { interval = 800, tries = 300 } = {}) {
  for (let i = 0; i < tries; i++) {
    const job = await request(`/tools/pdf-to-docx/${jobId}`);
    if (job.status !== "pending" && job.status !== "running") return job;
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error("conversion timed out");
}

export async function downloadConvertJob(jobId) {
  const headers = {};
  if (getToken()) headers["Authorization"] = `Token ${getToken()}`;
  const res = await fetch(`${BASE}/tools/pdf-to-docx/${jobId}/download`, { headers });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch { /* non-JSON */ }
    throw new Error(detail);
  }
  return res.blob();
}

export const checkout = (payload) =>
  request("/pos/checkout", { method: "POST", body: payload });

export const createQuotation = (payload) =>
  request("/quotations", { method: "POST", body: payload });
export const convertQuotation = (id, payment_method) =>
  request(`/quotations/${id}/convert`, { method: "POST", body: { payment_method } });

export const salesReport = (from, to) =>
  request(`/reports/sales?from=${from}&to=${to}`);
export const trialBalance = () => request("/reports/trial-balance");
export const financials = () => request("/reports/financials");
export const vatDeclaration = (from, to) => request(`/reports/vat?from=${from}&to=${to}`);
export const consolidatedReport = (from, to) =>
  request(`/reports/consolidated?from=${from}&to=${to}`);

export const stocktake = (payload) =>
  request("/inventory/stocktake", { method: "POST", body: payload });

export const cashOpen = (terminal, opening_float) =>
  request("/cash/open", { method: "POST", body: { terminal, opening_float } });
export const cashX = (terminal) => request(`/cash/x?terminal=${terminal}`);
export const cashZ = (terminal, counted_cash) =>
  request("/cash/z", { method: "POST", body: { terminal, counted_cash } });

export const listSuppliers = () => request("/suppliers/");
export const createSupplier = (name) =>
  request("/suppliers/", { method: "POST", body: { name } });
export const receiveGoods = (payload) =>
  request("/purchasing/receive", { method: "POST", body: payload });

export const listPOs = () => request("/purchasing/orders/");
export const createPO = (payload) => request("/purchasing/orders/", { method: "POST", body: payload });
export const receivePO = (id) => request(`/purchasing/orders/${id}/receive/`, { method: "POST", body: {} });
export const createPurchaseInvoice = (payload) =>
  request("/purchasing/invoices", { method: "POST", body: payload });

// Book scan = multipart (images + optional ISBN). Browser sets the multipart boundary.
export async function scanBook(files, isbn) {
  const fd = new FormData();
  files.forEach((f) => fd.append("images", f));
  if (isbn) fd.append("isbn", isbn);
  const headers = {};
  if (getToken()) headers["Authorization"] = `Token ${getToken()}`;
  let res;
  try {
    res = await fetch(`${BASE}/catalog/books/scan`, { method: "POST", headers, body: fd });
  } catch {
    const err = new Error("offline");
    err.offline = true;
    throw err;
  }
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

// Poll a scan job until it leaves the "pending" state (async OCR backend). Returns the final job.
export async function pollScanJob(jobId, { interval = 2000, tries = 120 } = {}) {
  for (let i = 0; i < tries; i++) {
    const job = await request(`/catalog/books/scan/${jobId}`);
    if (job.status === "failed") {
      throw new Error(job.error || "OCR failed");
    }
    if (job.status !== "pending") return job;
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error("scan timed out");
}

export const createBook = (data) => request("/catalog/books", { method: "POST", body: data });

export const fetchMe = () => request("/me");
export const fetchDiagnostics = () => request("/diagnostics");

/** Re-print last sale ticket without creating a new sale (server marks DUPLICATA). */
export async function reprintReceipt(saleId) {
  const headers = {};
  if (getToken()) headers.Authorization = `Token ${getToken()}`;
  const res = await fetch(`${BASE}/pos/sales/${saleId}/receipt?print=1&format=json`, { headers });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

export const listCustomers = () => request("/customers/");
export const createCustomer = (body) =>
  request("/customers/", { method: "POST", body });
export const customerStatement = (id) => request(`/customers/${id}/statement/`);
export const customerPayment = (id, amount) =>
  request(`/customers/${id}/payment/`, { method: "POST", body: { amount } });
