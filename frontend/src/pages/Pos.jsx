import { useEffect, useMemo, useRef, useState } from "react";
import {
  AppBar, Toolbar, Typography, Box, Grid, Paper, TextField, List, ListItemButton,
  ListItemText, IconButton, Stack, Button, Divider, Alert, Select, MenuItem, Menu, Snackbar, Chip,
  CircularProgress,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { searchProducts, lookupBarcode, checkout, listCustomers, reprintReceipt } from "../api";
import { enqueue, flush, pending } from "../offlineQueue";
import CameraScanner from "../components/CameraScanner.jsx";
import LangToggle from "../LangToggle.jsx";

// Millime-exact helpers mirroring backend money.py (1 DT = 1000 millimes).
const toMillimes = (x) => Math.round(Number(x) * 1000);
const fromMillimes = (m) => m / 1000;
const r3 = (x) => fromMillimes(toMillimes(x));
const fmt = (x) => Number(x).toFixed(2); // 2-dp display (server stores 3-dp)
/** Fallback until /health/ loads; backend APPLY_VAT_AND_TIMBRE is source of truth. */
const TIMBRE = 1; // only used if applyVat is true

/** Digits-heavy or alphanumeric refs (PEN-001, 1001, EAN) — not free-text names. */
const looksLikeCode = (q) => {
  const s = String(q || "").trim();
  if (!s || /\s/.test(s)) return false;
  if (/^\d{4,}$/.test(s)) return true;
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$/.test(s) && /\d/.test(s);
};

export default function Pos({ onLogout, onDashboard, onStockTake, onCash, onReceiving,
                              onCustomers, onNewBook, onQuotation, onPurchaseOrders, onCatalog,
                              onNewProduct, onPdfConvert, onDiagnostics }) {
  const { t } = useTranslation();
  const [terminal, setTerminal] = useState("C1");
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const searchSeq = useRef(0);
  const [cart, setCart] = useState([]); // {product, qty, discountPct}
  const [globalDiscountPct, setGlobalDiscountPct] = useState(0);
  const [method, setMethod] = useState("CASH");
  const [customers, setCustomers] = useState([]);
  const [customerId, setCustomerId] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(null);
  const [lastSale, setLastSale] = useState(null); // keep for reprint without new sale
  const [reprintMsg, setReprintMsg] = useState("");
  const [queued, setQueued] = useState(false);
  const [pendingCount, setPendingCount] = useState(pending().length);
  const [camera, setCamera] = useState(false);
  // Default off so a stale compile-time true cannot flash TVA; /health/ may turn it on.
  const [applyVat, setApplyVat] = useState(false);

  useEffect(() => {
    fetch("/health/")
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (body && typeof body.apply_vat_and_timbre === "boolean") {
          setApplyVat(body.apply_vat_and_timbre);
        }
      })
      .catch(() => {});
  }, []);

  // Replay any queued sales on mount and whenever connectivity returns (spec §4.3).
  useEffect(() => {
    const replay = async () => {
      const { succeeded } = await flush(checkout);
      setPendingCount(pending().length);
      if (succeeded.length) setDone(succeeded[succeeded.length - 1]);
    };
    replay();
    window.addEventListener("online", replay);
    return () => window.removeEventListener("online", replay);
  }, []);

  useEffect(() => {
    listCustomers().then(setCustomers).catch(() => {});
  }, []);

  const addToCart = (product) => {
    setResults([]);
    setQuery("");
    setHasSearched(false);
    setCart((c) => {
      const found = c.find((l) => l.product.id === product.id);
      if (found) {
        return c.map((l) => (l.product.id === product.id ? { ...l, qty: l.qty + 1 } : l));
      }
      return [...c, { product, qty: 1, discountPct: 0 }];
    });
  };

  const setQty = (id, qty) =>
    setCart((c) => c.map((l) => (l.product.id === id
      ? { ...l, qty: Math.max(1, Math.trunc(Number(qty) || 1)) } : l)));
  const setLineDiscount = (id, pct) =>
    setCart((c) => c.map((l) => (
      l.product.id === id ? { ...l, discountPct: Math.min(100, Math.max(0, Number(pct) || 0)) } : l
    )));
  const removeLine = (id) => setCart((c) => c.filter((l) => l.product.id !== id));

  // Live debounced search while typing (Catalog-style). Ignore stale responses.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      setHasSearched(false);
      setSearching(false);
      return undefined;
    }
    const seq = ++searchSeq.current;
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      setSearching(true);
      setError("");
      try {
        let hits = [];
        if (looksLikeCode(q)) {
          hits = await lookupBarcode(q, { signal: ctrl.signal });
        }
        if (!hits.length) {
          hits = await searchProducts(q, { signal: ctrl.signal });
        }
        if (seq !== searchSeq.current) return;
        setResults(hits);
        setHasSearched(true);
      } catch (err) {
        if (err?.name === "AbortError") return;
        if (seq !== searchSeq.current) return;
        setError(String(err.message || err));
        setResults([]);
        setHasSearched(true);
      } finally {
        if (seq === searchSeq.current) setSearching(false);
      }
    }, 250);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [query]);

  // Enter / scanner submit: exact code → single hit goes straight into the cart.
  const onSearch = async (e) => {
    e.preventDefault();
    setError("");
    const q = query.trim();
    if (!q) return;
    const seq = ++searchSeq.current;
    setSearching(true);
    try {
      if (looksLikeCode(q)) {
        const hits = await lookupBarcode(q);
        if (seq !== searchSeq.current) return;
        if (hits.length === 1) return addToCart(hits[0]);
        if (hits.length > 1) {
          setResults(hits);
          setHasSearched(true);
          return;
        }
      }
      const hits = await searchProducts(q);
      if (seq !== searchSeq.current) return;
      setResults(hits);
      setHasSearched(true);
    } catch (err) {
      if (seq !== searchSeq.current) return;
      setError(String(err.message || err));
    } finally {
      if (seq === searchSeq.current) setSearching(false);
    }
  };

  // Camera scan → barcode lookup (ISBN / EAN / CNP 619 / Code128). No book OCR on Caisse.
  const onCameraCode = async (code) => {
    setError("");
    const raw = String(code || "").replace(/[-\s]/g, "").trim();
    if (!raw) return;
    try {
      const hits = await lookupBarcode(raw);
      if (hits.length === 1) {
        addToCart(hits[0]);
        setCamera(false); // stop camera after successful add-to-cart
        return;
      }
      if (hits.length > 1) {
        setResults(hits);
        setHasSearched(true);
        return;
      }
      setError(t("unknownProductBarcode", { code: raw }));
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  // Preview mirrors backend: line discount → header discount; no TVA/timbre when shop flag is off.
  const totals = useMemo(() => {
    let grossM = 0;
    const lineBases = []; // millimes after line discount
    for (const l of cart) {
      const price = Number(l.product.sale_price);
      const rate = Number(l.product.tax_rate_percent || 0);
      const gross = toMillimes(l.qty * price);
      const lineDisc = Math.round(gross * (Number(l.discountPct) || 0) / 100);
      const base = gross - lineDisc;
      lineBases.push({ base, rate, lineDisc });
      grossM += base;
    }
    const headerDiscM = Math.round(grossM * (Number(globalDiscountPct) || 0) / 100);
    let allocated = 0;
    let subtotalM = 0;
    let taxM = 0;
    lineBases.forEach((lb, i) => {
      let share;
      if (!headerDiscM || !grossM) share = 0;
      else if (i === lineBases.length - 1) share = headerDiscM - allocated;
      else {
        share = Math.round(headerDiscM * lb.base / grossM);
        allocated += share;
      }
      const adj = lb.base - share;
      subtotalM += adj;
      if (applyVat) taxM += Math.round(adj * lb.rate / 100);
    });
    const timbre = applyVat && cart.length ? TIMBRE : 0;
    const subtotal = fromMillimes(subtotalM);
    const tax = fromMillimes(taxM);
    const discount = fromMillimes(headerDiscM);
    return {
      subtotal: r3(subtotal),
      discount: r3(discount),
      tax: r3(tax),
      timbre,
      showTax: applyVat,
      total: r3(subtotal + tax + timbre),
      lineDiscounts: lineBases.map((lb) => fromMillimes(lb.lineDisc)),
    };
  }, [cart, globalDiscountPct, applyVat]);

  const pay = async () => {
    setError("");
    if (totals.total < 0) {
      setError(t("totalCannotBeNegative"));
      return;
    }
    if (method === "ACCOUNT" && !customerId) {
      setError(t("customerRequired"));
      return;
    }
    const payload = {
      terminal,
      payment_method: method,
      discount: String(totals.discount),
      lines: cart.map((l, i) => ({
        product: l.product.id,
        qty: String(l.qty),
        discount: String(r3(totals.lineDiscounts[i] || 0)),
      })),
    };
    if (customerId) payload.customer = customerId;
    try {
      const res = await checkout(payload);
      setDone(res);
      setLastSale(res);
      setCart([]);
      setGlobalDiscountPct(0);
      setCustomerId("");
      // Open text receipt preview in a new tab (thermal already printed server-side).
      if (res.receipt_url && res.sale_id) {
        try {
          const headers = {};
          const tok = localStorage.getItem("teyssir_token");
          if (tok) headers.Authorization = `Token ${tok}`;
          const r = await fetch(res.receipt_url, { headers });
          if (r.ok) {
            const text = await r.text();
            const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
            window.open(URL.createObjectURL(blob), "_blank", "noopener");
          }
        } catch { /* preview is best-effort */ }
      }
    } catch (err) {
      if (err.offline) {
        setPendingCount(enqueue(payload));
        setCart([]);
        setGlobalDiscountPct(0);
        setQueued(true);
      } else {
        setError(String(err.message || err));
      }
    }
  };

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("appName")}</Typography>
          {pendingCount > 0 && (
            <Chip color="warning" size="small" label={`${pendingCount} ${t("pending")}`} />
          )}
          <Select
            size="small" value={terminal} onChange={(e) => setTerminal(e.target.value)}
            sx={{ bgcolor: "white", minWidth: 80 }}
          >
            {["C1", "C2", "C3"].map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </Select>
          <Button color="inherit" onClick={(e) => setMenuAnchor(e.currentTarget)}>{t("menu")} ▾</Button>
          <Menu anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={() => setMenuAnchor(null)}>
            {[
              ["dashboard", onDashboard],
              ["inventory", onStockTake],
              ["catalog", onCatalog],
              ["newArticle", onNewProduct],
              ["pdfToWord", onPdfConvert],
              ["receiving", onReceiving],
              ["purchaseOrders", onPurchaseOrders],
              ["newBook", onNewBook],
              ["quotation", onQuotation],
              ["customers", onCustomers],
              ["session", onCash],
              ...(onDiagnostics ? [["diagnostics", onDiagnostics]] : []),
            ].map(([key, fn]) => (
              <MenuItem key={key} onClick={() => { setMenuAnchor(null); fn && fn(); }}>{t(key)}</MenuItem>
            ))}
          </Menu>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2 }}>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Grid container spacing={2}>
          <Grid item xs={12} md={7}>
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" spacing={1} alignItems="flex-start">
                <Box component="form" onSubmit={onSearch} sx={{ flexGrow: 1 }}>
                  <TextField
                    fullWidth autoFocus value={query} onChange={(e) => setQuery(e.target.value)}
                    placeholder={t("scan")}
                  />
                </Box>
                <Button variant={camera ? "contained" : "outlined"} sx={{ minWidth: 52, height: 56 }}
                        onClick={() => setCamera((c) => !c)} aria-label={t("scanWithCamera")}>📷</Button>
              </Stack>
              {camera && (
                <CameraScanner
                  mode="pos"
                  stopOnDetect
                  onDetect={onCameraCode}
                  onClose={() => setCamera(false)}
                />
              )}
              <List dense>
                {searching && (
                  <Box sx={{ display: "flex", justifyContent: "center", p: 1 }}>
                    <CircularProgress size={22} />
                  </Box>
                )}
                {!searching && hasSearched && results.length === 0 && (
                  <Typography color="text.secondary" sx={{ p: 1 }}>{t("noResults")}</Typography>
                )}
                {results.map((p) => (
                  <ListItemButton key={p.id} onClick={() => addToCart(p)}>
                    <ListItemText
                      primary={p.name_fr}
                      secondary={`${p.reference || p.sku} · ${fmt(Number(p.sale_price))} DT`}
                    />
                  </ListItemButton>
                ))}
              </List>
            </Paper>
          </Grid>

          <Grid item xs={12} md={5}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>{t("cart")}</Typography>
              {cart.length === 0 && <Typography color="text.secondary">{t("empty")}</Typography>}
              {cart.map((l) => (
                <Stack key={l.product.id} direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                  <Box sx={{ flexGrow: 1 }}>
                    <Typography>{l.product.name_fr}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {fmt(Number(l.product.sale_price))} DT
                    </Typography>
                  </Box>
                  <TextField
                    size="small" type="number" label={t("qty")} value={l.qty}
                    onChange={(e) => setQty(l.product.id, parseInt(e.target.value || "1", 10))}
                    sx={{ width: 72 }} inputProps={{ min: 1, step: 1 }}
                  />
                  <TextField
                    size="small" type="number" label={t("discPct")} value={l.discountPct}
                    onChange={(e) => setLineDiscount(l.product.id, e.target.value)}
                    sx={{ width: 72 }} inputProps={{ min: 0, max: 100 }}
                  />
                  <IconButton onClick={() => removeLine(l.product.id)} aria-label="remove">✕</IconButton>
                </Stack>
              ))}

              <Divider sx={{ my: 2 }} />
              <TextField
                size="small" type="number" label={t("globalDiscPct")}
                value={globalDiscountPct}
                onChange={(e) => setGlobalDiscountPct(Math.min(100, Math.max(0, Number(e.target.value) || 0)))}
                sx={{ mb: 1, width: "100%" }} inputProps={{ min: 0, max: 100 }}
              />
              <Row label={t("subtotal")} value={fmt(totals.subtotal)} />
              {totals.showTax && (
                <>
                  <Row label={t("tva")} value={fmt(totals.tax)} />
                  <Row label={t("timbre")} value={fmt(totals.timbre)} />
                </>
              )}
              <Row label={t("total")} value={`${fmt(totals.total)} DT`} bold />

              <Stack direction="row" spacing={1} sx={{ mt: 2 }} flexWrap="wrap" useFlexGap>
                <Select size="small" value={method} onChange={(e) => setMethod(e.target.value)} sx={{ minWidth: 120 }}>
                  <MenuItem value="CASH">{t("cash")}</MenuItem>
                  <MenuItem value="CARD">{t("card")}</MenuItem>
                  <MenuItem value="ACCOUNT">{t("account")}</MenuItem>
                </Select>
                {method === "ACCOUNT" && (
                  <Select
                    size="small" value={customerId} displayEmpty
                    onChange={(e) => setCustomerId(e.target.value)}
                    sx={{ minWidth: 160 }}
                  >
                    <MenuItem value="">{t("selectCustomer")}</MenuItem>
                    {customers.map((c) => (
                      <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>
                    ))}
                  </Select>
                )}
                <Button
                  variant="contained" size="large" fullWidth
                  disabled={!cart.length || totals.total < 0} onClick={pay}
                >
                  {t("pay")}
                </Button>
                {lastSale?.sale_id && (
                  <Button
                    variant="outlined"
                    fullWidth
                    onClick={async () => {
                      try {
                        const r = await reprintReceipt(lastSale.sale_id);
                        setReprintMsg(r.printed ? t("reprintOk") : t("reprintFailed"));
                        setLastSale({ ...lastSale, printed: Boolean(r.printed) });
                      } catch (err) {
                        setReprintMsg(t("reprintFailed"));
                        setError(String(err.message || err));
                      }
                    }}
                  >
                    {t("reprintTicket")}
                  </Button>
                )}
              </Stack>
            </Paper>
          </Grid>
        </Grid>
      </Box>

      <Snackbar
        open={Boolean(done)} autoHideDuration={8000} onClose={() => setDone(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert
          severity={done && done.printed === false ? "warning" : "success"}
          variant="filled"
          onClose={() => setDone(null)}
          action={lastSale?.sale_id ? (
            <Button color="inherit" size="small" onClick={async () => {
              try {
                const r = await reprintReceipt(lastSale.sale_id);
                setReprintMsg(r.printed ? t("reprintOk") : t("reprintFailed"));
              } catch {
                setReprintMsg(t("reprintFailed"));
              }
            }}>
              {t("reprintTicket")}
            </Button>
          ) : null}
        >
          {done && `${t("done")} — ${t("invoice")} ${done.invoice_number} · ${done.total_display} DT${done.printed ? " · ✓" : ` · ${t("printFailedHint")}`}`}
        </Alert>
      </Snackbar>

      <Snackbar
        open={Boolean(reprintMsg)} autoHideDuration={4000} onClose={() => setReprintMsg("")}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity="info" variant="filled" onClose={() => setReprintMsg("")}>
          {reprintMsg}
        </Alert>
      </Snackbar>

      <Snackbar
        open={queued} autoHideDuration={5000} onClose={() => setQueued(false)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity="warning" variant="filled" onClose={() => setQueued(false)}>
          {t("queued")}
        </Alert>
      </Snackbar>
    </Box>
  );
}

function Row({ label, value, bold }) {
  return (
    <Stack direction="row" justifyContent="space-between" sx={{ py: 0.25 }}>
      <Typography fontWeight={bold ? 700 : 400}>{label}</Typography>
      <Typography fontWeight={bold ? 700 : 400} sx={{ fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
    </Stack>
  );
}
