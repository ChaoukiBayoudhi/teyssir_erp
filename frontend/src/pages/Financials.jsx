import { useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Grid, Paper, Stack, Divider, Alert, Chip,
  Table, TableHead, TableRow, TableCell, TableBody, TextField,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { financials, trialBalance, vatDeclaration } from "../api";
import LangToggle from "../LangToggle.jsx";

const monthStart = () => new Date().toISOString().slice(0, 8) + "01";
const today = () => new Date().toISOString().slice(0, 10);

function Line({ label, value, bold }) {
  return (
    <Stack direction="row" justifyContent="space-between" sx={{ py: 0.4 }}>
      <Typography fontWeight={bold ? 700 : 400}>{label}</Typography>
      <Typography fontWeight={bold ? 700 : 400} sx={{ fontVariantNumeric: "tabular-nums" }}>
        {value}
      </Typography>
    </Stack>
  );
}

export default function Financials({ onBack, onLogout }) {
  const { t } = useTranslation();
  const [fs, setFs] = useState(null);
  const [tb, setTb] = useState(null);
  const [vat, setVat] = useState(null);
  const [from, setFrom] = useState(monthStart());
  const [to, setTo] = useState(today());
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try {
      const [f, b, v] = await Promise.all([financials(), trialBalance(), vatDeclaration(from, to)]);
      setFs(f); setTb(b); setVat(v);
    } catch (err) {
      setError(String(err.message || err).startsWith("403") ? t("noAccess") : String(err.message || err));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("accounting")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2 }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

        <Grid container spacing={2}>
          <Grid item xs={12} md={4}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>{t("incomeStatement")}</Typography>
              {fs && (
                <>
                  <Line label={t("revenue")} value={`${fs.income_statement.revenue} DT`} />
                  <Line label={t("expenses")} value={`${fs.income_statement.expenses} DT`} />
                  <Divider sx={{ my: 1 }} />
                  <Line label={t("netIncome")} value={`${fs.income_statement.net_income} DT`} bold />
                </>
              )}
            </Paper>
          </Grid>

          <Grid item xs={12} md={4}>
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center">
                <Typography variant="h6">{t("balanceSheet")}</Typography>
                {fs && (
                  <Chip size="small" color={fs.balance_sheet.balanced ? "success" : "error"}
                        label={t("balanced")} />
                )}
              </Stack>
              {fs && (
                <Box sx={{ mt: 1 }}>
                  <Line label={t("assets")} value={`${fs.balance_sheet.assets} DT`} bold />
                  <Line label={t("liabilities")} value={`${fs.balance_sheet.liabilities} DT`} />
                  <Line label={t("equity")} value={`${fs.balance_sheet.equity} DT`} />
                </Box>
              )}
            </Paper>
          </Grid>

          <Grid item xs={12} md={4}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>{t("vatDeclaration")}</Typography>
              <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                <TextField size="small" type="date" value={from}
                           onChange={(e) => setFrom(e.target.value)} InputLabelProps={{ shrink: true }} />
                <TextField size="small" type="date" value={to}
                           onChange={(e) => setTo(e.target.value)} InputLabelProps={{ shrink: true }} />
                <Button variant="outlined" onClick={load}>{t("refresh")}</Button>
              </Stack>
              {vat && (
                <>
                  <Line label={t("vatCollected")} value={`${vat.tva_collected} DT`} />
                  <Line label={t("vatDeductible")} value={`${vat.tva_deductible} DT`} />
                  <Divider sx={{ my: 1 }} />
                  <Line label={t("vatNet")} value={`${vat.net_payable} DT`} bold />
                </>
              )}
            </Paper>
          </Grid>

          <Grid item xs={12}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6" gutterBottom>{t("trialBalanceT")}</Typography>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>{t("account")}</TableCell>
                    <TableCell align="right">{t("debit")}</TableCell>
                    <TableCell align="right">{t("credit")}</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {tb?.rows.map((r) => (
                    <TableRow key={r.code}>
                      <TableCell>{r.code} · {r.name}</TableCell>
                      <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{r.debit}</TableCell>
                      <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{r.credit}</TableCell>
                    </TableRow>
                  ))}
                  {tb && (
                    <TableRow>
                      <TableCell sx={{ fontWeight: 700 }}>Σ</TableCell>
                      <TableCell align="right" sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{tb.total_debit}</TableCell>
                      <TableCell align="right" sx={{ fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{tb.total_credit}</TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </Paper>
          </Grid>
        </Grid>
      </Box>
    </Box>
  );
}
