import { useEffect, useMemo, useState } from "react";
import {
  AppBar, Toolbar, Typography, Box, Grid, Paper, TextField, List, ListItemButton,
  ListItemText, IconButton, Stack, Button, Divider, Alert, Select, MenuItem, Snackbar, Chip,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { searchProducts, lookupBarcode, checkout } from "../api";
import { enqueue, flush, pending } from "../offlineQueue";
import LangToggle from "../LangToggle.jsx";

const TIMBRE = 1.0; // facture stamp (server snapshots the authoritative value)
const r3 = (x) => Math.round(x * 1000) / 1000;
const fmt = (x) => x.toFixed(2); // 2-dp display (server stores 3-dp)

export default function Pos({ onLogout, onDashboard, onStockTake, onCash, onReceiving, onCustomers, onNewBook }) {
  const { t } = useTranslation();
  const [terminal, setTerminal] = useState("C1");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [cart, setCart] = useState([]);
  const [method, setMethod] = useState("CASH");
  const [done, setDone] = useState(null);
  const [error, setError] = useState("");
  const [queued, setQueued] = useState(false);
  const [pendingCount, setPendingCount] = useState(pending().length);

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

  const addToCart = (product) => {
    setResults([]);
    setQuery("");
    setCart((c) => {
      const found = c.find((l) => l.product.id === product.id);
      if (found) return c.map((l) => (l.product.id === product.id ? { ...l, qty: l.qty + 1 } : l));
      return [...c, { product, qty: 1 }];
    });
  };

  const setQty = (id, qty) =>
    setCart((c) => c.map((l) => (l.product.id === id ? { ...l, qty: Math.max(1, qty) } : l)));
  const removeLine = (id) => setCart((c) => c.filter((l) => l.product.id !== id));

  const onSearch = async (e) => {
    e.preventDefault();
    setError("");
    const q = query.trim();
    if (!q) return;
    try {
      if (/^\d{6,}$/.test(q)) {
        const hits = await lookupBarcode(q);
        if (hits.length === 1) return addToCart(hits[0]);
        if (hits.length === 0) return setResults(await searchProducts(q));
        return setResults(hits);
      }
      setResults(await searchProducts(q));
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  const totals = useMemo(() => {
    let subtotal = 0;
    let tax = 0;
    for (const l of cart) {
      const price = Number(l.product.sale_price);
      const rate = Number(l.product.tax_rate_percent || 0);
      const base = r3(l.qty * price);
      subtotal = r3(subtotal + base);
      tax = r3(tax + r3((base * rate) / 100));
    }
    const timbre = cart.length ? TIMBRE : 0;
    return { subtotal, tax, timbre, total: r3(subtotal + tax + timbre) };
  }, [cart]);

  const pay = async () => {
    setError("");
    const payload = {
      terminal,
      payment_method: method,
      lines: cart.map((l) => ({ product: l.product.id, qty: String(l.qty) })),
    };
    try {
      const res = await checkout(payload);
      setDone(res);
      setCart([]);
    } catch (err) {
      if (err.offline) {
        setPendingCount(enqueue(payload)); // node unreachable — hold and replay later
        setCart([]);
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
          <Button color="inherit" onClick={onDashboard}>{t("dashboard")}</Button>
          <Button color="inherit" onClick={onStockTake}>{t("inventory")}</Button>
          <Button color="inherit" onClick={onReceiving}>{t("receiving")}</Button>
          <Button color="inherit" onClick={onNewBook}>{t("newBook")}</Button>
          <Button color="inherit" onClick={onCustomers}>{t("customers")}</Button>
          <Button color="inherit" onClick={onCash}>{t("session")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2 }}>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Grid container spacing={2}>
          <Grid item xs={12} md={7}>
            <Paper sx={{ p: 2 }}>
              <Box component="form" onSubmit={onSearch}>
                <TextField
                  fullWidth autoFocus value={query} onChange={(e) => setQuery(e.target.value)}
                  placeholder={t("scan")}
                />
              </Box>
              <List dense>
                {results.length === 0 && (
                  <Typography color="text.secondary" sx={{ p: 1 }}>{t("noResults")}</Typography>
                )}
                {results.map((p) => (
                  <ListItemButton key={p.id} onClick={() => addToCart(p)}>
                    <ListItemText
                      primary={p.name_fr}
                      secondary={`${p.sku} · ${fmt(Number(p.sale_price))} DT · TVA ${p.tax_rate_percent}%`}
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
                    sx={{ width: 84 }} inputProps={{ min: 1 }}
                  />
                  <IconButton onClick={() => removeLine(l.product.id)} aria-label="remove">✕</IconButton>
                </Stack>
              ))}

              <Divider sx={{ my: 2 }} />
              <Row label={t("subtotal")} value={fmt(totals.subtotal)} />
              <Row label={t("tva")} value={fmt(totals.tax)} />
              <Row label={t("timbre")} value={fmt(totals.timbre)} />
              <Row label={t("total")} value={`${fmt(totals.total)} DT`} bold />

              <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
                <Select size="small" value={method} onChange={(e) => setMethod(e.target.value)} sx={{ minWidth: 120 }}>
                  <MenuItem value="CASH">{t("cash")}</MenuItem>
                  <MenuItem value="CARD">{t("card")}</MenuItem>
                  <MenuItem value="ACCOUNT">{t("account")}</MenuItem>
                </Select>
                <Button
                  variant="contained" size="large" fullWidth disabled={!cart.length} onClick={pay}
                >
                  {t("pay")}
                </Button>
              </Stack>
            </Paper>
          </Grid>
        </Grid>
      </Box>

      <Snackbar
        open={Boolean(done)} autoHideDuration={6000} onClose={() => setDone(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      >
        <Alert severity="success" variant="filled" onClose={() => setDone(null)}>
          {done && `${t("done")} — ${t("invoice")} ${done.invoice_number} · ${done.total_display} DT`}
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
