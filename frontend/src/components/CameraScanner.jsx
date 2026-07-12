import { useEffect, useRef, useState } from "react";
import { Box, Button, Stack, Alert } from "@mui/material";
import { useTranslation } from "react-i18next";

/* Camera barcode scanner (native BarcodeDetector). Renders a live preview and calls
   onDetect(rawValue) once per distinct code (2 s duplicate guard so one article isn't added
   twice from consecutive frames). Degrades with a clear message when no camera/detector. */
export default function CameraScanner({ onDetect, onClose }) {
  const { t } = useTranslation();
  const videoRef = useRef(null);
  const lastRef = useRef({ value: "", at: 0 });
  const [error, setError] = useState("");

  useEffect(() => {
    let stream, raf, stopped = false;
    (async () => {
      if (!("BarcodeDetector" in window)) { setError(t("scannerUnsupported")); return; }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
      } catch { setError(t("cameraUnavailable")); return; }
      if (stopped) { stream.getTracks().forEach((tk) => tk.stop()); return; }
      videoRef.current.srcObject = stream;
      const detector = new window.BarcodeDetector();
      const tick = async () => {
        if (stopped || !videoRef.current) return;
        try {
          const codes = await detector.detect(videoRef.current);
          if (codes.length) {
            const value = codes[0].rawValue;
            const now = Date.now();
            if (value !== lastRef.current.value || now - lastRef.current.at > 2000) {
              lastRef.current = { value, at: now };
              onDetect(value);
            }
          }
        } catch { /* frame not ready */ }
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    })();
    return () => {
      stopped = true;
      if (raf) cancelAnimationFrame(raf);
      if (stream) stream.getTracks().forEach((tk) => tk.stop());
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Box sx={{ mb: 1 }}>
      {error
        ? <Alert severity="warning" onClose={onClose}>{error}</Alert>
        : (
          <Stack spacing={1}>
            <video ref={videoRef} autoPlay playsInline muted
                   style={{ width: "100%", borderRadius: 8, background: "#000", maxHeight: 220, objectFit: "cover" }} />
            <Button size="small" color="error" variant="outlined" onClick={onClose}>■ {t("stopCamera")}</Button>
          </Stack>
        )}
    </Box>
  );
}
