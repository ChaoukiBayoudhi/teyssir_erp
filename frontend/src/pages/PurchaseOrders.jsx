import { useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Grid, Paper, TextField, List, ListItemButton,
  ListItemText, Table, TableHead, TableRow, TableCell, TableBody, Stack, Alert, Snackbar,
  Select, MenuItem, Chip, Divider,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import {
  searchProducts, listSuppliers, createSupplier, listPOs, createPO, receivePO, createPurchaseInvoice,
} from "../api";
import LangToggle from "../LangToggle.jsx";

export default function PurchaseOrders({ onBack, onLogout }) {
  const { t } = useTranslation();
  const [suppliers, setSuppliers] = useState([]);
  const [supplier, setSupplier] = useState("");
  const [newName, setNewName] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [lines, setLines] = useState([]);          // {product, qty, cost}
  const [orders, setOrders] = useState([]);
  const [inv, setInv] = useState({});               // po_id -> {number, ht, tva}
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");

  const refresh = async () => {
    try {
      const [s, o] = await Promise.all([listSuppliers(), listPOs()]);
      setSuppliers(s);
      if (!supplier && s.length) setSupplier(s[0].id);
      setOrders(o);
    } catch (e) { setError(String(e.message || e)); }
  };
  useEffect(() => { refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const addSupplier = async () => {
    if (!newName.trim()) return;
    try { const s = await createSupplier(newName.trim()); setNewName(""); await refresh(); setSupplier(s.id); }
    catch (e) { setError(String(e.message || e)); }
  };
  const search = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;
    try { setResults(await searchProducts(query.trim())); } catch (err) { setError(String(err.message || err)); }
  };
  const add = (p) => {
    setResults([]); setQuery("");
    setLines((l) => l.find((x) => x.product.id === p.id) ? l
      : [...l, { product: p, qty: "1", cost: String(p.sale_price || "0") }]);
  };
  const setField = (id, k, v) => setLines((l) => l.map((x) => (x.product.id === id ? { ...x, [k]: v } : x)));

  const create = async () => {
    setError("");
    try {
      await createPO({
        supplier,
        items: lines.map((x) => ({ product: x.product.id, qty: x.qty || "0", unit_cost: x.cost || "0" })),
      });
      setLines([]); setToast(t("poCreated")); await refresh();
    } catch (e) { setError(String(e.message || e)); }
  };
  const receive = async (id) => {
    setError("");
    try { await receivePO(id); await refresh(); } catch (e) { setError(String(e.message || e)); }
  };
  const saveInvoice = async (po) => {
    const f = inv[po] || {};
    setError("");
    try {
      await createPurchaseInvoice({ supplier, po, supplier_number: f.number || "", subtotal: f.ht || "0", tva_total: f.tva || "0" });
      setInv((s) => ({ ...s, [po]: {} })); setToast(t("poInvoiceSaved")); await refresh();
    } catch (e) { setError(String(e.message || e)); }
  };
  const setInvField = (po, k, v) => setInv((s) => ({ ...s, [po]: { ...(s[po] || {}), [k]: v } }));

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("purchaseOrders")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2, maxWidth: 980, mx: "auto" }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

        <Paper sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 1 }}>
            <Typography sx={{ minWidth: 90 }}>{t("supplier")}</Typography>
            <Select size="small" value={supplier} onChange={(e) => setSupplier(e.target.value)} sx={{ minWidth: 220 }} displayEmpty>
              {suppliers.length === 0 && <MenuItem value="">—</MenuItem>}
              {suppliers.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </Select>
            <TextField size="small" placeholder={t("newSupplier")} value={newName} onChange={(e) => setNewName(e.target.value)} />
            <Button variant="outlined" onClick={addSupplier}>{t("add")}</Button>
          </Stack>
          <Box component="form" onSubmit={search}><TextField fullWidth size="small" value={query}
            onChange={(e) => setQuery(e.target.value)} placeholder={t("scan")} /></Box>
          <List dense>
            {results.map((p) => (
              <ListItemButton key={p.id} onClick={() => add(p)}>
                <ListItemText primary={p.name_fr} secondary={p.sku} />
              </ListItemButton>
            ))}
          </List>
          {lines.length > 0 && (
            <>
              <Table size="small">
                <TableHead><TableRow><TableCell>{t("cart")}</TableCell><TableCell align="right">{t("qty")}</TableCell><TableCell align="right">{t("unitCost")}</TableCell></TableRow></TableHead>
                <TableBody>
                  {lines.map((x) => (
                    <TableRow key={x.product.id}>
                      <TableCell>{x.product.name_fr}</TableCell>
                      <TableCell align="right"><TextField size="small" type="number" value={x.qty}
                        onChange={(e) => setField(x.product.id, "qty", e.target.value)} sx={{ width: 80 }} inputProps={{ min: 1, step: 1 }} /></TableCell>
                      <TableCell align="right"><TextField size="small" type="number" value={x.cost}
                        onChange={(e) => setField(x.product.id, "cost", e.target.value)} sx={{ width: 100 }} inputProps={{ step: "0.001" }} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Button variant="contained" sx={{ mt: 1 }} disabled={!supplier} onClick={create}>{t("createPO")}</Button>
            </>
          )}
        </Paper>

        <Paper sx={{ p: 2 }}>
          <Typography variant="h6" gutterBottom>{t("purchaseOrders")}</Typography>
          {orders.map((po) => (
            <Box key={po.id} sx={{ mb: 1.5 }}>
              <Stack direction="row" spacing={2} alignItems="center">
                <Box sx={{ flexGrow: 1 }}>
                  <Typography>{po.supplier_name} · {po.lines.length} {t("qty")}</Typography>
                </Box>
                <Chip size="small" color={po.status === "RECEIVED" ? "success" : "warning"} label={po.status} />
                {po.status !== "RECEIVED" && (
                  <Button size="small" variant="contained" onClick={() => receive(po.id)}>{t("receivePO")}</Button>
                )}
              </Stack>
              <Stack direction="row" spacing={1} sx={{ mt: 0.5 }} alignItems="center">
                <Typography variant="caption" color="text.secondary" sx={{ minWidth: 90 }}>{t("poInvoice")}</Typography>
                <TextField size="small" placeholder={t("invoiceNo")} value={(inv[po.id] || {}).number || ""}
                  onChange={(e) => setInvField(po.id, "number", e.target.value)} sx={{ width: 130 }} />
                <TextField size="small" type="number" placeholder={t("htLabel")} value={(inv[po.id] || {}).ht || ""}
                  onChange={(e) => setInvField(po.id, "ht", e.target.value)} sx={{ width: 90 }} />
                <TextField size="small" type="number" placeholder={t("tva")} value={(inv[po.id] || {}).tva || ""}
                  onChange={(e) => setInvField(po.id, "tva", e.target.value)} sx={{ width: 90 }} />
                <Button size="small" onClick={() => saveInvoice(po.id)}>{t("save")}</Button>
              </Stack>
              <Divider sx={{ mt: 1 }} />
            </Box>
          ))}
        </Paper>
      </Box>

      <Snackbar open={Boolean(toast)} autoHideDuration={4000} onClose={() => setToast("")}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setToast("")}>{toast}</Alert>
      </Snackbar>
    </Box>
  );
}
