import { useEffect, useRef, useState } from "react";
import { Box, Button, Stack, Alert, Typography, Chip } from "@mui/material";
import { useTranslation } from "react-i18next";
import { scanBook, pollScanJob } from "../api";

const BARCODE_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "qr_code", "itf"];

/** ISBN-13 check digit (978/979). Do not send invalid EAN as a scan hint. */
function isbn13CheckOk(raw) {
  const s = String(raw || "").replace(/[-\s]/g, "");
  if (!/^97[89]\d{10}$/.test(s)) return false;
  let total = 0;
  for (let i = 0; i < 12; i++) {
    total += (i % 2 === 0 ? 1 : 3) * parseInt(s[i], 10);
  }
  const check = (10 - (total % 10)) % 10;
  return check === parseInt(s[12], 10);
}

async function detectCodeFromSource(source) {
  if (!("BarcodeDetector" in window)) return "";
  try {
    const detector = new window.BarcodeDetector({ formats: BARCODE_FORMATS });
    const codes = await detector.detect(source);
    if (!codes.length) return "";
    // Prefer checksum-valid ISBN-like EAN-13 (978/979)
    const isbn = codes.find((c) => isbn13CheckOk(c.rawValue));
    if (isbn) return isbn.rawValue;
    // Non-ISBN retail barcodes (still useful for POS lookup)
    return (codes[0]).rawValue || "";
  } catch {
    return "";
  }
}

function frameToBlob(video) {
  if (!video?.videoWidth || !video?.videoHeight) return Promise.resolve(null);
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/jpeg", 0.92));
}

/* POS camera: live barcode when BarcodeDetector exists, plus Capturer / Analyser so a
   book cover (no barcode in frame) can still yield ISBN via still-frame detect or OCR. */
