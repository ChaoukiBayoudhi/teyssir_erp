import { useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Grid, Paper, TextField, List, ListItemButton,
  ListItemText, Stack, Divider, Alert, Table, TableHead, TableRow, TableCell, TableBody, Chip,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { listCustomers, createCustomer, customerStatement, customerPayment } from "../api";
import LangToggle from "../LangToggle.jsx";

export default function Customers({ onBack, onLogout }) {
  const { t } = useTranslation();
  const [customers, setCustomers] = useState([]);
  const [selected, setSelected] = useState(null);
  const [statement, setStatement] = useState(null);
  const [newName, setNewName] = useState("");
  const [newPhone, setNewPhone] = useState("");
  const [payAmount, setPayAmount] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try {
      setCustomers(await listCustomers());
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  useEffect(() => {
    load();
  }, []);

  const open = async (c) => {
    setSelected(c);
    setStatement(null);
    try {
      setStatement(await customerStatement(c.id));
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  const add = async () => {
    if (!newName.trim()) return;
    setError("");
    try {
      const c = await createCustomer({ name: newName.trim(), phone: newPhone.trim() });
      setNewName("");
      setNewPhone("");
      await load();
      open(c);
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  const pay = async () => {
    if (!selected || !payAmount) return;
    setError("");
    const amount = Number(payAmount);
    if (!Number.isFinite(amount) || amount <= 0) {
      setError(t("amountMustBePositive"));
      return;
    }
    const owed = Number(selected.balance);
    if (Number.isFinite(owed) && amount > owed + 1e-9) {
      setError(t("paymentExceedsBalance", { balance: selected.balance }));
      return;
    }
    try {
      await customerPayment(selected.id, String(payAmount));
      setPayAmount("");
      await load();
      const fresh = (await listCustomers()).find((x) => x.id === selected.id);
      setSelected(fresh || selected);
      setStatement(await customerStatement(selected.id));
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("customers")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2 }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}
        <Grid container spacing={2}>
          <Grid item xs={12} md={5}>
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
                <TextField size="small" placeholder={t("newCustomer")} value={newName}
                           onChange={(e) => setNewName(e.target.value)} />
                <TextField size="small" placeholder="Tel" value={newPhone}
                           onChange={(e) => setNewPhone(e.target.value)} sx={{ width: 110 }} />
                <Button variant="outlined" onClick={add}>{t("add")}</Button>
              </Stack>
              <List dense>
                {customers.length === 0 && (
                  <Typography color="text.secondary" sx={{ p: 1 }}>{t("noCustomer")}</Typography>
                )}
                {customers.map((c) => (
                  <ListItemButton key={c.id} selected={selected?.id === c.id} onClick={() => open(c)}>
                    <ListItemText primary={c.name} secondary={c.phone} />
                    <Chip size="small"
                          color={Number(c.balance) > 0 ? "warning" : Number(c.balance) < 0 ? "info" : "default"}
                          label={`${Number(c.balance) < 0 ? t("credit") + " " : ""}${Math.abs(Number(c.balance)).toFixed(3)} DT`} />
                  </ListItemButton>
                ))}
              </List>
            </Paper>
          </Grid>

          <Grid item xs={12} md={7}>
            {selected && (
              <Paper sx={{ p: 2 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="baseline">
                  <Typography variant="h6">{selected.name}</Typography>
                  <Typography variant="h6" color={
                    Number(selected.balance) > 0 ? "warning.main"
                      : Number(selected.balance) < 0 ? "info.main" : "text.secondary"
                  }>
                    {Number(selected.balance) < 0
                      ? `${t("credit")}: ${Math.abs(Number(selected.balance)).toFixed(3)} DT`
                      : `${t("balance")}: ${selected.balance} DT`}
                  </Typography>
                </Stack>
                <Divider sx={{ my: 1.5 }} />

                <Typography variant="subtitle2" gutterBottom>{t("statement")}</Typography>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>{t("date")}</TableCell>
                      <TableCell>{t("type")}</TableCell>
                      <TableCell align="right">{t("amount")}</TableCell>
                      <TableCell align="right">{t("balance")}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {statement?.entries.map((e, i) => (
                      <TableRow key={i}>
                        <TableCell>{e.at.slice(0, 10)}</TableCell>
                        <TableCell>{t(e.type)}</TableCell>
                        <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums",
                          color: e.type === "PAYMENT" ? "success.main" : "text.primary" }}>
                          {e.type === "PAYMENT" ? "-" : "+"}{e.amount}
                        </TableCell>
                        <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{e.balance}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>

                <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
                  <TextField size="small" type="number" label={t("amount")} value={payAmount}
                             onChange={(e) => setPayAmount(e.target.value)}
                             inputProps={{ min: 0.001, step: "0.001" }} />
                  <Button variant="contained" onClick={pay}
                          disabled={!payAmount || Number(payAmount) <= 0
                            || Number(selected.balance) <= 0}>
                    {t("recordPayment")}
                  </Button>
                </Stack>
              </Paper>
            )}
          </Grid>
        </Grid>
      </Box>
    </Box>
  );
}
