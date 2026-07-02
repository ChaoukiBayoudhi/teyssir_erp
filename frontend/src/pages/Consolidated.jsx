import { useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, Stack, Alert, Chip,
  Table, TableHead, TableRow, TableCell, TableBody, TextField,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { consolidatedReport } from "../api";
import LangToggle from "../LangToggle.jsx";

const monthStart = () => new Date().toISOString().slice(0, 8) + "01";
const today = () => new Date().toISOString().slice(0, 10);

export default function Consolidated({ onBack, onLogout }) {
  const { t } = useTranslation();
  const [rep, setRep] = useState(null);
  const [from, setFrom] = useState(monthStart());
  const [to, setTo] = useState(today());
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try {
      setRep(await consolidatedReport(from, to));
    } catch (err) {
      const m = String(err.message || err);
      setError(m.startsWith("403") ? t("noAccess") : m);
    }
  };
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const g = rep?.grand_total;
  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("consolidated")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("dashboard")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2, maxWidth: 900, mx: "auto" }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

        <Stack direction="row" spacing={2} sx={{ mb: 2 }} alignItems="center">
          <TextField type="date" size="small" label={t("from")} InputLabelProps={{ shrink: true }}
                     value={from} onChange={(e) => setFrom(e.target.value)} />
          <TextField type="date" size="small" label={t("to")} InputLabelProps={{ shrink: true }}
                     value={to} onChange={(e) => setTo(e.target.value)} />
          <Button variant="contained" onClick={load}>{t("refresh")}</Button>
        </Stack>

        {rep && (
          <Paper sx={{ p: 2 }}>
            <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("byStore")}</Typography>
              <Chip color="primary" label={`${t("grandTotal")}: ${g.revenue_inc_tax}`} />
            </Stack>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("store")}</TableCell>
                  <TableCell align="right">{t("salesCount")}</TableCell>
                  <TableCell align="right">{t("revenueExTax")}</TableCell>
                  <TableCell align="right">{t("tva")}</TableCell>
                  <TableCell align="right">{t("revenueIncTax")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rep.stores.map((s) => (
                  <TableRow key={s.store_code || "—"}>
                    <TableCell>{s.store_code || t("singleStore")}</TableCell>
                    <TableCell align="right">{s.sales_count}</TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{s.revenue_ex_tax}</TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{s.tax_total}</TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{s.revenue_inc_tax}</TableCell>
                  </TableRow>
                ))}
                <TableRow>
                  <TableCell sx={{ fontWeight: 700 }}>{t("grandTotal")}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700 }}>{g.sales_count}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{g.revenue_ex_tax}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{g.tax_total}</TableCell>
                  <TableCell align="right" sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{g.revenue_inc_tax}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </Paper>
        )}
      </Box>
    </Box>
  );
}
