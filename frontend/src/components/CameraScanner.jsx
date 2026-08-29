import { useEffect, useRef, useState } from "react";
import { Box, Button, Stack, Alert, Typography, Chip } from "@mui/material";
import { useTranslation } from "react-i18next";
import { scanBook, pollScanJob } from "../api";

const BARCODE_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128", "code_39", "qr_code", "itf"];

/** High-res like BookCreate (XPC01 / USB cams); fall back so older webcams still open. */
const POS_VIDEO_CONSTRAINTS = {
  width: { ideal: 1920, max: 3840 },
  height: { ideal: 1080, max: 2160 },
  frameRate: { ideal: 24, max: 30 },
};

/** Live decode throttle (ms): keep POS snappy without burning CPU every RAF. */
const LIVE_THROTTLE_NATIVE_MS = 180;
const LIVE_THROTTLE_ZXING_MS = 400;
/** Ignore duplicate detections for this long after a hit. */
const DETECT_COOLDOWN_MS = 1800;

/** ISBN-13 check digit (978/979). Prefer valid ISBN when several codes are in frame (book mode). */
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

/**
 * Reject partial / noisy camera decodes (e.g. 5-digit "71899").
 * Typed POS search can still use short refs; camera needs a fuller code.
 * Accepts: EAN-8+, UPC, EAN-13/ISBN, CNP 619…, Code128 alphanumeric ≥4 with a digit.
 */
export function isPlausibleProductBarcode(raw) {
  const s = String(raw || "").replace(/[-\s]/g, "").trim();
  if (!s || s.length > 48) return false;
  if (/^\d+$/.test(s)) {
    // Partial EAN/ISBN fragments are typically 4–7 digits; EAN-8 is the shortest retail bar.
    if (s.length < 8) return false;
    if (s.length > 14) return false;
    return true;
  }
  // Code128 / local refs (e.g. PEN-001) — need a digit and reasonable length.
  if (/^[A-Za-z0-9][A-Za-z0-9._/-]{2,47}$/.test(s) && /\d/.test(s)) return true;
  return false;
}

let zxingReaderPromise = null;

function getZxingReader() {
  if (!zxingReaderPromise) {
    zxingReaderPromise = import("@zxing/browser").then(
      ({ BrowserMultiFormatReader }) => new BrowserMultiFormatReader(),
    );
  }
  return zxingReaderPromise;
}

function sourceToCanvas(source) {
  if (source instanceof HTMLCanvasElement) return source;
  const canvas = document.createElement("canvas");
  if (source instanceof HTMLVideoElement) {
    if (!source.videoWidth || !source.videoHeight) return null;
    canvas.width = source.videoWidth;
    canvas.height = source.videoHeight;
    canvas.getContext("2d").drawImage(source, 0, 0);
    return canvas;
  }
  if (typeof ImageBitmap !== "undefined" && source instanceof ImageBitmap) {
    canvas.width = source.width;
    canvas.height = source.height;
    canvas.getContext("2d").drawImage(source, 0, 0);
    return canvas;
  }
  return null;
}

async function detectWithZxing(source) {
  try {
    const canvas = sourceToCanvas(source);
    if (!canvas) return "";
    const reader = await getZxingReader();
    const result = await reader.decodeFromCanvas(canvas);
    return result?.getText?.() || "";
  } catch {
    return "";
  }
}

/** Pick best raw value from native detector results (prefer longer plausible / ISBN in book mode). */
function pickBestCode(rawValues, { preferIsbn = false } = {}) {
  const cleaned = rawValues.map((v) => String(v || "").trim()).filter(Boolean);
  if (!cleaned.length) return "";
  if (preferIsbn) {
    const isbn = cleaned.find((c) => isbn13CheckOk(c));
    if (isbn) return isbn;
  }
  const plausible = cleaned.filter(isPlausibleProductBarcode);
  const pool = plausible.length ? plausible : cleaned;
  return pool.reduce((a, b) => (b.length > a.length ? b : a), pool[0]);
}

async function detectCodeFromSource(source, { preferIsbn = false } = {}) {
  if ("BarcodeDetector" in window) {
    try {
      const detector = new window.BarcodeDetector({ formats: BARCODE_FORMATS });
      const codes = await detector.detect(source);
      if (codes.length) {
        return pickBestCode(codes.map((c) => c.rawValue), { preferIsbn });
      }
    } catch { /* fall through to ZXing */ }
  }
  return detectWithZxing(source);
}

function frameToBlob(video) {
  if (!video?.videoWidth || !video?.videoHeight) return Promise.resolve(null);
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/jpeg", 0.92));
}

