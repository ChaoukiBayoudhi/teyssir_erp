import { useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, TextField, Stack, Alert, Snackbar,
  MenuItem, Select, InputLabel, FormControl, Grid, CircularProgress, Chip,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { productDetail, updateProduct, listCategories } from "../api";
import LangToggle from "../LangToggle.jsx";
import { fmtQty } from "../format.js";

export default function ProductEdit({ productId, onBack, onLogout, onSaved }) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [cats, setCats] = useState([]);
  const [meta, setMeta] = useState({ is_book: false, product_type: "furniture" });
  const [form, setForm] = useState({
    name_fr: "", name_ar: "", category: "", tax_rate: "", sale_price: "",
    reorder_point: "", qty_on_hand: "0", reference: "", color: "", brand: "", isbn: "",
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setError("");
      try {
        const [detail, categories] = await Promise.all([
          productDetail(productId), listCategories(),
        ]);
        if (cancelled) return;
        setCats(categories);
        setMeta({
          is_book: Boolean(detail.is_book),
          product_type: detail.product_type || (detail.is_book ? "book" : "furniture"),
        });
        setForm({
          name_fr: detail.name_fr || "",
          name_ar: detail.name_ar || "",
          category: detail.category_id || "",
          tax_rate: detail.tax_rate || "",
          sale_price: detail.sale_price || "",
          reorder_point: detail.reorder_point || "",
          qty_on_hand: fmtQty(detail.qty_on_hand),
          reference: detail.reference || "",
          color: detail.color || "",
          brand: detail.brand || "",
          isbn: detail.isbn || detail.book?.isbn13 || "",
        });
      } catch (e) {
        if (!cancelled) setError(String(e.message || e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [productId]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const isBook = meta.is_book;

  const save = async () => {
    setError("");
    if (!form.name_fr.trim()) { setError(t("nameRequired")); return; }
    if (!isBook && !form.reference.trim()) { setError(t("referenceRequired")); return; }
    setBusy(true);
    try {
      const updated = await updateProduct(productId, {
        name_fr: form.name_fr,
        name_ar: form.name_ar,
        category: form.category,
        tax_rate: form.tax_rate,
        sale_price: form.sale_price || "0",
        reorder_point: form.reorder_point || "0",
        qty_on_hand: form.qty_on_hand === "" ? "0" : form.qty_on_hand,
        reference: form.reference.trim(),
        color: form.color,
        brand: form.brand,
        isbn: form.isbn.trim(),
      });
      setForm((f) => ({ ...f, qty_on_hand: fmtQty(updated.qty_on_hand) }));
      setToast(t("productUpdated"));
      onSaved?.();
    } catch (e) {
      setError(String(e.message || e).replace(/^\d+:\s*/, ""));
    } finally {
      setBusy(false);
    }
  };

  const ready = Boolean(form.name_fr.trim()) && (isBook || Boolean(form.reference.trim()));

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("editArticle")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("catalog")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2, maxWidth: 640, mx: "auto" }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
        {loading ? (
          <Box sx={{ textAlign: "center", py: 6 }}><CircularProgress /></Box>
        ) : (
          <Paper sx={{ p: 2 }}>
            <Stack direction="row" spacing={1} sx={{ mb: 2 }} alignItems="center">
              <Chip size="small" label={isBook ? t("books") : t("furniture")} />
            </Stack>
            <Stack spacing={2}>
              {isBook ? (
                <>
                  <TextField label="ISBN" value={form.isbn} onChange={(e) => set("isbn", e.target.value)} fullWidth />
                  <TextField label={t("bookTitle")} value={form.name_fr} onChange={(e) => set("name_fr", e.target.value)}
                             required fullWidth />
                </>
              ) : (
                <>
                  <TextField label={t("reference")} value={form.reference} required fullWidth
                             onChange={(e) => set("reference", e.target.value)}
                             helperText={t("referenceHint")} />
                  <TextField label={t("articleName")} value={form.name_fr} onChange={(e) => set("name_fr", e.target.value)}
                             required fullWidth />
                  <Grid container spacing={2}>
                    <Grid item xs={6}>
                      <TextField label={t("color")} value={form.color} onChange={(e) => set("color", e.target.value)} fullWidth />
                    </Grid>
                    <Grid item xs={6}>
                      <TextField label={t("brand")} value={form.brand} onChange={(e) => set("brand", e.target.value)} fullWidth />
                    </Grid>
                  </Grid>
                </>
              )}
              <TextField label={t("nameArLabel")} value={form.name_ar} onChange={(e) => set("name_ar", e.target.value)}
                         fullWidth inputProps={{ dir: "rtl" }} />
              <Grid container spacing={2}>
                <Grid item xs={12}>
                  <FormControl fullWidth size="small">
                    <InputLabel>{t("category")}</InputLabel>
                    <Select label={t("category")} value={form.category} onChange={(e) => set("category", e.target.value)}>
                      <MenuItem value="">—</MenuItem>
                      {cats.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={4}>
                  <TextField label={`${t("price")} (DT)`} type="number" value={form.sale_price}
                             onChange={(e) => set("sale_price", e.target.value)} fullWidth
                             inputProps={{ min: 0, step: "0.001" }} />
                </Grid>
                <Grid item xs={4}>
                  <TextField label={t("stock")} type="number" value={form.qty_on_hand}
                             onChange={(e) => set("qty_on_hand", e.target.value)} fullWidth
                             inputProps={{ min: 0, step: 1 }} />
                </Grid>
                <Grid item xs={4}>
                  <TextField label={t("reorderPoint")} type="number" value={form.reorder_point}
                             onChange={(e) => set("reorder_point", e.target.value)} fullWidth
                             inputProps={{ min: 0, step: 1 }} />
                </Grid>
              </Grid>
              <Stack direction="row" spacing={1}>
                <Button variant="outlined" onClick={onBack}>{t("cancel")}</Button>
                <Button variant="contained" size="large" disabled={busy || !ready} onClick={save} sx={{ flex: 1 }}>
                  {t("save")}
                </Button>
              </Stack>
            </Stack>
          </Paper>
        )}
      </Box>

      <Snackbar open={Boolean(toast)} autoHideDuration={2000} onClose={() => { setToast(""); onBack?.(); }}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => { setToast(""); onBack?.(); }}>{toast}</Alert>
      </Snackbar>
    </Box>
  );
}
