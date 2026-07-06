import { useEffect, useRef, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, TextField, Stack, Alert, Snackbar, MenuItem,
  Select, InputLabel, FormControl, ToggleButton, ToggleButtonGroup, Chip, Grid,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { barcodeLookup, createProduct, listCategories, listTaxRates } from "../api";
import LangToggle from "../LangToggle.jsx";

const EMPTY = { name_fr: "", name_ar: "", category: "", tax_rate: "", sale_price: "",
                initial_qty: "", reorder_point: "" };

export default function ProductCreate({ onBack, onLogout }) {
  const { t } = useTranslation();
  const barcodeRef = useRef(null);
  const videoRef = useRef(null);
  const [barcode, setBarcode] = useState("");
  const [existing, setExisting] = useState(null);      // product already using this barcode
  const [isBook, setIsBook] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [cats, setCats] = useState([]);
  const [taxes, setTaxes] = useState([]);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    listCategories().then(setCats).catch(() => {});
    listTaxRates().then((r) => { setTaxes(r); const d = r.find((x) => x.is_default); if (d) setForm((f) => ({ ...f, tax_rate: d.id })); }).catch(() => {});
    barcodeRef.current?.focus();
  }, []);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const lookup = async (code) => {
    setError(""); setExisting(null);
    if (!code) return;
    try {
      const r = await barcodeLookup(code);
      if (r.found) setExisting(r.product);   // duplicate — show it instead of creating
    } catch (e) { setError(String(e.message || e)); }
  };

  const onBarcodeKey = (e) => {
    if (e.key === "Enter") { e.preventDefault(); lookup(barcode.trim()); }   // USB scanner sends Enter
  };

  const stopScan = () => {
    const s = videoRef.current?.srcObject;
    if (s) s.getTracks().forEach((tk) => tk.stop());
    if (videoRef.current) videoRef.current.srcObject = null;
    setScanning(false);
  };

  const scanWithCamera = async () => {
    setError("");
    if (!("BarcodeDetector" in window)) { setError(t("scannerUnsupported")); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      videoRef.current.srcObject = stream; setScanning(true);
      const detector = new window.BarcodeDetector();
      const tick = async () => {
        if (!videoRef.current?.srcObject) return;
        try {
          const codes = await detector.detect(videoRef.current);
          if (codes.length) { const code = codes[0].rawValue; stopScan(); setBarcode(code); lookup(code); return; }
        } catch { /* frame not ready */ }
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    } catch { setError(t("cameraUnavailable")); }
  };

  const save = async () => {
    setError("");
    if (!form.name_fr.trim()) { setError(t("nameRequired")); return; }
    setBusy(true);
    try {
      await createProduct({
        name_fr: form.name_fr, name_ar: form.name_ar, category: form.category,
        tax_rate: form.tax_rate, sale_price: form.sale_price || "0", is_book: isBook,
        barcode: barcode.trim(), initial_qty: form.initial_qty || "0",
        reorder_point: form.reorder_point || "0",
      });
      setToast(t("registered"));
      setForm((f) => ({ ...EMPTY, tax_rate: f.tax_rate })); setBarcode(""); setExisting(null);
      barcodeRef.current?.focus();     // ready for the next scan (continuous entry)
    } catch (e) {
      setError(String(e.message || e).replace(/^\d+:\s*/, ""));
    } finally { setBusy(false); }
  };

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
          <Typography color="text.secondary" sx={{ mb: 1 }}>{t("scanBarcodePrompt")}</Typography>
          <Stack direction="row" spacing={1}>
            <TextField inputRef={barcodeRef} fullWidth size="small" label={t("barcodeLabel")} value={barcode}
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
              {t("alreadyExists")} : <b>{existing.name_fr}</b> — {existing.sale_price} · {t("stock")} {existing.qty_on_hand}
            </Alert>
          )}
        </Paper>

        {!existing && (
          <Paper sx={{ p: 2 }}>
            <ToggleButtonGroup exclusive size="small" value={isBook ? "book" : "supply"} sx={{ mb: 2 }}
                               onChange={(_, v) => v && setIsBook(v === "book")}>
              <ToggleButton value="supply">{t("supplies")}</ToggleButton>
              <ToggleButton value="book">{t("books")}</ToggleButton>
            </ToggleButtonGroup>

            <Stack spacing={2}>
              <TextField label={t("articleName")} value={form.name_fr} onChange={(e) => set("name_fr", e.target.value)} required fullWidth />
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
                <Grid item xs={4}><TextField label={t("price")} type="number" value={form.sale_price} onChange={(e) => set("sale_price", e.target.value)} fullWidth inputProps={{ step: "0.001" }} /></Grid>
                <Grid item xs={4}><TextField label={t("initialQty")} type="number" value={form.initial_qty} onChange={(e) => set("initial_qty", e.target.value)} fullWidth /></Grid>
                <Grid item xs={4}><TextField label={t("reorderPoint")} type="number" value={form.reorder_point} onChange={(e) => set("reorder_point", e.target.value)} fullWidth /></Grid>
              </Grid>
              <Button variant="contained" size="large" disabled={busy || !form.name_fr.trim()} onClick={save}>
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