async function openPosCameraStream() {
  const attempts = [
    { ...POS_VIDEO_CONSTRAINTS, facingMode: { ideal: "environment" } },
    {
      width: { ideal: 1280, max: 1920 },
      height: { ideal: 720, max: 1080 },
      facingMode: { ideal: "environment" },
    },
    { facingMode: { ideal: "environment" } },
    { facingMode: "environment" },
    true,
  ];
  let lastErr;
  for (const video of attempts) {
    try {
      return await navigator.mediaDevices.getUserMedia({ video, audio: false });
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error("getUserMedia failed");
}

async function bumpTrackResolution(stream) {
  const track = stream?.getVideoTracks?.()?.[0];
  if (!track?.getCapabilities || !track.applyConstraints) return;
  try {
    const caps = track.getCapabilities();
    const wMax = caps.width?.max;
    const hMax = caps.height?.max;
    if (!wMax && !hMax) return;
    const width = wMax ? { ideal: Math.min(wMax, 1920), max: Math.min(wMax, 3840) } : POS_VIDEO_CONSTRAINTS.width;
    const height = hMax ? { ideal: Math.min(hMax, 1080), max: Math.min(hMax, 2160) } : POS_VIDEO_CONSTRAINTS.height;
    await track.applyConstraints({ width, height });
  } catch { /* device may reject */ }
}

/**
 * Shared camera barcode scanner.
 * - mode="pos" (default): fast ZXing/BarcodeDetector only — never book Vision/OCR, never ISBN banners.
 * - mode="book": still-frame + optional /catalog/books/scan OCR fallback (ISBN/title).
 */
export default function CameraScanner({
  mode = "pos",
  onDetect,
  onQuery,
  onClose,
  stopOnDetect = false,
}) {
  const { t } = useTranslation();
  const isPos = mode !== "book";
  const preferIsbn = !isPos;
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const lastRef = useRef({ value: "", at: 0 });
  const decodingRef = useRef(false);
  const [fatal, setFatal] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveOk, setLiveOk] = useState(false);
  const [stream, setStream] = useState(null);
  const [captures, setCaptures] = useState([]); // { file, url }

  const aimHint = () => (isPos ? t("scannerAimProductBarcode") : t("scannerAimBarcode"));
  const captureHint = () => (isPos ? t("scannerCaptureHintPos") : t("scannerCaptureHint"));

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

  const emitDetect = (value) => {
    onDetect?.(value);
    if (stopOnDetect) {
      stopTracks();
      captures.forEach((c) => c.url && URL.revokeObjectURL(c.url));
      onClose?.();
    }
  };

  useEffect(() => {
    let raf;
    let stopped = false;
    let lastTickAt = 0;

    (async () => {
      let media;
      try {
        media = await openPosCameraStream();
        await bumpTrackResolution(media);
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

      const hasNative = "BarcodeDetector" in window;
      setLiveOk(true);
      setInfo(hasNative ? aimHint() : captureHint());
      const throttleMs = hasNative ? LIVE_THROTTLE_NATIVE_MS : LIVE_THROTTLE_ZXING_MS;

      const tick = async () => {
        if (stopped || !videoRef.current) return;
        const now = Date.now();
        if (now - lastTickAt < throttleMs || decodingRef.current) {
          raf = requestAnimationFrame(tick);
          return;
        }
        lastTickAt = now;
        decodingRef.current = true;
        try {
          const value = await detectCodeFromSource(videoRef.current, { preferIsbn });
          if (value && isPlausibleProductBarcode(value)) {
            if (value !== lastRef.current.value || now - lastRef.current.at > DETECT_COOLDOWN_MS) {
              lastRef.current = { value, at: now };
              setInfo(`${t("codeDetected")}: ${value}`);
              setError("");
              emitDetect(value);
            }
          }
        } catch { /* frame not ready */ }
        decodingRef.current = false;
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
    setInfo(isPos ? t("captureReadyAnalyzePos") : t("captureReadyAnalyze"));

    try {
      const code = await detectCodeFromSource(await createImageBitmap(file), { preferIsbn });
      if (code && isPlausibleProductBarcode(code)) {
        lastRef.current = { value: code, at: Date.now() };
        setInfo(`${t("codeDetected")}: ${code}`);
        emitDetect(code);
      } else if (code) {
        setError(t("barcodeIncomplete"));
        setInfo(t("scannerRecaptureBarcode"));
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
        code = await detectCodeFromSource(await createImageBitmap(file), { preferIsbn });
      } catch { /* */ }

      // POS / article: barcode decode only — never call bookscan Vision/OCR.
      if (isPos) {
        if (code && isPlausibleProductBarcode(code)) {
          lastRef.current = { value: code, at: Date.now() };
          setInfo(`${t("codeDetected")}: ${code}`);
          emitDetect(code);
          return;
        }
        if (code) {
          setError(t("barcodeIncomplete"));
        } else {
          setError(t("barcodeNotDetected"));
        }
        setInfo(t("scannerRecaptureBarcode"));
        return;
      }

      // Book mode: optional OCR / title search fallback.
      if (!code || !isPlausibleProductBarcode(code)) {
        setInfo(t("runningOcr"));
        let draft = await scanBook([file]);
        if (draft.status === "pending") {
          setInfo(t("waitingOcr"));
          draft = await pollScanJob(draft.job_id);
        }
        if (draft.status === "failed") {
          throw new Error(draft.error || t("ocrFailed"));
        }
        code = draft.isbn13 || draft.barcode_raw || "";
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
      setInfo(`${t("codeDetected")}: ${code}`);
      emitDetect(code);
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
            onClose={() => { setError(""); setInfo(liveOk ? aimHint() : captureHint()); }}
          >
            {error || info}
          </Alert>
        )}
        <Box sx={{ position: "relative", borderRadius: 1, overflow: "hidden", bgcolor: "#000" }}>
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            style={{ width: "100%", display: "block", maxHeight: 280, objectFit: "cover" }}
          />
          {/* Guide: horizontal barcode strip (not ISBN-specific). */}
          <Box
            aria-hidden
            sx={{
              pointerEvents: "none",
              position: "absolute",
              left: "8%",
              right: "8%",
              top: "42%",
              height: "16%",
              border: "2px solid",
              borderColor: "success.light",
              borderRadius: 1,
              boxShadow: "0 0 0 9999px rgba(0,0,0,0.28)",
            }}
          />
        </Box>
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
            {busy ? (isPos ? t("analyzingBarcode") : t("analyzingBook")) : t("analyze")}
          </Button>
          <Button size="small" color="error" variant="outlined" onClick={handleClose} disabled={busy}>
            ■ {t("stopCamera")}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
}
