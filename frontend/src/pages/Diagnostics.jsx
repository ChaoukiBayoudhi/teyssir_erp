import { useCallback, useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, Stack, Alert, Chip, LinearProgress,
  Table, TableBody, TableCell, TableRow,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { fetchDiagnostics } from "../api";
import LangToggle from "../LangToggle.jsx";

function StatusChip({ ok, label }) {
  return (
    <Chip
      size="small"
      color={ok ? "success" : "warning"}
      label={label}
      variant={ok ? "filled" : "outlined"}
    />
  );
}

export default function Diagnostics({ onBack, onLogout }) {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [cameraNote, setCameraNote] = useState("");

  const probeCamera = useCallback(async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        setCameraNote(t("diagCameraUnsupported"));
        return;
      }
      const secure = window.isSecureContext || ["localhost", "127.0.0.1"].includes(location.hostname);
      if (!secure) {
        setCameraNote(t("diagCameraInsecure"));
        return;
      }
      const stream = await navigator.mediaDevices.getUserMedia({ video: true });
      stream.getTracks().forEach((tk) => tk.stop());
      const devices = await navigator.mediaDevices.enumerateDevices();
      const cams = devices.filter((d) => d.kind === "videoinput");
      setCameraNote(t("diagCameraOk", { count: cams.length || 1 }));
    } catch {
      setCameraNote(t("diagCameraDenied"));
    }
  }, [t]);

  const load = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const d = await fetchDiagnostics();
      setData(d);
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
    probeCamera();
  }, [load, probeCamera]);

  const rows = data
    ? [
        ["DB", data.db?.ok, data.db?.ok ? `${data.db.vendor} · ${data.role}` : (data.db?.error || "—")],
        ["Tesseract", data.tesseract?.installed,
          data.tesseract?.installed
            ? `${data.tesseract.path || "—"} · ${(data.tesseract.langs || []).join("+") || "langs?"}`
            : t("diagTesseractMissing")],
        ["OCR", data.ocr?.working, data.ocr?.detail || data.ocr?.provider || "—"],
        ["Printer", data.printer?.reachable,
          `${data.printer?.target || "—"} — ${data.printer?.detail || ""}`],
        ["LLM", data.llm?.enabled ? data.llm?.reachable !== false : true,
          data.llm?.enabled
            ? `${data.llm.provider}/${data.llm.model} · ${data.llm.reachable ? "up" : "down"}`
            : t("diagLlmDisabled")],
        ["Camera", !/denied|unsupported|insecure/i.test(cameraNote), cameraNote || "…"],
      ]
    : [];

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("diagnostics")}</Typography>
          <Button color="inherit" onClick={load} disabled={busy}>{t("refresh")}</Button>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>
      {busy && <LinearProgress />}
      <Box sx={{ p: 2, maxWidth: 800, mx: "auto" }}>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        <Paper sx={{ p: 2 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <Typography variant="h6">Teyssir Diagnostics</Typography>
            {data && <Chip size="small" label={`${data.role}${data.terminal ? ` · ${data.terminal}` : ""}`} />}
          </Stack>
          <Table size="small">
            <TableBody>
              {rows.map(([label, ok, detail]) => (
                <TableRow key={label}>
                  <TableCell sx={{ width: 120, fontWeight: 600 }}>{label}</TableCell>
                  <TableCell sx={{ width: 100 }}>
                    <StatusChip ok={Boolean(ok)} label={ok ? t("diagOk") : t("diagWarn")} />
                  </TableCell>
                  <TableCell sx={{ fontFamily: "ui-monospace, monospace", fontSize: 13 }}>{detail}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {!data && !busy && !error && (
            <Typography color="text.secondary">{t("diagLoading")}</Typography>
          )}
        </Paper>
      </Box>
    </Box>
  );
}
