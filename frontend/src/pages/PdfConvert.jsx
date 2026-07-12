import { useRef, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, Stack, Alert, LinearProgress, Chip,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { convertPdf } from "../api";
import LangToggle from "../LangToggle.jsx";

export default function PdfConvert({ onBack, onLogout }) {
  const { t } = useTranslation();
  const fileRef = useRef(null);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [doneName, setDoneName] = useState("");
  const [error, setError] = useState("");

  const pick = (f) => {
    setError(""); setDoneName("");
    if (f && !/\.pdf$/i.test(f.name)) { setError(t("pdfOnly")); return; }
    setFile(f || null);
  };

  const convert = async () => {
    if (!file) return;
    setBusy(true); setError(""); setDoneName("");
    try {
      const blob = await convertPdf(file);
      const outName = file.name.replace(/\.pdf$/i, "") + ".docx";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = outName;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      setDoneName(outName);                       // downloaded — Word/LibreOffice opens it
    } catch (e) {
      setError(String(e.message || e));
    } finally { setBusy(false); }
  };

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("pdfToWord")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2, maxWidth: 560, mx: "auto" }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setError("")}>{error}</Alert>}
        {doneName && <Alert severity="success" sx={{ mb: 2 }}>{t("converted")} : <b>{doneName}</b></Alert>}

        <Paper
          sx={{ p: 4, textAlign: "center", border: "2px dashed #bbb", cursor: "pointer",
                bgcolor: file ? "#f0f7f0" : "inherit" }}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files?.[0]); }}
        >
          <Typography variant="h2" sx={{ mb: 1 }}>📄</Typography>
          {file
            ? <Chip color="primary" label={`${file.name} · ${(file.size / 1024).toFixed(0)} Ko`}
                    onDelete={() => pick(null)} />
            : <Typography color="text.secondary">{t("dropPdfHere")}</Typography>}
          <input ref={fileRef} type="file" accept="application/pdf,.pdf" hidden
                 onChange={(e) => pick(e.target.files?.[0])} />
        </Paper>

        {busy && <LinearProgress sx={{ mt: 2 }} />}
        <Stack direction="row" justifyContent="center" sx={{ mt: 2 }}>
          <Button variant="contained" size="large" disabled={!file || busy} onClick={convert}>
            {busy ? t("converting") : t("convertToWord")}
          </Button>
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 2, textAlign: "center" }}>
          {t("pdfConvertNote")}
        </Typography>
      </Box>
    </Box>
  );
}
