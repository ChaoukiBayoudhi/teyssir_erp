import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Grid, Paper, Stack,
  TextField, Alert, Chip, FormControl, InputLabel, Select, MenuItem,
  FormControlLabel, Switch, ToggleButton, ToggleButtonGroup, useMediaQuery,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { useTranslation } from "react-i18next";
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, LineChart, Line,
} from "recharts";
import { salesReport } from "../api";
import LangToggle from "../LangToggle.jsx";

const PREFS_KEY = "teyssir.dashboard.prefs";
const CHART_COLORS = ["#1B5E20", "#2E7D32", "#558B2F", "#8D6E63", "#F9A825", "#546E7A", "#6A1B9A"];

const today = () => new Date().toISOString().slice(0, 10);

function addDays(iso, n) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function monthStart(iso) {
  return `${iso.slice(0, 7)}-01`;
}

function defaultPrefs() {
  return {
    preset: "today",
    payment: "",
    product_type: "",
    terminal: "",
    store: "",
    charts: {
      series: true,
      products: true,
      payments: true,
      vat: true,
      category: true,
      margin: true,
      hourly: true,
    },
  };
}

function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return defaultPrefs();
    return { ...defaultPrefs(), ...JSON.parse(raw), charts: { ...defaultPrefs().charts, ...(JSON.parse(raw).charts || {}) } };
  } catch {
    return defaultPrefs();
  }
}

function savePrefs(prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch { /* ignore quota */ }
}

function rangeForPreset(preset) {
  const t = today();
  switch (preset) {
    case "7d":
      return { from: addDays(t, -6), to: t };
    case "30d":
      return { from: addDays(t, -29), to: t };
    case "month":
      return { from: monthStart(t), to: t };
    case "today":
    default:
      return { from: t, to: t };
  }
}

