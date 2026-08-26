import { useEffect, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Paper, Stack, Alert, Chip, TextField, MenuItem,
  Select, Table, TableHead, TableRow, TableCell, TableBody, TableContainer, Avatar, Pagination,
  Dialog, DialogTitle, DialogContent, Divider, Grid, CircularProgress,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { catalogSearch, productDetail, listCategories } from "../api";
import ImageViewer from "../components/ImageViewer.jsx";
import LangToggle from "../LangToggle.jsx";
import { fmtQty } from "../format.js";

function StockChip({ row, t }) {
  if (row.out_of_stock) return <Chip size="small" color="error" label={t("outOfStock")} />;
  if (row.low_stock) return <Chip size="small" color="warning" label={`${t("lowStock")} · ${fmtQty(row.qty_on_hand)}`} />;
  return <Chip size="small" color="success" variant="outlined" label={fmtQty(row.qty_on_hand)} />;
}

export default function Catalog({ onBack, onLogout, onNewProduct }) {
  const { t } = useTranslation();
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [type, setType] = useState("");
  const [stock, setStock] = useState("");
  const [ordering, setOrdering] = useState("name");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [cats, setCats] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState(null);
  const [viewer, setViewer] = useState(null);

  useEffect(() => { listCategories().then(setCats).catch(() => {}); }, []);
  useEffect(() => { setPage(1); }, [q, category, type, stock, ordering]);

  useEffect(() => {
    const timer = setTimeout(async () => {
      setLoading(true); setError("");
      try {
        setData(await catalogSearch({ q, category, type, stock, ordering, page, page_size: 20 }));
      } catch (e) { setError(String(e.message || e)); } finally { setLoading(false); }
    }, 250);   // debounce → instant search while typing
    return () => clearTimeout(timer);
  }, [q, category, type, stock, ordering, page]);

  const openDetail = async (id) => {
    setDetail({ loading: true });
    try { setDetail(await productDetail(id)); } catch (e) { setError(String(e.message || e)); setDetail(null); }
  };

  const results = data?.results || [];
  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("catalog")}</Typography>
          {onNewProduct && <Button color="inherit" onClick={onNewProduct}>＋ {t("newArticle")}</Button>}
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      <Box sx={{ p: 2, maxWidth: 1040, mx: "auto" }}>
        {error && <Alert severity="warning" sx={{ mb: 2 }}>{error}</Alert>}

        <Paper sx={{ p: 2, mb: 2 }}>
          <TextField fullWidth size="small" autoFocus value={q} onChange={(e) => setQ(e.target.value)}
                     placeholder={t("catalogSearchPlaceholder")} sx={{ mb: 1.5 }} />
          <Stack direction="row" spacing={1.5} flexWrap="wrap" useFlexGap>
            <Select size="small" value={category} onChange={(e) => setCategory(e.target.value)} displayEmpty sx={{ minWidth: 150 }}>
              <MenuItem value="">{t("allCategories")}</MenuItem>
              {cats.map((c) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
            </Select>
            <Select size="small" value={type} onChange={(e) => setType(e.target.value)} displayEmpty sx={{ minWidth: 130 }}>
              <MenuItem value="">{t("allTypes")}</MenuItem>
              <MenuItem value="book">{t("books")}</MenuItem>
              <MenuItem value="supply">{t("supplies")}</MenuItem>
            </Select>
            <Select size="small" value={stock} onChange={(e) => setStock(e.target.value)} displayEmpty sx={{ minWidth: 140 }}>
              <MenuItem value="">{t("allStock")}</MenuItem>
              <MenuItem value="in">{t("inStock")}</MenuItem>
              <MenuItem value="low">{t("lowStock")}</MenuItem>
              <MenuItem value="out">{t("outOfStock")}</MenuItem>
            </Select>
            <Select size="small" value={ordering} onChange={(e) => setOrdering(e.target.value)} sx={{ minWidth: 150 }}>
              <MenuItem value="name">{t("sortName")}</MenuItem>
              <MenuItem value="price">{t("sortPriceAsc")}</MenuItem>
              <MenuItem value="-price">{t("sortPriceDesc")}</MenuItem>
              <MenuItem value="-stock">{t("sortStock")}</MenuItem>
            </Select>
          </Stack>
        </Paper>

        <Paper>
          <TableContainer>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell />
                  <TableCell>{t("productName")}</TableCell>
                  <TableCell>{t("category")}</TableCell>
                  <TableCell align="right">{t("price")}</TableCell>
                  <TableCell align="center">{t("stock")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {results.map((r) => (
                  <TableRow key={r.id} hover sx={{ cursor: "pointer" }} onClick={() => openDetail(r.id)}>
                    <TableCell sx={{ width: 52 }}>
                      <Avatar variant="rounded" src={r.image || undefined} sx={{ width: 40, height: 40, bgcolor: "#e0e0e0", fontSize: 12 }}>
                        {r.is_book ? "📖" : "▦"}
                      </Avatar>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" fontWeight={600}>{r.name_fr}</Typography>
                      <Typography variant="caption" color="text.secondary">{r.sku}</Typography>
                    </TableCell>
                    <TableCell>{r.category}</TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>{r.sale_price}</TableCell>
                    <TableCell align="center"><StockChip row={r} t={t} /></TableCell>
                  </TableRow>
                ))}
                {!loading && results.length === 0 && (
                  <TableRow><TableCell colSpan={5} align="center" sx={{ py: 4, color: "text.secondary" }}>{t("noResults")}</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
          <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ p: 1.5 }}>
            <Typography variant="caption" color="text.secondary">
              {loading ? <CircularProgress size={14} /> : `${data?.count ?? 0} ${t("items")}`}
            </Typography>
            {data && data.num_pages > 1 && (
              <Pagination size="small" count={data.num_pages} page={page} onChange={(_, p) => setPage(p)} />
            )}
          </Stack>
        </Paper>
      </Box>

      {/* Detail dialog */}
      <Dialog open={Boolean(detail)} onClose={() => setDetail(null)} maxWidth="sm" fullWidth>
        {detail && detail.loading && <DialogContent sx={{ textAlign: "center", py: 5 }}><CircularProgress /></DialogContent>}
        {detail && !detail.loading && (
          <>
            <DialogTitle>{detail.name_fr}{detail.name_ar ? `  ·  ${detail.name_ar}` : ""}</DialogTitle>
            <DialogContent dividers>
              <Grid container spacing={2}>
                {detail.images?.length > 0 && (
                  <Grid item xs={4}>
                    <img src={detail.images[0].url} alt={detail.name_fr}
                         onClick={() => setViewer(detail.images[0].url)}
                         style={{ width: "100%", borderRadius: 8, cursor: "zoom-in", objectFit: "cover" }} />
                    {detail.images.length > 1 && (
                      <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
                        {detail.images.slice(1, 4).map((im) => (
                          <img key={im.id} src={im.url} alt="" onClick={() => setViewer(im.url)}
                               style={{ width: 34, height: 34, objectFit: "cover", borderRadius: 4, cursor: "zoom-in" }} />
                        ))}
                      </Stack>
                    )}
                  </Grid>
                )}
                <Grid item xs={detail.images?.length > 0 ? 8 : 12}>
                  <Info label={t("sku")} value={detail.sku} />
                  <Info label={t("category")} value={detail.category} />
                  <Info label={t("price")} value={`${detail.sale_price} · TVA ${detail.tax_rate_percent}%`} />
                  <Info label={t("stock")} value={`${fmtQty(detail.qty_on_hand)} (${t("lowStock")} ≤ ${fmtQty(detail.reorder_point)})`} />
                  {detail.barcodes?.length > 0 && (
                    <Info label={t("barcodes")} value={detail.barcodes.map((b) => b.value).join(", ")} />
                  )}
                </Grid>
              </Grid>

              {detail.book && (
                <>
                  <Divider sx={{ my: 2 }}><Chip size="small" label={t("bookInfo")} /></Divider>
                  <Info label={t("isbn")} value={detail.book.isbn13} />
                  {detail.book.subtitle && <Info label={t("subtitle")} value={detail.book.subtitle} />}
                  <Info label={t("authors")} value={detail.book.contributors?.filter((c) => c.role === "AUTHOR").map((c) => c.name).join(", ")} />
                  {detail.book.contributors?.some((c) => c.role === "TRANSLATOR") &&
                    <Info label={t("translators")} value={detail.book.contributors.filter((c) => c.role === "TRANSLATOR").map((c) => c.name).join(", ")} />}
                  <Info label={t("publisher")} value={detail.book.publisher} />
                  <Info label={t("year")} value={detail.book.pub_year || ""} />
                  <Info label={t("pages")} value={detail.book.pages || ""} />
                  <Info label={t("languages")} value={(detail.book.languages || []).join(", ")} />
                  {detail.book.description && (
                    <Typography variant="body2" sx={{ mt: 1, color: "text.secondary" }}>{detail.book.description}</Typography>
                  )}
                </>
              )}
            </DialogContent>
          </>
        )}
      </Dialog>

      <ImageViewer src={viewer} open={Boolean(viewer)} onClose={() => setViewer(null)} alt="" />
    </Box>
  );
}

function Info({ label, value }) {
  if (value === "" || value == null) return null;
  return (
    <Stack direction="row" spacing={1} sx={{ py: 0.3 }}>
      <Typography variant="body2" sx={{ minWidth: 110, color: "text.secondary" }}>{label}</Typography>
      <Typography variant="body2" sx={{ fontWeight: 500 }}>{value}</Typography>
    </Stack>
  );
}
