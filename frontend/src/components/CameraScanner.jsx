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
/**
 * Still-frame analyse budget (ms) — barcode only, never book OCR.
 * Product packaging (Fournitures) + green CNP stickers need headroom:
 * large webcam frames + band×scale×angle×invert variants burn budget fast.
 */
const STILL_BUDGET_MS = 7000;
/** Cap longest edge before ZXing — full 1080p burns the whole budget on band 1. */
const STILL_MAX_EDGE = 1280;
const MAX_CAPTURES = 4;

/** Guide overlay fractions (must match the green strip UI). */
const GUIDE = { left: 0.08, right: 0.92, top: 0.42, height: 0.16 };

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
    zxingReaderPromise = Promise.all([
      import("@zxing/browser"),
      import("@zxing/library"),
    ]).then(([{ BrowserMultiFormatReader, BarcodeFormat }, { DecodeHintType }]) => {
      const hints = new Map();
      hints.set(DecodeHintType.TRY_HARDER, true);
      hints.set(DecodeHintType.POSSIBLE_FORMATS, [
        BarcodeFormat.EAN_13,
        BarcodeFormat.EAN_8,
        BarcodeFormat.UPC_A,
        BarcodeFormat.UPC_E,
        BarcodeFormat.CODE_128,
        BarcodeFormat.CODE_39,
        BarcodeFormat.ITF,
        BarcodeFormat.QR_CODE,
      ]);
      return new BrowserMultiFormatReader(hints);
    });
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
  if (source instanceof HTMLImageElement) {
    if (!source.naturalWidth) return null;
    canvas.width = source.naturalWidth;
    canvas.height = source.naturalHeight;
    canvas.getContext("2d").drawImage(source, 0, 0);
    return canvas;
  }
  return null;
}

async function fileToCanvas(file) {
  if (!file) return null;
  try {
    if (typeof createImageBitmap === "function") {
      const bmp = await createImageBitmap(file);
      const c = sourceToCanvas(bmp);
      try { bmp.close?.(); } catch { /* */ }
      return c;
    }
  } catch { /* fall through */ }
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const c = sourceToCanvas(img);
      URL.revokeObjectURL(url);
      resolve(c);
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(null);
    };
    img.src = url;
  });
}

function cropFrac(src, x0, y0, x1, y1) {
  const sx = Math.max(0, Math.floor(x0 * src.width));
  const sy = Math.max(0, Math.floor(y0 * src.height));
  const sw = Math.max(1, Math.floor((x1 - x0) * src.width));
  const sh = Math.max(1, Math.floor((y1 - y0) * src.height));
  const c = document.createElement("canvas");
  c.width = sw;
  c.height = sh;
  c.getContext("2d").drawImage(src, sx, sy, sw, sh, 0, 0, sw, sh);
  return c;
}

function scaleCanvas(src, factor) {
  if (factor === 1) return src;
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(src.width * factor));
  c.height = Math.max(1, Math.round(src.height * factor));
  const ctx = c.getContext("2d");
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(src, 0, 0, c.width, c.height);
  return c;
}

function rotateCanvas(src, deg) {
  if (!deg) return src;
  const rad = (deg * Math.PI) / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(src.width * cos + src.height * sin));
  c.height = Math.max(1, Math.round(src.width * sin + src.height * cos));
  const ctx = c.getContext("2d");
  ctx.translate(c.width / 2, c.height / 2);
  ctx.rotate(rad);
  ctx.drawImage(src, -src.width / 2, -src.height / 2);
  return c;
}

/** Shrink large stills so ZXing finishes before the analyse budget expires. */
function limitCanvasEdge(src, maxEdge = STILL_MAX_EDGE) {
  if (!src?.width) return src;
  const edge = Math.max(src.width, src.height);
  if (edge <= maxEdge) return src;
  return scaleCanvas(src, maxEdge / edge);
}

