import { useState } from "react";
import {
  Box, Button, Card, CardContent, Stack, TextField, Typography, Alert,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { login } from "../api";
import LangToggle from "../LangToggle.jsx";

export default function Login({ onLogin }) {
  const { t } = useTranslation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(username, password);
      onLogin();
    } catch {
      setError("Identifiants invalides");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box sx={{ minHeight: "100vh", display: "grid", placeItems: "center", bgcolor: "#f5f5f5" }}>
      <Card sx={{ width: 360 }}>
        <CardContent component="form" onSubmit={submit}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={2}>
            <Typography variant="h5" color="primary" fontWeight={700}>
              {t("appName")}
            </Typography>
            <LangToggle />
          </Stack>
          <Stack spacing={2}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField
              label={t("username")} value={username}
              onChange={(e) => setUsername(e.target.value)} autoFocus fullWidth
            />
            <TextField
              label={t("password")} type="password" value={password}
              onChange={(e) => setPassword(e.target.value)} fullWidth
            />
            <Button type="submit" variant="contained" disabled={busy} size="large">
              {t("signIn")}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
