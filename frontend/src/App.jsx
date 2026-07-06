import { useEffect, useMemo, useState } from "react";
import { CacheProvider } from "@emotion/react";
import { ThemeProvider, CssBaseline } from "@mui/material";
import { useTranslation } from "react-i18next";
import { makeTheme, ltrCache, rtlCache } from "./theme";
import { getToken, clearToken } from "./api";
import Login from "./pages/Login.jsx";
import Pos from "./pages/Pos.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import StockTake from "./pages/StockTake.jsx";
import CashSession from "./pages/CashSession.jsx";
import Receiving from "./pages/Receiving.jsx";
import Customers from "./pages/Customers.jsx";
import Financials from "./pages/Financials.jsx";
import Consolidated from "./pages/Consolidated.jsx";
import BookCreate from "./pages/BookCreate.jsx";
import Quotation from "./pages/Quotation.jsx";
import PurchaseOrders from "./pages/PurchaseOrders.jsx";
import Catalog from "./pages/Catalog.jsx";

export default function App() {
  const { i18n } = useTranslation();
  const [authed, setAuthed] = useState(Boolean(getToken()));
  const [view, setView] = useState("pos");
  const dir = i18n.language === "ar" ? "rtl" : "ltr";

  useEffect(() => {
    document.documentElement.dir = dir;
    document.documentElement.lang = i18n.language;
  }, [dir, i18n.language]);

  const theme = useMemo(() => makeTheme(dir), [dir]);
  const cache = dir === "rtl" ? rtlCache : ltrCache;

  const logout = () => {
    clearToken();
    setAuthed(false);
  };

  return (
    <CacheProvider value={cache}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        {!authed ? (
          <Login onLogin={() => setAuthed(true)} />
        ) : view === "dashboard" ? (
          <Dashboard onBack={() => setView("pos")} onLogout={logout}
                     onFinancials={() => setView("financials")}
                     onConsolidated={() => setView("consolidated")} />
        ) : view === "financials" ? (
          <Financials onBack={() => setView("dashboard")} onLogout={logout} />
        ) : view === "consolidated" ? (
          <Consolidated onBack={() => setView("dashboard")} onLogout={logout} />
        ) : view === "stocktake" ? (
          <StockTake onBack={() => setView("pos")} onLogout={logout} />
        ) : view === "cash" ? (
          <CashSession onBack={() => setView("pos")} onLogout={logout} />
        ) : view === "receiving" ? (
          <Receiving onBack={() => setView("pos")} onLogout={logout} />
        ) : view === "customers" ? (
          <Customers onBack={() => setView("pos")} onLogout={logout} />
        ) : view === "book" ? (
          <BookCreate onBack={() => setView("pos")} onLogout={logout} />
        ) : view === "quotation" ? (
          <Quotation onBack={() => setView("pos")} onLogout={logout} />
        ) : view === "po" ? (
          <PurchaseOrders onBack={() => setView("pos")} onLogout={logout} />
        ) : view === "catalog" ? (
          <Catalog onBack={() => setView("pos")} onLogout={logout} />
        ) : (
          <Pos
            onLogout={logout}
            onDashboard={() => setView("dashboard")}
            onStockTake={() => setView("stocktake")}
            onCash={() => setView("cash")}
            onReceiving={() => setView("receiving")}
            onCustomers={() => setView("customers")}
            onNewBook={() => setView("book")}
            onQuotation={() => setView("quotation")}
            onPurchaseOrders={() => setView("po")}
            onCatalog={() => setView("catalog")}
          />
        )}
      </ThemeProvider>
    </CacheProvider>
  );
}