/** Grayscale + contrast boost (helps faint webcam barcode labels). */
function grayscaleContrast(src, contrast = 1.8) {
  const c = document.createElement("canvas");
  c.width = src.width;
  c.height = src.height;
  const ctx = c.getContext("2d");
  ctx.drawImage(src, 0, 0);
  const img = ctx.getImageData(0, 0, c.width, c.height);
  const d = img.data;
  const c255 = contrast * 255;
  const factor = (259 * (c255 + 255)) / (255 * (259 - c255));
  for (let i = 0; i < d.length; i += 4) {
    let g = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    g = factor * (g - 128) + 128;
    g = g < 0 ? 0 : g > 255 ? 255 : g;
    d[i] = d[i + 1] = d[i + 2] = g;
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

/** Invert (helps light bars on dark / green packaging labels). */
function invertCanvas(src) {
  const c = document.createElement("canvas");
  c.width = src.width;
  c.height = src.height;
  const ctx = c.getContext("2d");
  ctx.drawImage(src, 0, 0);
  const img = ctx.getImageData(0, 0, c.width, c.height);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    d[i] = 255 - d[i];
    d[i + 1] = 255 - d[i + 1];
    d[i + 2] = 255 - d[i + 2];
  }
  ctx.putImageData(img, 0, 0);
  return c;
}

/** Simple threshold binarize after grayscale (curved / glossy stickers). */
function binarizeCanvas(src, threshold = 128) {
  const gray = grayscaleContrast(src, 1.4);
  const ctx = gray.getContext("2d");
  const img = ctx.getImageData(0, 0, gray.width, gray.height);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    const v = d[i] >= threshold ? 255 : 0;
    d[i] = d[i + 1] = d[i + 2] = v;
  }
  ctx.putImageData(img, 0, 0);
  return gray;
}

/**
 * Rough score: barcodes have strong horizontal high-frequency energy in the lower/middle band.
 * Used to prefer barcode-looking stills when analysing multiple captures.
 */
function barcodeLikenessScore(canvas) {
  if (!canvas?.width) return 0;
  const band = cropFrac(canvas, 0.05, 0.4, 0.95, 0.95);
  const w = Math.min(band.width, 160);
  const h = Math.min(band.height, 80);
  const tiny = document.createElement("canvas");
  tiny.width = w;
  tiny.height = h;
  const ctx = tiny.getContext("2d");
  ctx.drawImage(band, 0, 0, w, h);
  const { data } = ctx.getImageData(0, 0, w, h);
  let edges = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 1; x < w; x++) {
      const i = (y * w + x) * 4;
      const j = (y * w + x - 1) * 4;
      const g1 = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
      const g0 = 0.299 * data[j] + 0.587 * data[j + 1] + 0.114 * data[j + 2];
      if (Math.abs(g1 - g0) > 28) edges += 1;
    }
  }
  return edges / Math.max(1, w * h);
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

/**
 * Strengthen still-frame decode for retail packaging + books:
 * downscale first, try barcode bands BEFORE full frame (avoids budget starvation),
 * upscale / grayscale / invert / binarize / small + 90° rotations,
 * BarcodeDetector + ZXing TRY_HARDER — early-exit on first plausible code.
 *
 * ``closeup`` (POS Analyser on captured photos): prefer lower/full frame first —
 * green sticker close-ups often miss the live guide strip crop.
 */
async function detectCodeFromStill(canvas, {
  preferIsbn = false,
  budgetMs = STILL_BUDGET_MS,
  closeup = false,
} = {}) {
  if (!canvas?.width) return "";
  const base = limitCanvasEdge(canvas);
  const deadline = Date.now() + budgetMs;
  let incomplete = "";

  // Crops first (fast, high hit-rate on packaging stickers); full frame last by default.
  // Close-up captures: try lower half + full frame before the live guide strip.
  const bands = closeup
    ? [
        [0.02, 0.30, 0.98, 0.98],
        [0.04, 0.45, 0.96, 0.98],
        [0, 0, 1, 1],
        [0.08, 0.15, 0.92, 0.55],
        [GUIDE.left, GUIDE.top, GUIDE.right, GUIDE.top + GUIDE.height],
        [0.12, 0.28, 0.88, 0.72],
      ]
    : [
        [GUIDE.left, GUIDE.top, GUIDE.right, GUIDE.top + GUIDE.height],
        [0.04, 0.55, 0.96, 0.98],
        [0.04, 0.42, 0.96, 0.78],
        [0.08, 0.18, 0.92, 0.55],
        [0.12, 0.28, 0.88, 0.72],
        [0.2, 0.35, 0.8, 0.65],
        [0, 0, 1, 1],
      ];
  // Phase A: upright — include invert early (green / light-on-dark stickers).
  // Phase B: tilt. Phase C: vertical barcodes.
  const phases = [
    { scales: [1, 1.6, 2.4], angles: [0], hardVariants: true },
    { scales: [1.2, 2.0, 3.0], angles: [0, -8, 8, -15, 15], hardVariants: true },
    { scales: [1.5, 2.5], angles: [90, -90], hardVariants: true },
  ];

  const tryVariant = async (v) => {
    if (Date.now() > deadline) return "deadline";
    const code = await detectCodeFromSource(v, { preferIsbn });
    if (!code) return "";
    if (isPlausibleProductBarcode(code)) return { hit: code };
    if (!incomplete || code.length > incomplete.length) incomplete = code;
    return "";
  };

  for (const phase of phases) {
    for (const band of bands) {
      const cropped = cropFrac(base, band[0], band[1], band[2], band[3]);
      for (const scale of phase.scales) {
        const sized = scaleCanvas(cropped, scale);
        for (const angle of phase.angles) {
          if (Date.now() > deadline) {
            return incomplete && isPlausibleProductBarcode(incomplete) ? incomplete : "";
          }
          const rotated = rotateCanvas(sized, angle);
          const variants = phase.hardVariants
            ? [
                rotated,
                grayscaleContrast(rotated, 1.6),
                grayscaleContrast(rotated, 2.4),
                invertCanvas(rotated),
                invertCanvas(grayscaleContrast(rotated, 1.8)),
                binarizeCanvas(rotated, 110),
                binarizeCanvas(rotated, 145),
              ]
            : [
                rotated,
                grayscaleContrast(rotated, 1.7),
                grayscaleContrast(rotated, 2.2),
                invertCanvas(rotated),
              ];
          for (const v of variants) {
            const r = await tryVariant(v);
            if (r === "deadline") {
              return incomplete && isPlausibleProductBarcode(incomplete) ? incomplete : "";
            }
            if (r?.hit) return r.hit;
          }
        }
      }
    }
  }
  return incomplete && isPlausibleProductBarcode(incomplete) ? incomplete : "";
}

