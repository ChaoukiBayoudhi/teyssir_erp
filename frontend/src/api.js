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
export const listCategories = () => request("/catalog/categories");
export const listTaxRates = () => request("/catalog/tax-rates/");
// Register any article (book or supply) from its scanned barcode.
export const barcodeLookup = (code) =>
  request(`/catalog/lookup?barcode=${encodeURIComponent(code)}`);
export const createProduct = (payload) =>
  request("/catalog/register", { method: "POST", body: payload });

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
    if (job.status !== "pending") return job;
    await new Promise((r) => setTimeout(r, interval));
  }
  throw new Error("scan timed out");
}

export const createBook = (data) => request("/catalog/books", { method: "POST", body: data });

export const listCustomers = () => request("/customers/");
export const createCustomer = (body) =>
  request("/customers/", { method: "POST", body });
export const customerStatement = (id) => request(`/customers/${id}/statement/`);
export const customerPayment = (id, amount) =>
  request(`/customers/${id}/payment/`, { method: "POST", body: { amount } });
