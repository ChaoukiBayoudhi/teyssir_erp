import { useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, TextField, List, ListItemButton,
  ListItemText, Table, TableHead, TableRow, TableCell, TableBody, Stack, Alert, Snackbar,
  Select, MenuItem, Divider,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { searchProducts, listSuppliers, createSupplier, receiveGoods } from "../api";
import LangToggle from "../LangToggle.jsx";
import { fmtQty } from "../format.js";

export default function Receiving({ onBack, onLogout }) {
  const { t } = useTranslation();
  const [suppliers, setSuppliers] = useState([]);
  const [supplier, setSupplier] = useState("");
  const [newName, setNewName] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [rows, setRows] = useState([]); // { product, qty, cost }
  const [done, setDone] = useState(null);
  const [error, setError] = useState("");

  const loadSuppliers = async () => {
    try {
      const list = await listSuppliers();
      setSuppliers(list);
      if (!supplier && list.length) setSupplier(list[0].id);
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  useEffect(() => {
    loadSuppliers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addSupplier = async () => {
    if (!newName.trim()) return;
    setError("");
    try {
      const s = await createSupplier(newName.trim());
      setNewName("");
      await loadSuppliers();
      setSupplier(s.id);
    } catch (err) {
      setError(String(err.message || err));
    }
  };

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
        : [...r, { product: p, qty: "1", cost: String(p.sale_price || "0") }]
    );
  };

  const setField = (id, field, v) =>
    setRows((r) => r.map((x) => (x.product.id === id ? { ...x, [field]: v } : x)));

  const submit = async () => {
    setError("");
    try {
      const res = await receiveGoods({
        supplier,
        terminal: "C1",
        items: rows.map((x) => ({ product: x.product.id, qty: x.qty || "0", unit_cost: x.cost || "0" })),
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
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("receiving")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2, maxWidth: 760, mx: "auto" }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

        <Paper sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" spacing={2} alignItems="center">
            <Typography sx={{ minWidth: 90 }}>{t("supplier")}</Typography>
            <Select size="small" value={supplier} onChange={(e) => setSupplier(e.target.value)}
                    sx={{ minWidth: 220 }} displayEmpty>
              {suppliers.length === 0 && <MenuItem value="">—</MenuItem>}
              {suppliers.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </Select>
            <Divider orientation="vertical" flexItem />
            <TextField size="small" placeholder={t("newSupplier")} value={newName}
                       onChange={(e) => setNewName(e.target.value)} />
            <Button variant="outlined" onClick={addSupplier}>{t("add")}</Button>
          </Stack>
        </Paper>

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
                <TableCell align="right">{t("qty")}</TableCell>
                <TableCell align="right">{t("unitCost")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((x) => (
                <TableRow key={x.product.id}>
                  <TableCell>{x.product.name_fr}</TableCell>
                  <TableCell align="right">
                    <TextField size="small" type="number" value={x.qty}
                               onChange={(e) => setField(x.product.id, "qty", e.target.value)}
                               sx={{ width: 90 }} inputProps={{ min: 0, step: 1, style: { textAlign: "right" } }} />
                  </TableCell>
                  <TableCell align="right">
                    <TextField size="small" type="number" value={x.cost}
                               onChange={(e) => setField(x.product.id, "cost", e.target.value)}
                               sx={{ width: 110 }} inputProps={{ min: 0, step: "0.001", style: { textAlign: "right" } }} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Button variant="contained" size="large" sx={{ mt: 2 }}
                  disabled={!rows.length || !supplier} onClick={submit}>
            {t("doReceive")}
          </Button>
        </Paper>
      </Box>

      <Snackbar open={Boolean(done)} autoHideDuration={5000} onClose={() => setDone(null)}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setDone(null)}>
          {done && `${done.lines.length} ${t("received")}`}
        </Alert>
      </Snackbar>
    </Box>
  );
}
