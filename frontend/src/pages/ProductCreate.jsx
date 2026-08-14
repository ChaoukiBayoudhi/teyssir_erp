import { useEffect, useRef, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, TextField, Stack, Alert, Snackbar, MenuItem,
  Select, InputLabel, FormControl, ToggleButton, ToggleButtonGroup, Grid,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { barcodeLookup, createProduct, listCategories, listTaxRates } from "../api";
import LangToggle from "../LangToggle.jsx";
import { fmtQty } from "../format.js";

const EMPTY = {
  name_fr: "", name_ar: "", category: "", tax_rate: "", sale_price: "",
  initial_qty: "", reorder_point: "", reference: "", color: "", brand: "",
  isbn: "", authors: "",
};

export default function ProductCreate({ onBack, onLogout, onNewBook }) {
  const { t } = useTranslation();
  const barcodeRef = useRef(null);
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const [barcode, setBarcode] = useState("");
  const [existing, setExisting] = useState(null);
  const [productType, setProductType] = useState("furniture"); // furniture | book
  const [form, setForm] = useState(EMPTY);
  const [cats, setCats] = useState([]);
  const [taxes, setTaxes] = useState([]);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");

  const isBook = productType === "book";

  useEffect(() => {
    listCategories().then(setCats).catch(() => {});
    listTaxRates().then((r) => {
      setTaxes(r);
      const d = r.find((x) => x.is_default);
      if (d) setForm((f) => ({ ...f, tax_rate: d.id }));
    }).catch(() => {});
    barcodeRef.current?.focus();
    return () => stopScan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const lookup = async (code) => {
    setError(""); setExisting(null);
    if (!code) return;
    try {
      const r = await barcodeLookup(code);
      if (r.found) setExisting(r.product);
    } catch (e) { setError(String(e.message || e)); }
  };

  const onBarcodeKey = (e) => {
    if (e.key === "Enter") { e.preventDefault(); lookup(barcode.trim()); }
  };

  const stopScan = () => {
    const s = streamRef.current || videoRef.current?.srcObject;
    if (s) s.getTracks().forEach((tk) => tk.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setScanning(false);
  };

  const scanWithCamera = async () => {
    setError("");
    if (!("BarcodeDetector" in window)) { setError(t("scannerUnsupported")); return; }
    try {
      stopScan();
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      streamRef.current = stream;
      videoRef.current.srcObject = stream;
      setScanning(true);
      const detector = new window.BarcodeDetector();
      const tick = async () => {
        if (!videoRef.current?.srcObject) return;
        try {
          const codes = await detector.detect(videoRef.current);
          if (codes.length) {
            const code = codes[0].rawValue;
            stopScan();
            setBarcode(code);
            lookup(code);
            return;
          }
        } catch { /* frame not ready */ }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    } catch { setError(t("cameraUnavailable")); }
  };

  const save = async () => {
    setError("");
    if (!form.name_fr.trim()) { setError(t("nameRequired")); return; }
    if (!isBook && !form.reference.trim()) { setError(t("referenceRequired")); return; }
    setBusy(true);
    try {
      await createProduct({
        name_fr: form.name_fr, name_ar: form.name_ar, category: form.category,
        tax_rate: form.tax_rate, sale_price: form.sale_price || "0",
        product_type: productType, is_book: isBook,
        reference: form.reference.trim(), color: form.color, brand: form.brand,
        isbn: form.isbn.trim(),
        barcode: barcode.trim(), initial_qty: form.initial_qty || "0",
        reorder_point: form.reorder_point || "0",
      });
      setToast(t("registered"));
      setForm((f) => ({ ...EMPTY, tax_rate: f.tax_rate }));
      setBarcode(""); setExisting(null);
      barcodeRef.current?.focus();
    } catch (e) {
      setError(String(e.message || e).replace(/^\d+:\s*/, ""));
    } finally { setBusy(false); }
  };

  const furnitureReady = Boolean(form.name_fr.trim() && form.reference.trim());
  const bookReady = Boolean(form.name_fr.trim());

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("newArticle")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2, maxWidth: 640, mx: "auto" }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}

        <Paper sx={{ p: 2, mb: 2 }}>
          <ToggleButtonGroup exclusive size="small" value={productType} sx={{ mb: 2 }}
                             onChange={(_, v) => { if (v) { setProductType(v); setExisting(null); stopScan(); } }}>
            <ToggleButton value="furniture">{t("furniture")}</ToggleButton>
            <ToggleButton value="book">{t("books")}</ToggleButton>
          </ToggleButtonGroup>

          <Typography color="text.secondary" sx={{ mb: 1 }}>
            {isBook ? t("scanIsbnPrompt") : t("scanFurniturePrompt")}
          </Typography>
          <Stack direction="row" spacing={1}>
            <TextField inputRef={barcodeRef} fullWidth size="small"
                       label={isBook ? t("isbnOrBarcode") : t("barcodeOptional")}
                       value={barcode}
                       onChange={(e) => setBarcode(e.target.value)} onKeyDown={onBarcodeKey}
                       onBlur={() => barcode && lookup(barcode.trim())} autoFocus />
            {!scanning
              ? <Button variant="outlined" onClick={scanWithCamera}>📷</Button>
              : <Button variant="outlined" color="error" onClick={stopScan}>■</Button>}
          </Stack>
          <Box sx={{ mt: scanning ? 1 : 0 }}>
            <video ref={videoRef} autoPlay playsInline muted
                   style={{ width: "100%", borderRadius: 8, display: scanning ? "block" : "none" }} />
          </Box>
          {existing && (
            <Alert severity="info" sx={{ mt: 1 }}>
              {t("alreadyExists")} : <b>{existing.name_fr}</b> — {existing.sale_price} · {t("stock")} {fmtQty(existing.qty_on_hand)}
            </Alert>
          )}
        </Paper>

        {!existing && (
          <Paper sx={{ p: 2 }}>
            <Stack spacing={2}>
              {isBook ? (
                <>
                  {onNewBook && (
                    <Button variant="outlined" onClick={onNewBook}>{t("ocrBookCamera")}</Button>
                  )}
                  <TextField label="ISBN" value={form.isbn} onChange={(e) => set("isbn", e.target.value)} fullWidth />
                  <TextField label={t("bookTitle")} value={form.name_fr} onChange={(e) => set("name_fr", e.target.value)} required fullWidth />
                  <TextField label={t("authorsF")} value={form.authors} onChange={(e) => set("authors", e.target.value)} fullWidth />
                </>
              ) : (
                <>
                  <TextField label={t("reference")} value={form.reference} required fullWidth
                             onChange={(e) => set("reference", e.target.value)}
                             helperText={t("referenceHint")} />
                  <TextField label={t("articleName")} value={form.name_fr} onChange={(e) => set("name_fr", e.target.value)} required fullWidth />
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
              <TextField label={t("nameArLabel")} value={form.name_ar} onChange={(e) => set("name_ar", e.target.value)} fullWidth
                         inputProps={{ dir: "rtl" }} />
              <Grid container spacing={2}>
                <Grid item xs={6}>
                  <FormControl fullWidth size="small">
                    <InputLabel>{t("category")}</InputLabel>
                    <Select label={t("category")} value={form.category} onChange={(e) => set("category", e.target.value)}>
                      <MenuItem value="">—</MenuItem>
                      {cats.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={6}>
                  <FormControl fullWidth size="small">
                    <InputLabel>{t("taxRate")}</InputLabel>
                    <Select label={t("taxRate")} value={form.tax_rate} onChange={(e) => set("tax_rate", e.target.value)}>
                      {taxes.map((x) => <MenuItem key={x.id} value={x.id}>{x.name}</MenuItem>)}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={4}><TextField label={`${t("price")} (DT)`} type="number" value={form.sale_price} onChange={(e) => set("sale_price", e.target.value)} fullWidth inputProps={{ min: 0, step: "0.001" }} /></Grid>
                <Grid item xs={4}><TextField label={t("initialQty")} type="number" value={form.initial_qty} onChange={(e) => set("initial_qty", e.target.value)} fullWidth inputProps={{ min: 0, step: 1 }} /></Grid>
                <Grid item xs={4}><TextField label={t("reorderPoint")} type="number" value={form.reorder_point} onChange={(e) => set("reorder_point", e.target.value)} fullWidth inputProps={{ min: 0, step: 1 }} /></Grid>
              </Grid>
              <Button variant="contained" size="large"
                      disabled={busy || (isBook ? !bookReady : !furnitureReady)} onClick={save}>
                {t("register")}
              </Button>
            </Stack>
          </Paper>
        )}
      </Box>

      <Snackbar open={Boolean(toast)} autoHideDuration={3000} onClose={() => setToast("")}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setToast("")}>{toast}</Alert>
      </Snackbar>
    </Box>
  );
}
