import { useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, TextField, List, ListItemButton,
  ListItemText, Table, TableHead, TableRow, TableCell, TableBody, Alert, Snackbar,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { searchProducts, stocktake } from "../api";
import LangToggle from "../LangToggle.jsx";
import { fmtQty } from "../format.js";

export default function StockTake({ onBack, onLogout }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [rows, setRows] = useState([]); // { product, system, counted }
  const [done, setDone] = useState(null);
  const [error, setError] = useState("");

  const search = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;
    setError("");
    try {
      setResults(await searchProducts(query.trim()));
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  const add = (p) => {
    setResults([]);
    setQuery("");
    setRows((r) =>
      r.find((x) => x.product.id === p.id)
        ? r
        : [...r, { product: p, system: Number(p.qty_on_hand), counted: fmtQty(p.qty_on_hand) }]
    );
  };

  const setCounted = (id, v) =>
    setRows((r) => r.map((x) => (x.product.id === id ? { ...x, counted: v } : x)));

  const submit = async () => {
    setError("");
    try {
      const res = await stocktake({
        terminal: "C1",
        items: rows.map((x) => ({ product: x.product.id, counted_qty: x.counted || "0" })),
      });
      setDone(res);
      setRows([]);
    } catch (err) {
      setError(String(err.message || err).startsWith("403") ? t("noAccess") : String(err.message || err));
    }
  };

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("inventory")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2 }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

        <Paper sx={{ p: 2, mb: 2 }}>
          <Box component="form" onSubmit={search}>
            <TextField fullWidth value={query} onChange={(e) => setQuery(e.target.value)}
                       placeholder={t("scan")} />
          </Box>
          <List dense>
            {results.map((p) => (
              <ListItemButton key={p.id} onClick={() => add(p)}>
                <ListItemText primary={p.name_fr}
                              secondary={`${p.sku} · ${t("system")}: ${fmtQty(p.qty_on_hand)}`} />
              </ListItemButton>
            ))}
          </List>
        </Paper>

        <Paper sx={{ p: 2 }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("cart")}</TableCell>
                <TableCell align="right">{t("system")}</TableCell>
                <TableCell align="right">{t("counted")}</TableCell>
                <TableCell align="right">{t("variance")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((x) => {
                const variance = (Number(x.counted) || 0) - x.system;
                return (
                  <TableRow key={x.product.id}>
                    <TableCell>{x.product.name_fr}</TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{x.system}</TableCell>
                    <TableCell align="right">
                      <TextField size="small" type="number" value={x.counted}
                                 onChange={(e) => setCounted(x.product.id, e.target.value)}
                                 sx={{ width: 90 }} inputProps={{ min: 0, step: 1, style: { textAlign: "right" } }} />
                    </TableCell>
                    <TableCell align="right" sx={{
                      fontVariantNumeric: "tabular-nums", fontWeight: 700,
                      color: variance === 0 ? "text.secondary" : variance > 0 ? "success.main" : "error.main",
                    }}>
                      {variance > 0 ? `+${variance}` : variance}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
          <Button variant="contained" size="large" sx={{ mt: 2 }} disabled={!rows.length} onClick={submit}>
            {t("submitCount")}
          </Button>
        </Paper>
      </Box>

      <Snackbar open={Boolean(done)} autoHideDuration={5000} onClose={() => setDone(null)}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setDone(null)}>
          {done && `${done.adjusted} ${t("adjusted")}`}
        </Alert>
      </Snackbar>
    </Box>
  );
}