/** Live path: decode inside the green guide strip (avoids face / room noise). */
function guideStripCanvas(video) {
  if (!video?.videoWidth || !video?.videoHeight) return null;
  const full = sourceToCanvas(video);
  if (!full) return null;
  return cropFrac(full, GUIDE.left, GUIDE.top, GUIDE.right, GUIDE.top + GUIDE.height);
}

/** Live POS fallback: lower packaging band when the sticker sits below the guide. */
function lowerBandCanvas(video) {
  if (!video?.videoWidth || !video?.videoHeight) return null;
  const full = sourceToCanvas(video);
  if (!full) return null;
  return cropFrac(full, 0.04, 0.52, 0.96, 0.98);
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
 * - mode="pos" | mode="product" (default pos): continuous live decode (guide strip +
 *   lower band) via BarcodeDetector/ZXing → onDetect; Capturer/Analyser as fallback.
 *   Never book Vision/OCR, never ISBN banners. Used by POS + Nouvel article.
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
  const lastRef = useRef({ value: "", at: 0, needsClear: false });
  const decodingRef = useRef(false);
  const capturesRef = useRef([]);
  const [fatal, setFatal] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  const [liveOk, setLiveOk] = useState(false);
  const [stream, setStream] = useState(null);
  const [captures, setCaptures] = useState([]); // { file, url }

  const aimHint = () => (isPos ? t("scannerAimStrip") : t("scannerAimBarcode"));
  const captureHint = () => (isPos ? t("scannerCaptureHintPos") : t("scannerCaptureHint"));

  useEffect(() => {
    capturesRef.current = captures;
  }, [captures]);

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

  const revokeAllCaptures = (list) => {
    (list || []).forEach((c) => c.url && URL.revokeObjectURL(c.url));
  };

  const handleClose = () => {
    stopTracks();
    revokeAllCaptures(capturesRef.current);
    setCaptures([]);
    onClose?.();
  };

  const emitDetect = (value) => {
    onDetect?.(value);
    if (stopOnDetect) {
      stopTracks();
      revokeAllCaptures(capturesRef.current);
      setCaptures([]);
      onClose?.();
    }
  };

  useEffect(() => {
    let raf;
    let stopped = false;
    let lastTickAt = 0;
    let missTicks = 0;

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
      // Live decode always runs (BD and/or ZXing) — never tell cashiers live is "off".
      setLiveOk(true);
      setInfo(aimHint());
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
          // Live: guide strip first; POS also tries lower band (sticker below strip).
          const strip = guideStripCanvas(videoRef.current);
          let value = strip
            ? await detectCodeFromSource(strip, { preferIsbn })
            : "";
          if ((!value || !isPlausibleProductBarcode(value)) && isPos) {
            const lower = lowerBandCanvas(videoRef.current);
            if (lower) {
              const alt = await detectCodeFromSource(lower, { preferIsbn });
              if (alt && isPlausibleProductBarcode(alt)) value = alt;
            }
          }
          if (value && isPlausibleProductBarcode(value)) {
            missTicks = 0;
            const same = value === lastRef.current.value;
            // Cooldown + require barcode to leave frame before re-adding the same code.
            if (same && lastRef.current.needsClear) {
              /* still in frame after a hit — wait for misses */
            } else if (!same || now - lastRef.current.at > DETECT_COOLDOWN_MS) {
              lastRef.current = { value, at: now, needsClear: true };
              setInfo(`${t("codeDetected")}: ${value}`);
              setError("");
              emitDetect(value);
            }
          } else {
            missTicks += 1;
            if (missTicks >= 3 && lastRef.current.needsClear) {
              lastRef.current = { ...lastRef.current, needsClear: false };
            }
            // Soft hint after a few empty strips — never yellow "non détecté" from live.
            if (missTicks === 8) {
              setError("");
              setInfo(t("scannerAimStrip"));
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
      const next = [...prev, { file, url }];
      if (next.length > MAX_CAPTURES) {
        const dropped = next.splice(0, next.length - MAX_CAPTURES);
        revokeAllCaptures(dropped);
      }
      return next;
    });
    setInfo(isPos ? t("captureReadyAnalyzePos") : t("captureReadyAnalyze"));

    // Quick still decode on this capture (strengthened path).
    try {
      const canvas = await fileToCanvas(file);
      const code = await detectCodeFromStill(canvas, {
        preferIsbn,
        budgetMs: isPos ? 3200 : 2200,
        closeup: isPos,
      });
      if (code && isPlausibleProductBarcode(code)) {
        lastRef.current = { value: code, at: Date.now(), needsClear: true };
        setInfo(`${t("codeDetected")}: ${code}`);
        setError("");
        emitDetect(code);
      }
    } catch { /* still-frame detect optional on capture */ }
  };

  const analyze = async () => {
    setError("");
    setBusy(true);
    try {
      let list = capturesRef.current.slice();
      if (!list.length) {
        const blob = await frameToBlob(videoRef.current);
        if (!blob) {
          setError(t("cameraNotReady"));
          return;
        }
        const file = new File([blob], `scan-${Date.now()}.jpg`, { type: "image/jpeg" });
        const url = URL.createObjectURL(file);
        list = [{ file, url }];
        setCaptures(list);
      }

      // Decode captured stills first (never the live face frame when photos exist).
      // Prefer barcode-looking photos (high horizontal edge energy).
      const withCanvas = [];
      for (const c of list) {
        const canvas = await fileToCanvas(c.file);
        if (canvas) withCanvas.push({ ...c, canvas, score: barcodeLikenessScore(canvas) });
      }
      withCanvas.sort((a, b) => b.score - a.score);

      // Give the best photo nearly the full budget (POS green stickers need invert variants).
      const n = Math.max(1, Math.min(withCanvas.length, 2));
      const perPhotoBudget = Math.max(
        isPos ? 5500 : 2800,
        Math.floor(STILL_BUDGET_MS / n),
      );
      let code = "";
      let incomplete = "";
      for (const item of withCanvas) {
        const hit = await detectCodeFromStill(item.canvas, {
          preferIsbn,
          budgetMs: perPhotoBudget,
          closeup: isPos,
        });
        if (hit && isPlausibleProductBarcode(hit)) {
          code = hit;
          break;
        }
        if (hit && (!incomplete || hit.length > incomplete.length)) incomplete = hit;
      }

      // POS / article (Fournitures): barcode decode only — never call bookscan Vision/OCR.
      if (isPos) {
        if (code && isPlausibleProductBarcode(code)) {
          lastRef.current = { value: code, at: Date.now(), needsClear: true };
          setInfo(`${t("codeDetected")}: ${code}`);
          setError("");
          emitDetect(code);
          return;
        }
        if (incomplete) {
          setError(t("barcodeIncomplete"));
          setInfo(t("scannerRecaptureBarcode"));
        } else {
          // Keep photos for retry — do not clear captures.
          // Prefer actionable illegible copy (not a false ISBN banner).
          setError(t("barcodeIllegible"));
          setInfo(t("scannerRecaptureBarcode"));
        }
        return;
      }

      // Book mode: optional OCR / title search fallback (uses first capture).
      if (!code || !isPlausibleProductBarcode(code)) {
        setInfo(t("runningOcr"));
        const file = list[list.length - 1]?.file || list[0]?.file;
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

      lastRef.current = { value: code, at: Date.now(), needsClear: true };
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
        {captures.length > 0 && (
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
            {captures.map((c, i) => (
              <Box
                key={c.url || i}
                component="img"
                src={c.url}
                alt=""
                sx={{ width: 56, height: 56, objectFit: "cover", borderRadius: 1, border: "1px solid", borderColor: "divider" }}
              />
            ))}
            <Typography variant="caption" color="text.secondary">
              {t("choosePhotos")}: {captures.length}
            </Typography>
          </Stack>
        )}
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
          {liveOk && <Chip size="small" label={t("liveScanOn")} color="success" variant="outlined" />}
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
