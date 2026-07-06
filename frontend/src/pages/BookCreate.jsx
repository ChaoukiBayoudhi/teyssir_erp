import { useRef, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Grid, Paper, TextField, Stack, Alert, Snackbar,
  Chip, IconButton, LinearProgress, Select, MenuItem,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { scanBook, pollScanJob, createBook } from "../api";
import LangToggle from "../LangToggle.jsx";

// Try the browser-native barcode detector for an ISBN (EAN-13). Best-effort, feature-detected.
async function detectIsbn(file) {
  if (!("BarcodeDetector" in window)) return "";
  try {
    const det = new window.BarcodeDetector({ formats: ["ean_13"] });
    const bmp = await createImageBitmap(file);
    const codes = await det.detect(bmp);
    const ean = codes.find((c) => /^97[89]\d{10}$/.test(c.rawValue));
    return ean ? ean.rawValue : (codes[0]?.rawValue || "");
  } catch {
    return "";
  }
}

const EMPTY = {
  isbn13: "", title: "", subtitle: "", authors: "", translators: "", publisher: "",
  series: "", edition: "", pub_year: "", pages: "", languages: "", subject: "",
  description: "", sale_price: "",
};

export default function BookCreate({ onBack, onLogout }) {
  const { t } = useTranslation();
  const videoRef = useRef(null);
  const fileRef = useRef(null);
  const [stream, setStream] = useState(null);
  const [images, setImages] = useState([]);          // File[]
  const [previews, setPreviews] = useState([]);       // object URLs
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const [cameras, setCameras] = useState([]);
  const [cameraId, setCameraId] = useState(localStorage.getItem("teyssir_camera") || "");

  const addImage = (file) => {
    setImages((a) => [...a, file]);
    setPreviews((a) => [...a, URL.createObjectURL(file)]);
  };

  const listCameras = async () => {
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      setCameras(devs.filter((d) => d.kind === "videoinput"));   // labels appear after permission
    } catch { /* enumeration unsupported */ }
  };

  const startCamera = async (deviceId) => {
    setError("");
    try {
      if (stream) stream.getTracks().forEach((tk) => tk.stop());  // release before switching
      const video = deviceId ? { deviceId: { exact: deviceId } } : { facingMode: "environment" };
      const s = await navigator.mediaDevices.getUserMedia({ video });
      setStream(s);
      if (videoRef.current) videoRef.current.srcObject = s;
      if (deviceId) { setCameraId(deviceId); localStorage.setItem("teyssir_camera", deviceId); }
      await listCameras();
    } catch {
      setError("Caméra indisponible — utilisez « Photos »");
    }
  };

  const capture = () => {
    const v = videoRef.current;
    if (!v) return;
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth || 720;
    canvas.height = v.videoHeight || 960;
    canvas.getContext("2d").drawImage(v, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => addImage(new File([blob], `cover-${Date.now()}.jpg`, { type: "image/jpeg" })), "image/jpeg", 0.85);
  };

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const analyze = async () => {
    if (!images.length) return;
    setBusy(true);
    setError("");
    try {
      const isbn = await detectIsbn(images[0]);
      let d = await scanBook(images, isbn);
      if (d.status === "pending") d = await pollScanJob(d.job_id);   // async OCR backend
      setDraft(d);
      setForm({
        ...EMPTY,
        isbn13: d.isbn13 || isbn || "",
        title: d.title || "",
        subtitle: d.subtitle || "",
        authors: (d.authors || []).join(", "),
        translators: (d.translators || []).join(", "),
        publisher: d.publisher || "",
        series: d.series || "",
        edition: d.edition || "",
        pub_year: d.pub_year || "",
        pages: d.pages || "",
        languages: (d.languages || []).join(", "),
        subject: d.subject || "",
        description: d.description || "",
        sale_price: d.price || "",
      });
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await createBook({
        ...form,
        authors: form.authors ? form.authors.split(",").map((s) => s.trim()).filter(Boolean) : [],
        translators: form.translators ? form.translators.split(",").map((s) => s.trim()).filter(Boolean) : [],
        languages: form.languages ? form.languages.split(",").map((s) => s.trim()).filter(Boolean) : [],
        pub_year: form.pub_year ? Number(form.pub_year) : null,
        pages: form.pages ? Number(form.pages) : null,
        source: draft?.source || "manual",
        confidence: draft?.confidence || 0,
        raw: draft?.raw || {},
        image_ids: draft?.image_ids || [],
      });
      setDone(true);
      setImages([]); setPreviews([]); setDraft(null); setForm(EMPTY);
      if (stream) { stream.getTracks().forEach((tk) => tk.stop()); setStream(null); }
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };

  const F = (label, key, props = {}) => (
    <TextField size="small" fullWidth label={label} value={form[key]}
               onChange={(e) => set(key, e.target.value)} {...props} />
  );

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("newBook")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      {busy && <LinearProgress />}
      <Box sx={{ p: 2 }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}
        <Grid container spacing={2}>
          {/* Capture column */}
          <Grid item xs={12} md={5}>
            <Paper sx={{ p: 2 }}>
              <Typography color="text.secondary" sx={{ mb: 1 }}>{t("scanHint")}</Typography>
              <Box sx={{ bgcolor: "#000", borderRadius: 1, overflow: "hidden", mb: 1, minHeight: 180 }}>
                <video ref={videoRef} autoPlay playsInline muted style={{ width: "100%", display: stream ? "block" : "none" }} />
              </Box>
              {stream && cameras.length > 1 && (
                <Select size="small" fullWidth sx={{ mb: 1 }}
                        value={cameraId || cameras[0]?.deviceId || ""}
                        onChange={(e) => startCamera(e.target.value)}>
                  {cameras.map((c, i) => (
                    <MenuItem key={c.deviceId} value={c.deviceId}>{c.label || `${t("camera")} ${i + 1}`}</MenuItem>
                  ))}
                </Select>
              )}
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                {!stream
                  ? <Button variant="outlined" onClick={() => startCamera(cameraId || undefined)}>{t("startCamera")}</Button>
                  : <Button variant="contained" onClick={capture}>{t("capture")}</Button>}
                <Button variant="outlined" onClick={() => fileRef.current?.click()}>{t("choosePhotos")}</Button>
                <input ref={fileRef} type="file" accept="image/*" capture="environment" multiple hidden
                       onChange={(e) => [...e.target.files].forEach(addImage)} />
                <Button variant="contained" color="secondary" disabled={!images.length || busy} onClick={analyze}>
                  {t("analyze")}
                </Button>
              </Stack>
              <Stack direction="row" spacing={1} sx={{ mt: 2, overflowX: "auto" }}>
                {previews.map((src, i) => (
                  <img key={i} src={src} alt="" style={{ height: 84, borderRadius: 6 }} />
                ))}
                {(draft?.images || []).map((im) => (
                  <img key={im.id} src={im.url} alt="" style={{ height: 84, borderRadius: 6 }} />
                ))}
              </Stack>
            </Paper>
          </Grid>

          {/* Review column */}
          <Grid item xs={12} md={7}>
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
                <Typography variant="h6">{t("bookTitle")}</Typography>
                {draft && (
                  <Chip size="small" color={draft.confidence >= 0.8 ? "success" : draft.confidence > 0 ? "warning" : "default"}
                        label={`${t("confidence")}: ${Math.round((draft.confidence || 0) * 100)}% · ${draft.source || "—"}`} />
                )}
              </Stack>
              <Grid container spacing={1.5}>
                <Grid item xs={12} sm={6}>{F("ISBN", "isbn13")}</Grid>
                <Grid item xs={12} sm={6}>{F(t("priceF") + " (DT)", "sale_price", { type: "number" })}</Grid>
                <Grid item xs={12}>{F(t("bookTitle"), "title")}</Grid>
                <Grid item xs={12}>{F(t("subtitleF"), "subtitle")}</Grid>
                <Grid item xs={12} sm={6}>{F(t("authorsF"), "authors")}</Grid>
                <Grid item xs={12} sm={6}>{F(t("translatorsF"), "translators")}</Grid>
                <Grid item xs={12} sm={6}>{F(t("publisherF"), "publisher")}</Grid>
                <Grid item xs={6} sm={3}>{F(t("yearF"), "pub_year", { type: "number" })}</Grid>
                <Grid item xs={6} sm={3}>{F(t("pagesF"), "pages", { type: "number" })}</Grid>
                <Grid item xs={12} sm={6}>{F(t("languagesF"), "languages")}</Grid>
                <Grid item xs={12} sm={6}>{F(t("subjectF"), "subject")}</Grid>
                <Grid item xs={12}>{F(t("descriptionF"), "description", { multiline: true, rows: 2 })}</Grid>
              </Grid>
              <Button variant="contained" size="large" sx={{ mt: 2 }} disabled={!form.title || busy} onClick={save}>
                {t("saveBook")}
              </Button>
            </Paper>
          </Grid>
        </Grid>
      </Box>

      <Snackbar open={done} autoHideDuration={5000} onClose={() => setDone(false)}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setDone(false)}>{t("bookSaved")}</Alert>
      </Snackbar>
    </Box>
  );
}