function Kpi({ label, value, suffix }) {
  return (
    <Paper elevation={0} sx={{ p: 2, height: "100%", border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
      <Typography variant="overline" color="text.secondary" sx={{ letterSpacing: 0.6 }}>{label}</Typography>
      <Typography variant="h4" color="primary" sx={{ fontVariantNumeric: "tabular-nums", fontWeight: 600, mt: 0.5 }}>
        {value}
        {suffix && (
          <Typography component="span" variant="h6" color="text.secondary" sx={{ ml: 0.5 }}>
            {suffix}
          </Typography>
        )}
      </Typography>
    </Paper>
  );
}

function ChartCard({ title, children, empty, height = 280, footer }) {
  return (
    <Paper elevation={0} sx={{ p: 2, height: "100%", border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
      <Typography variant="h6" gutterBottom sx={{ fontWeight: 600, fontSize: "1.05rem" }}>{title}</Typography>
      {empty ? (
        <Typography color="text.secondary" sx={{ py: 6, textAlign: "center" }}>—</Typography>
      ) : (
        <>
          <Box sx={{ width: "100%", height, mt: 1 }}>{children}</Box>
          {footer}
        </>
      )}
    </Paper>
  );
}

function moneyTip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <Paper sx={{ p: 1.25, border: "1px solid", borderColor: "divider" }}>
      <Typography variant="caption" color="text.secondary" display="block">{label}</Typography>
      {payload.map((p) => (
        <Typography key={p.dataKey} variant="body2" sx={{ fontVariantNumeric: "tabular-nums", color: p.color }}>
          {p.name}: {Number(p.value).toLocaleString(undefined, { maximumFractionDigits: 3 })} DT
        </Typography>
      ))}
    </Paper>
  );
}

function payLabel(method, t) {
  const map = { CASH: t("cash"), CARD: t("card"), ACCOUNT: t("account"), VOUCHER: t("voucher") };
  return map[method] || method;
}

function typeLabel(ptype, t) {
  if (ptype === "book") return t("books");
  if (ptype === "furniture") return t("furniture");
  return ptype;
}

export default function Dashboard({ onBack, onLogout, onFinancials, onConsolidated }) {
  const { t } = useTranslation();
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("sm"));
  const initial = useMemo(() => loadPrefs(), []);
  const initialRange = rangeForPreset(initial.preset === "custom" ? "today" : initial.preset);

  const [prefs, setPrefs] = useState(initial);
  const [from, setFrom] = useState(initialRange.from);
  const [to, setTo] = useState(initialRange.to);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const updatePrefs = useCallback((patch) => {
    setPrefs((prev) => {
      const next = { ...prev, ...patch, charts: { ...prev.charts, ...(patch.charts || {}) } };
      savePrefs(next);
      return next;
    });
  }, []);

  const applyPreset = (preset) => {
    const r = rangeForPreset(preset);
    setFrom(r.from);
    setTo(r.to);
    updatePrefs({ preset });
  };

  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const filters = {};
      if (prefs.payment) filters.payment = prefs.payment;
      if (prefs.product_type) filters.product_type = prefs.product_type;
      if (prefs.terminal) filters.terminal = prefs.terminal;
      if (prefs.store) filters.store = prefs.store;
      setData(await salesReport(from, to, filters));
    } catch (e) {
      setData(null);
      setError(String(e.message || e).startsWith("403") ? t("noAccess") : String(e.message || e));
    } finally {
      setLoading(false);
    }
  }, [from, to, prefs.payment, prefs.product_type, prefs.terminal, prefs.store, t]);

  useEffect(() => {
    load();
  }, [load]);

  const seriesData = useMemo(
    () => (data?.series || []).map((s) => ({
      ...s,
      label: s.date,
      revenue: Number(s.revenue),
    })),
    [data],
  );

  const productsData = useMemo(
    () => (data?.best_sellers || []).map((b) => ({
      name: b.name?.length > 22 ? `${b.name.slice(0, 20)}…` : b.name,
      fullName: b.name,
      qty: Number(b.qty),
      revenue: Number(b.revenue),
    })),
    [data],
  );

  const paymentData = useMemo(
    () => (data?.payment_mix || []).map((p) => ({
      name: payLabel(p.method, t),
      value: Number(p.amount),
      method: p.method,
    })),
    [data, t],
  );

  const vatData = useMemo(() => {
    const rows = (data?.tva_by_rate || []).map((r) => ({
      name: `TVA ${Number(r.rate)}%`,
      value: Number(r.tax || 0) || Number(r.base),
      base: Number(r.base),
      tax: Number(r.tax || 0),
    }));
    if (data && Number(data.timbre_total) > 0) {
      rows.push({ name: t("timbre"), value: Number(data.timbre_total), base: 0, tax: Number(data.timbre_total) });
    }
    return rows;
  }, [data, t]);

  const categoryData = useMemo(
    () => (data?.category_mix || []).map((c) => ({
      name: typeLabel(c.product_type, t),
      value: Number(c.revenue),
      qty: Number(c.qty),
    })),
    [data, t],
  );

  const marginData = useMemo(
    () => (data?.margin_trend || []).map((m) => ({
      label: m.date,
      gross_profit: Number(m.gross_profit),
      margin_pct: Number(m.margin_pct),
    })),
    [data],
  );

  const hourlyData = useMemo(
    () => (data?.hourly || []).map((h) => ({
      hour: h.hour,
      revenue: Number(h.revenue),
      sales_count: h.sales_count,
    })),
    [data],
  );

  const opts = data?.filter_options || { payment_methods: [], product_types: [], terminals: [], stores: [] };
  const charts = prefs.charts;
  const chartH = isMobile ? 240 : 280;

  const onDateChange = (which, value) => {
    if (which === "from") setFrom(value);
    else setTo(value);
    updatePrefs({ preset: "custom" });
  };

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f3f5f3" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 1, flexWrap: "wrap" }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("dashboard")}</Typography>
          <Button color="inherit" onClick={onFinancials}>{t("accounting")}</Button>
          <Button color="inherit" onClick={onConsolidated}>{t("consolidated")}</Button>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: { xs: 1.5, md: 2.5 }, maxWidth: 1400, mx: "auto" }}>
        <Paper elevation={0} sx={{ p: 2, mb: 2, border: "1px solid", borderColor: "divider", borderRadius: 2 }}>
          <Stack spacing={1.5}>
            <ToggleButtonGroup
              exclusive
              size="small"
              value={prefs.preset === "custom" ? null : prefs.preset}
              onChange={(_, v) => v && applyPreset(v)}
              sx={{
                flexWrap: "wrap",
                "& .MuiToggleButton-root": {
                  textTransform: "none",
                  px: 1.5,
                  borderColor: "divider",
                  "&.Mui-selected": { bgcolor: "primary.main", color: "#fff", "&:hover": { bgcolor: "primary.dark" } },
                },
              }}
            >
              <ToggleButton value="today">{t("presetToday")}</ToggleButton>
              <ToggleButton value="7d">{t("preset7d")}</ToggleButton>
              <ToggleButton value="30d">{t("preset30d")}</ToggleButton>
              <ToggleButton value="month">{t("presetMonth")}</ToggleButton>
            </ToggleButtonGroup>

            <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} alignItems={{ md: "center" }} flexWrap="wrap">
              <TextField type="date" size="small" label={t("from")} value={from}
                         onChange={(e) => onDateChange("from", e.target.value)} InputLabelProps={{ shrink: true }} />
              <TextField type="date" size="small" label={t("to")} value={to}
                         onChange={(e) => onDateChange("to", e.target.value)} InputLabelProps={{ shrink: true }} />

              <FormControl size="small" sx={{ minWidth: 140 }}>
                <InputLabel>{t("filterPayment")}</InputLabel>
                <Select label={t("filterPayment")} value={prefs.payment}
                        onChange={(e) => updatePrefs({ payment: e.target.value })}>
                  <MenuItem value="">{t("filterAll")}</MenuItem>
                  {(opts.payment_methods.length ? opts.payment_methods : ["CASH", "CARD", "ACCOUNT", "VOUCHER"]).map((m) => (
                    <MenuItem key={m} value={m}>{payLabel(m, t)}</MenuItem>
                  ))}
                </Select>
              </FormControl>

              <FormControl size="small" sx={{ minWidth: 160 }}>
                <InputLabel>{t("filterProductType")}</InputLabel>
                <Select label={t("filterProductType")} value={prefs.product_type}
                        onChange={(e) => updatePrefs({ product_type: e.target.value })}>
                  <MenuItem value="">{t("filterAll")}</MenuItem>
                  <MenuItem value="book">{t("books")}</MenuItem>
                  <MenuItem value="furniture">{t("furniture")}</MenuItem>
                </Select>
              </FormControl>

              {opts.terminals?.length > 1 && (
                <FormControl size="small" sx={{ minWidth: 120 }}>
                  <InputLabel>{t("filterTerminal")}</InputLabel>
                  <Select label={t("filterTerminal")} value={prefs.terminal}
                          onChange={(e) => updatePrefs({ terminal: e.target.value })}>
                    <MenuItem value="">{t("filterAll")}</MenuItem>
                    {opts.terminals.map((term) => (
                      <MenuItem key={term} value={term}>{term}</MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}

              {opts.stores?.length > 0 && (
                <FormControl size="small" sx={{ minWidth: 120 }}>
                  <InputLabel>{t("filterStore")}</InputLabel>
                  <Select label={t("filterStore")} value={prefs.store}
                          onChange={(e) => updatePrefs({ store: e.target.value })}>
                    <MenuItem value="">{t("filterAll")}</MenuItem>
                    {opts.stores.map((s) => (
                      <MenuItem key={s || "_"} value={s}>{s || t("filterStoreLocal")}</MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}

              <Button variant="contained" onClick={load} disabled={loading} sx={{ alignSelf: { xs: "stretch", md: "center" } }}>
                {t("refresh")}
              </Button>
            </Stack>

            <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
              <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>{t("chartVisibility")}</Typography>
              {[
                ["series", t("chartSalesOverTime")],
                ["products", t("bestSellers")],
                ["payments", t("paymentMix")],
                ["vat", t("tvaBreakdown")],
                ["category", t("chartCategoryMix")],
                ["margin", t("chartMarginTrend")],
                ["hourly", t("chartHourly")],
              ].map(([key, label]) => (
                <FormControlLabel
                  key={key}
                  sx={{ m: 0, "& .MuiFormControlLabel-label": { fontSize: "0.8rem" } }}
                  control={
                    <Switch size="small" checked={!!charts[key]}
                            onChange={(e) => updatePrefs({ charts: { [key]: e.target.checked } })} />
                  }
                  label={label}
                />
              ))}
            </Stack>
          </Stack>
        </Paper>

        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

        {data && (
          <>
            <Grid container spacing={2} sx={{ mb: 2 }}>
              <Grid item xs={6} md={3}><Kpi label={t("revenue")} value={data.revenue_inc_tax} suffix="DT" /></Grid>
              <Grid item xs={6} md={3}><Kpi label={t("profit")} value={data.gross_profit} suffix="DT" /></Grid>
              <Grid item xs={6} md={3}><Kpi label={t("margin")} value={data.margin_pct} suffix="%" /></Grid>
              <Grid item xs={6} md={3}><Kpi label={t("salesCount")} value={data.sales_count} /></Grid>
            </Grid>

            <Grid container spacing={2}>
              {charts.series && (
                <Grid item xs={12}>
                  <ChartCard title={`${t("chartSalesOverTime")} (${data.bucket === "week" ? t("bucketWeek") : t("bucketDay")})`}
                             empty={!seriesData.length} height={isMobile ? 220 : 300}>
                    <ResponsiveContainer>
                      <AreaChart data={seriesData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                        <defs>
                          <linearGradient id="caFill" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="#1B5E20" stopOpacity={0.35} />
                            <stop offset="100%" stopColor="#1B5E20" stopOpacity={0.02} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
                        <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                        <YAxis tick={{ fontSize: 11 }} width={56} />
                        <Tooltip content={moneyTip} />
                        <Area type="monotone" dataKey="revenue" name={t("revenue")}
                              stroke="#1B5E20" fill="url(#caFill)" strokeWidth={2.5} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </ChartCard>
                </Grid>
              )}

              {charts.products && (
                <Grid item xs={12} md={7}>
                  <ChartCard title={t("bestSellers")} empty={!productsData.length} height={chartH}>
                    <ResponsiveContainer>
                      <BarChart data={productsData} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e8e8e8" horizontal={false} />
                        <XAxis type="number" tick={{ fontSize: 11 }} />
                        <YAxis type="category" dataKey="name" width={isMobile ? 80 : 120} tick={{ fontSize: 11 }} />
                        <Tooltip
                          formatter={(v, name) => [`${v}${name === "revenue" ? " DT" : ""}`, name === "revenue" ? t("revenue") : t("qty")]}
                          labelFormatter={(_, payload) => payload?.[0]?.payload?.fullName || ""}
                        />
                        <Bar dataKey="qty" name={t("qty")} fill="#1B5E20" radius={[0, 6, 6, 0]} barSize={16} />
                      </BarChart>
                    </ResponsiveContainer>
                  </ChartCard>
                </Grid>
              )}

              {charts.payments && (
                <Grid item xs={12} md={5}>
                  <ChartCard
                    title={t("paymentMix")}
                    empty={!paymentData.length}
                    height={Math.max(200, chartH - 40)}
                    footer={(
                      <Stack spacing={0.5} sx={{ mt: 1 }}>
                        {paymentData.map((p) => (
                          <Stack key={p.method} direction="row" justifyContent="space-between">
                            <Typography variant="body2">{p.name}</Typography>
                            <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
                              {p.value.toLocaleString(undefined, { maximumFractionDigits: 3 })} DT
                            </Typography>
                          </Stack>
                        ))}
                      </Stack>
                    )}
                  >
                    <ResponsiveContainer>
                      <PieChart>
                        <Pie data={paymentData} dataKey="value" nameKey="name" innerRadius="52%" outerRadius="78%"
                             paddingAngle={2} stroke="#fff" strokeWidth={2}>
                          {paymentData.map((_, i) => (
                            <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(v) => [`${Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 })} DT`, t("total")]} />
                        <Legend />
                      </PieChart>
                    </ResponsiveContainer>
                  </ChartCard>
                </Grid>
              )}

              {charts.vat && (
                <Grid item xs={12} md={4}>
                  <ChartCard title={t("tvaBreakdown")} empty={!vatData.length} height={chartH}>
                    <ResponsiveContainer>
                      <PieChart>
                        <Pie data={vatData} dataKey="value" nameKey="name" cx="50%" cy="45%"
                             outerRadius="70%" paddingAngle={1}>
                          {vatData.map((_, i) => (
                            <Cell key={i} fill={CHART_COLORS[(i + 2) % CHART_COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(v) => [`${Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 })} DT`]} />
                        <Legend />
                      </PieChart>
                    </ResponsiveContainer>
                  </ChartCard>
                </Grid>
              )}

              {charts.category && (
                <Grid item xs={12} md={4}>
                  <ChartCard title={t("chartCategoryMix")} empty={!categoryData.length} height={chartH}>
                    <ResponsiveContainer>
                      <PieChart>
                        <Pie data={categoryData} dataKey="value" nameKey="name" innerRadius="48%" outerRadius="72%">
                          {categoryData.map((_, i) => (
                            <Cell key={i} fill={i === 0 ? "#1B5E20" : "#8D6E63"} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(v) => [`${Number(v).toLocaleString(undefined, { maximumFractionDigits: 3 })} DT`, t("revenue")]} />
                        <Legend />
                      </PieChart>
                    </ResponsiveContainer>
                  </ChartCard>
                </Grid>
              )}

              {charts.margin && marginData.length > 0 && (
                <Grid item xs={12} md={4}>
                  <ChartCard title={t("chartMarginTrend")} height={chartH}>
                    <ResponsiveContainer>
                      <LineChart data={marginData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e0e0e0" />
                        <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                        <YAxis yAxisId="gp" tick={{ fontSize: 10 }} width={48} />
                        <YAxis yAxisId="pct" orientation="right" tick={{ fontSize: 10 }} width={36} unit="%" />
                        <Tooltip />
                        <Line yAxisId="gp" type="monotone" dataKey="gross_profit" name={t("profit")}
                              stroke="#2E7D32" strokeWidth={2} dot={false} />
                        <Line yAxisId="pct" type="monotone" dataKey="margin_pct" name={t("margin")}
                              stroke="#F9A825" strokeWidth={2} dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </ChartCard>
                </Grid>
              )}

              {charts.hourly && hourlyData.length > 0 && (
                <Grid item xs={12}>
                  <ChartCard title={t("chartHourly")} height={isMobile ? 200 : 240}>
                    <ResponsiveContainer>
                      <BarChart data={hourlyData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#e8e8e8" />
                        <XAxis dataKey="hour" tick={{ fontSize: 11 }} />
                        <YAxis tick={{ fontSize: 11 }} width={48} />
                        <Tooltip content={moneyTip} />
                        <Bar dataKey="revenue" name={t("revenue")} fill="#558B2F" radius={[6, 6, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </ChartCard>
                </Grid>
              )}
            </Grid>

            {(prefs.payment || prefs.product_type || prefs.terminal || prefs.store) && (
              <Stack direction="row" spacing={1} sx={{ mt: 2 }} flexWrap="wrap" useFlexGap>
                <Chip size="small" label={t("filtersActive")} color="primary" variant="outlined" />
                {prefs.payment && <Chip size="small" label={payLabel(prefs.payment, t)} onDelete={() => updatePrefs({ payment: "" })} />}
                {prefs.product_type && <Chip size="small" label={typeLabel(prefs.product_type, t)} onDelete={() => updatePrefs({ product_type: "" })} />}
                {prefs.terminal && <Chip size="small" label={prefs.terminal} onDelete={() => updatePrefs({ terminal: "" })} />}
                {prefs.store && <Chip size="small" label={prefs.store} onDelete={() => updatePrefs({ store: "" })} />}
              </Stack>
            )}
          </>
        )}
      </Box>
    </Box>
  );
}
