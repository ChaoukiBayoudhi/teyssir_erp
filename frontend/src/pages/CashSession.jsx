import { useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, Stack, TextField, Divider, Alert,
  Snackbar, Select, MenuItem,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { cashOpen, cashX, cashZ } from "../api";
import LangToggle from "../LangToggle.jsx";

function Row({ label, value, bold }) {
  return (
    <Stack direction="row" justifyContent="space-between" sx={{ py: 0.5 }}>
      <Typography fontWeight={bold ? 700 : 400}>{label}</Typography>
      <Typography fontWeight={bold ? 700 : 400} sx={{ fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
    </Stack>
  );
}

export default function CashSession({ onBack, onLogout }) {
  const { t } = useTranslation();
  const [terminal, setTerminal] = useState("C1");
  const [report, setReport] = useState(null);     // X report when a session is open
  const [openingFloat, setOpeningFloat] = useState("0.000");
  const [counted, setCounted] = useState("");
  const [closed, setClosed] = useState(null);      // Z result
  const [error, setError] = useState("");

  const loadX = async (term = terminal) => {
    setError("");
    setClosed(null);
    try {
      setReport(await cashX(term));
    } catch (err) {
      // 404 => no open session for this terminal
      setReport(String(err.message || err).startsWith("404") ? null : null);
      if (!String(err.message || err).startsWith("404")) setError(String(err.message || err));
    }
  };

  useEffect(() => {
    loadX(terminal);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terminal]);

  const open = async () => {
    setError("");
    try {
      await cashOpen(terminal, openingFloat || "0");
      await loadX(terminal);
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  const close = async () => {
    setError("");
    try {
      const z = await cashZ(terminal, counted || "0");
      setClosed(z);
      setReport(null);
      setCounted("");
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("session")}</Typography>
          <Select size="small" value={terminal} onChange={(e) => setTerminal(e.target.value)}
                  sx={{ bgcolor: "white", minWidth: 80 }}>
            {["C1", "C2", "C3"].map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
          </Select>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2, maxWidth: 480, mx: "auto" }}>
        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

        {!report ? (
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" gutterBottom>{t("noSession")}</Typography>
            <Stack spacing={2}>
              <TextField label={t("openingFloat")} type="number" value={openingFloat}
                         onChange={(e) => setOpeningFloat(e.target.value)} />
              <Button variant="contained" size="large" onClick={open}>{t("openSession")}</Button>
            </Stack>
          </Paper>
        ) : (
          <Paper sx={{ p: 3 }}>
            <Typography variant="h6" color="primary" gutterBottom>
              {t("sessionOpen")} · {report.terminal}
            </Typography>
            <Row label={t("openingFloat")} value={`${report.opening_float} DT`} />
            <Row label={t("salesCount")} value={report.sales_count} />
            <Divider sx={{ my: 1 }} />
            <Row label={t("cashSales")} value={`${report.cash_sales} DT`} />
            <Row label={t("cardSales")} value={`${report.card_sales} DT`} />
            <Row label={t("accountSales")} value={`${report.account_sales} DT`} />
            <Divider sx={{ my: 1 }} />
            <Row label={t("expectedCash")} value={`${report.expected_cash} DT`} bold />

            <Stack spacing={2} sx={{ mt: 3 }}>
              <TextField label={t("countedCash")} type="number" value={counted}
                         onChange={(e) => setCounted(e.target.value)} />
              <Button variant="contained" color="error" size="large" onClick={close}>
                {t("closeZ")}
              </Button>
            </Stack>
          </Paper>
        )}
      </Box>

      <Snackbar open={Boolean(closed)} autoHideDuration={8000} onClose={() => setClosed(null)}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity={closed && Number(closed.variance) === 0 ? "success" : "warning"}
               variant="filled" onClose={() => setClosed(null)}>
          {closed && `Z · ${t("expectedCash")}: ${closed.expected_cash} · ${t("countedCash")}: ${closed.counted_cash} · ${t("variance")}: ${closed.variance} DT`}
        </Alert>
      </Snackbar>
    </Box>
  );
}