export default function CameraScanner({ onDetect, onQuery, onClose }) {
  const { t } = useTranslation();
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const lastRef = useRef({ value: "", at: 0 });
  const [fatal, setFatal] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveOk, setLiveOk] = useState(false);
  const [stream, setStream] = useState(null);
  const [captures, setCaptures] = useState([]); // { file, url }

  const stopTracks = () => {
    const s = streamRef.current;
    if (s) {
      try { s.getTracks().forEach((tk) => { try { tk.stop(); } catch { /* */ } }); } catch { /* */ }
    }
    streamRef.current = null;
    setStream(null);
    if (videoRef.current) {
      try { videoRef.current.srcObject = null; } catch { /* */ }
    }
  };

  const handleClose = () => {
    stopTracks();
    captures.forEach((c) => c.url && URL.revokeObjectURL(c.url));
    onClose?.();
  };

  useEffect(() => {
    let raf;
    let stopped = false;

    (async () => {
      let media;
      try {
        media = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" } },
        });
      } catch {
        setFatal(t("cameraUnavailable"));
        return;
      }
      if (stopped) {
        media.getTracks().forEach((tk) => tk.stop());
        return;
      }
      streamRef.current = media;
      setStream(media);

      if (!("BarcodeDetector" in window)) {
        setInfo(t("scannerCaptureHint"));
        return;
      }
      setLiveOk(true);
      setInfo(t("scannerAimBarcode"));
      const tick = async () => {
        if (stopped || !videoRef.current) return;
        try {
          const value = await detectCodeFromSource(videoRef.current);
          if (value) {
            const now = Date.now();
            if (value !== lastRef.current.value || now - lastRef.current.at > 2000) {
              lastRef.current = { value, at: now };
              onDetect?.(value);
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
      stopTracks();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Attach stream after <video> mounts (getUserMedia can resolve before paint).
  useEffect(() => {
    if (videoRef.current && stream) {
      videoRef.current.srcObject = stream;
      videoRef.current.play?.().catch(() => {});
    }
  }, [stream]);

  const capture = async () => {
    setError("");
    const blob = await frameToBlob(videoRef.current);
    if (!blob) {
      setError(t("cameraNotReady"));
      return;
    }
    const file = new File([blob], `scan-${Date.now()}.jpg`, { type: "image/jpeg" });
    const url = URL.createObjectURL(file);
    setCaptures((prev) => {
      prev.forEach((c) => c.url && URL.revokeObjectURL(c.url));
      return [{ file, url }];
    });
    setInfo(t("captureReadyAnalyze"));

    try {
      const code = await detectCodeFromSource(await createImageBitmap(file));
      if (code) {
        lastRef.current = { value: code, at: Date.now() };
        onDetect?.(code);
        setInfo(`${t("codeDetected")}: ${code}`);
      }
    } catch { /* still-frame detect optional */ }
  };

  const analyze = async () => {
    setError("");
    setBusy(true);
    try {
      let file = captures[0]?.file;
      if (!file) {
        const blob = await frameToBlob(videoRef.current);
        if (!blob) {
          setError(t("cameraNotReady"));
          return;
        }
        file = new File([blob], `scan-${Date.now()}.jpg`, { type: "image/jpeg" });
        const url = URL.createObjectURL(file);
        setCaptures((prev) => {
          prev.forEach((c) => c.url && URL.revokeObjectURL(c.url));
          return [{ file, url }];
        });
      }

      let code = "";
      try {
        code = await detectCodeFromSource(await createImageBitmap(file));
      } catch { /* */ }

      if (!code) {
        setInfo(t("runningOcr"));
        let draft = await scanBook([file]);
        if (draft.status === "pending") {
          setInfo(t("waitingOcr"));
          draft = await pollScanJob(draft.job_id);
        }
        if (draft.status === "failed") {
          throw new Error(draft.error || t("ocrFailed"));
        }
        code = draft.isbn13 || "";
        if (!code && draft.title) {
          stopTracks();
          onQuery?.(draft.title);
          setInfo(`${t("titleSearchHit")}: ${draft.title}`);
          onClose?.();
          return;
        }
      }

      if (!code) {
        setError(t("isbnNotDetected"));
        setInfo(t("scannerAimBarcode"));
        return;
      }

      lastRef.current = { value: code, at: Date.now() };
      onDetect?.(code);
      setInfo(`${t("codeDetected")}: ${code}`);
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
    }
  };

  if (fatal) {
    return <Alert severity="warning" onClose={handleClose} sx={{ mb: 1 }}>{fatal}</Alert>;
  }

  return (
    <Box sx={{ mb: 1 }}>
      <Stack spacing={1}>
        {(info || error) && (
          <Alert
            severity={error ? "warning" : "info"}
            onClose={() => { setError(""); setInfo(liveOk ? t("scannerAimBarcode") : t("scannerCaptureHint")); }}
          >
            {error || info}
          </Alert>
        )}
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          style={{ width: "100%", borderRadius: 8, background: "#000", maxHeight: 280, objectFit: "cover" }}
        />
        {captures[0]?.url && (
          <Stack direction="row" spacing={1} alignItems="center">
            <Box
              component="img"
              src={captures[0].url}
              alt=""
              sx={{ width: 56, height: 56, objectFit: "cover", borderRadius: 1, border: "1px solid", borderColor: "divider" }}
            />
            <Typography variant="caption" color="text.secondary">{t("choosePhotos")}: 1</Typography>
            {liveOk && <Chip size="small" label={t("liveScanOn")} color="success" variant="outlined" />}
          </Stack>
        )}
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Button size="small" variant="contained" onClick={capture} disabled={busy}>
            {t("capture")}
          </Button>
          <Button size="small" variant="contained" color="secondary" onClick={analyze} disabled={busy}>
            {busy ? t("analyzingBook") : t("analyze")}
          </Button>
          <Button size="small" color="error" variant="outlined" onClick={handleClose} disabled={busy}>
            ■ {t("stopCamera")}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}
