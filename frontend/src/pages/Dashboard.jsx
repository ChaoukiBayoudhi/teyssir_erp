import { useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Grid, Paper, Stack, LinearProgress,
  TextField, Alert, Divider,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { salesReport } from "../api";
import LangToggle from "../LangToggle.jsx";

const today = () => new Date().toISOString().slice(0, 10);

function Kpi({ label, value, suffix }) {
  return (
    <Paper sx={{ p: 2, height: "100%" }}>
      <Typography variant="overline" color="text.secondary">{label}</Typography>
      <Typography variant="h4" color="primary" sx={{ fontVariantNumeric: "tabular-nums" }}>
        {value}
        {suffix && <Typography component="span" variant="h6" color="text.secondary"> {suffix}</Typography>}
      </Typography>
    </Paper>
  );
}

export default function Dashboard({ onBack, onLogout, onFinancials, onConsolidated }) {
  const { t } = useTranslation();
  const [from, setFrom] = useState(today());
  const [to, setTo] = useState(today());
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try {
      setData(await salesReport(from, to));
    } catch (e) {
      setData(null);
      setError(String(e.message || e).startsWith("403") ? t("noAccess") : String(e.message || e));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const maxQty = data ? Math.max(1, ...data.best_sellers.map((b) => Number(b.qty))) : 1;

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("dashboard")}</Typography>
          <Button color="inherit" onClick={onFinancials}>{t("accounting")}</Button>
          <Button color="inherit" onClick={onConsolidated}>{t("consolidated")}</Button>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2 }}>
        <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 2 }}>
          <TextField type="date" size="small" label={t("from")} value={from}
                     onChange={(e) => setFrom(e.target.value)} InputLabelProps={{ shrink: true }} />
          <TextField type="date" size="small" label={t("to")} value={to}
                     onChange={(e) => setTo(e.target.value)} InputLabelProps={{ shrink: true }} />
          <Button variant="contained" onClick={load}>{t("refresh")}</Button>
        </Stack>

        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

        {data && (
          <>
            <Grid container spacing={2} sx={{ mb: 1 }}>
              <Grid item xs={6} md={3}><Kpi label={t("revenue")} value={data.revenue_inc_tax} suffix="DT" /></Grid>
              <Grid item xs={6} md={3}><Kpi label={t("profit")} value={data.gross_profit} suffix="DT" /></Grid>
              <Grid item xs={6} md={3}><Kpi label={t("margin")} value={data.margin_pct} suffix="%" /></Grid>
              <Grid item xs={6} md={3}><Kpi label={t("salesCount")} value={data.sales_count} /></Grid>
            </Grid>

            <Grid container spacing={2}>
              <Grid item xs={12} md={7}>
                <Paper sx={{ p: 2 }}>
                  <Typography variant="h6" gutterBottom>{t("bestSellers")}</Typography>
                  {data.best_sellers.length === 0 && <Typography color="text.secondary">—</Typography>}
                  {data.best_sellers.map((b) => (
                    <Box key={b.sku} sx={{ mb: 1.5 }}>
                      <Stack direction="row" justifyContent="space-between">
                        <Typography>{b.name}</Typography>
                        <Typography color="text.secondary" sx={{ fontVariantNumeric: "tabular-nums" }}>
                          {Number(b.qty)} · {b.revenue} DT
                        </Typography>
                      </Stack>
                      <LinearProgress variant="determinate"
                                      value={(Number(b.qty) / maxQty) * 100}
                                      sx={{ height: 8, borderRadius: 4 }} />
                    </Box>
                  ))}
                </Paper>
              </Grid>

              <Grid item xs={12} md={5}>
                <Paper sx={{ p: 2, mb: 2 }}>
                  <Typography variant="h6" gutterBottom>{t("paymentMix")}</Typography>
                  {data.payment_mix.map((p) => (
                    <Stack key={p.method} direction="row" justifyContent="space-between" sx={{ py: 0.5 }}>
                      <Typography>{p.method}</Typography>
                      <Typography sx={{ fontVariantNumeric: "tabular-nums" }}>{p.amount} DT</Typography>
                    </Stack>
                  ))}
                </Paper>
                <Paper sx={{ p: 2 }}>
                  <Typography variant="h6" gutterBottom>{t("tvaBreakdown")}</Typography>
                  {data.tva_by_rate.map((r) => (
                    <Stack key={r.rate} direction="row" justifyContent="space-between" sx={{ py: 0.5 }}>
                      <Typography>TVA {Number(r.rate)}%</Typography>
                      <Typography sx={{ fontVariantNumeric: "tabular-nums" }}>{r.base} DT</Typography>
                    </Stack>
                  ))}
                  <Divider sx={{ my: 1 }} />
                  <Stack direction="row" justifyContent="space-between">
                    <Typography color="text.secondary">{t("timbre")}</Typography>
                    <Typography sx={{ fontVariantNumeric: "tabular-nums" }}>{data.timbre_total} DT</Typography>
                  </Stack>
                </Paper>
              </Grid>
            </Grid>
          </>
        )}
      </Box>
    </Box>
  );
}
