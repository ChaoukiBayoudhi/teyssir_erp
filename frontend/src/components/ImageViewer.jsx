import { useEffect, useState } from "react";
import { Dialog, IconButton, Box, Stack } from "@mui/material";

// Fullscreen image viewer: zoom (buttons + wheel), rotate, keyboard (+/-/r/Esc). No dependencies.
export default function ImageViewer({ src, alt = "", open, onClose }) {
  const [scale, setScale] = useState(1);
  const [rot, setRot] = useState(0);

  useEffect(() => { if (open) { setScale(1); setRot(0); } }, [open, src]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "+" || e.key === "=") setScale((s) => Math.min(6, s + 0.25));
      else if (e.key === "-") setScale((s) => Math.max(0.25, s - 0.25));
      else if (e.key.toLowerCase() === "r") setRot((r) => (r + 90) % 360);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!src) return null;
  const zoom = (d) => setScale((s) => Math.min(6, Math.max(0.25, s + d)));

  return (
    <Dialog open={open} onClose={onClose} maxWidth={false} fullScreen
            PaperProps={{ sx: { bgcolor: "rgba(0,0,0,0.92)" } }}>
      <Stack direction="row" spacing={1} sx={{ position: "fixed", top: 12, right: 12, zIndex: 2 }}>
        <IconButton onClick={() => zoom(0.25)} sx={{ color: "#fff", bgcolor: "rgba(255,255,255,.12)" }} aria-label="zoom in">＋</IconButton>
        <IconButton onClick={() => zoom(-0.25)} sx={{ color: "#fff", bgcolor: "rgba(255,255,255,.12)" }} aria-label="zoom out">－</IconButton>
        <IconButton onClick={() => setRot((r) => (r + 90) % 360)} sx={{ color: "#fff", bgcolor: "rgba(255,255,255,.12)" }} aria-label="rotate">⟳</IconButton>
        <IconButton onClick={onClose} sx={{ color: "#fff", bgcolor: "rgba(255,255,255,.12)" }} aria-label="close">✕</IconButton>
      </Stack>
      <Box onClick={onClose} onWheel={(e) => zoom(e.deltaY < 0 ? 0.2 : -0.2)}
           sx={{ width: "100vw", height: "100vh", display: "flex", alignItems: "center",
                 justifyContent: "center", overflow: "hidden", cursor: "zoom-out" }}>
        <img src={src} alt={alt} onClick={(e) => e.stopPropagation()}
             style={{ maxWidth: "92vw", maxHeight: "92vh", objectFit: "contain",
                      transform: `scale(${scale}) rotate(${rot}deg)`, transition: "transform .12s",
                      cursor: "default" }} />
      </Box>
    </Dialog>
  );
}
