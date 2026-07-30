import { useMemo, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Grid, Paper, TextField, List, ListItemButton,
  ListItemText, IconButton, Stack, Divider, Alert, Snackbar, Chip,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { searchProducts, createQuotation, convertQuotation } from "../api";
import LangToggle from "../LangToggle.jsx";

const r3 = (x) => Math.round(x * 1000) / 1000;
const fmt = (x) => x.toFixed(2);

export default function Quotation({ onBack, onLogout }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [cart, setCart] = useState([]);
  const [quote, setQuote] = useState(null);     // saved quotation {id,total}
  const [done, setDone] = useState(null);        // converted -> invoice
  const [error, setError] = useState("");

  const search = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;
    setError("");
    try { setResults(await searchProducts(query.trim())); }
    catch (err) { setError(String(err.message || err)); }
  };
  const add = (p) => {
    setResults([]); setQuery(""); setQuote(null);
    setCart((c) => {
      const found = c.find((l) => l.product.id === p.id);
      if (found) return c.map((l) => (l.product.id === p.id ? { ...l, qty: l.qty + 1 } : l));
      return [...c, { product: p, qty: 1 }];
    });
  };
  const setQty = (id, qty) => setCart((c) => c.map((l) => (l.product.id === id ? { ...l, qty: Math.max(1, qty) } : l)));
  const remove = (id) => setCart((c) => c.filter((l) => l.product.id !== id));

  const totals = useMemo(() => {
    let sub = 0, tax = 0;
    for (const l of cart) {
      const base = r3(l.qty * Number(l.product.sale_price));
      sub = r3(sub + base);
      tax = r3(tax + r3((base * Number(l.product.tax_rate_percent || 0)) / 100));
    }
    return { sub, tax, total: r3(sub + tax) };
  }, [cart]);

  const saveQuote = async () => {
    setError("");
    try {
      const q = await createQuotation({
        terminal: "C1",
        items: cart.map((l) => ({
          product: l.product.id, qty: String(l.qty),
          unit_price: String(l.product.sale_price), tax_rate: String(l.product.tax_rate_percent || 0),
        })),
      });
      setQuote(q);
    } catch (err) { setError(String(err.message || err)); }
  };

  const convert = async () => {
    setError("");
    try {
      const res = await convertQuotation(quote.id, "CASH");
      setDone(res); setQuote(null); setCart([]);
    } catch (err) { setError(String(err.message || err)); }
  };

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("quotation")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2 }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}
        <Grid container spacing={2}>
          <Grid item xs={12} md={7}>
            <Paper sx={{ p: 2 }}>
              <Box component="form" onSubmit={search}>
                <TextField fullWidth value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("scan")} />
              </Box>
              <List dense>
                {results.map((p) => (
                  <ListItemButton key={p.id} onClick={() => add(p)}>
                    <ListItemText primary={p.name_fr} secondary={`${p.sku} · ${fmt(Number(p.sale_price))} DT`} />
                  </ListItemButton>
                ))}
              </List>
            </Paper>
          </Grid>

          <Grid item xs={12} md={5}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>{t("quotation")}</Typography>
              {cart.length === 0 && <Typography color="text.secondary">{t("empty")}</Typography>}
              {cart.map((l) => (
                <Stack key={l.product.id} direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                  <Box sx={{ flexGrow: 1 }}><Typography>{l.product.name_fr}</Typography></Box>
                  <TextField size="small" type="number" label={t("qty")} value={l.qty}
                             onChange={(e) => setQty(l.product.id, parseInt(e.target.value || "1", 10))}
                             sx={{ width: 80 }} inputProps={{ min: 1, step: 1 }} />
                  <IconButton onClick={() => remove(l.product.id)} aria-label="remove">✕</IconButton>
                </Stack>
              ))}
              <Divider sx={{ my: 2 }} />
              <Stack direction="row" justifyContent="space-between"><span>{t("subtotal")}</span><span>{fmt(totals.sub)}</span></Stack>
              <Stack direction="row" justifyContent="space-between"><span>{t("tva")}</span><span>{fmt(totals.tax)}</span></Stack>
              <Stack direction="row" justifyContent="space-between" sx={{ fontWeight: 700 }}>
                <span>{t("total")}</span><span>{fmt(totals.total)} DT</span>
              </Stack>

              {!quote ? (
                <Button variant="contained" fullWidth size="large" sx={{ mt: 2 }} disabled={!cart.length} onClick={saveQuote}>
                  {t("saveQuote")}
                </Button>
              ) : (
                <Stack spacing={1} sx={{ mt: 2 }}>
                  <Chip color="success" label={`${t("quoteSaved")} · ${quote.total} DT`} />
                  <Button variant="contained" color="secondary" size="large" onClick={convert}>
                    {t("convertToSale")}
                  </Button>
                </Stack>
              )}
            </Paper>
          </Grid>
        </Grid>
      </Box>

      <Snackbar open={Boolean(done)} autoHideDuration={6000} onClose={() => setDone(null)}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setDone(null)}>
          {done && `${t("done")} — ${t("invoice")} ${done.invoice_number}`}
        </Alert>
      </Snackbar>
    </Box>
  );
}
