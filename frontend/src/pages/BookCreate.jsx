import { useEffect, useRef, useState } from "react";
import {
  AppBar, Toolbar, Typography, Button, Box, Grid, Paper, TextField, Stack, Alert, Snackbar,
  Chip, LinearProgress, Select, MenuItem, FormControl, InputLabel, Stepper, Step, StepLabel,
} from "@mui/material";
import { useTranslation } from "react-i18next";
import { scanBook, pollScanJob, cancelScanJob, createBook, listCategories, listTaxRates } from "../api";
import LangToggle from "../LangToggle.jsx";
import CameraScanner from "../components/CameraScanner.jsx";

/** ISBN-13 check digit (bookland 978/979 only). Reject OCR digit soup before hinting. */
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

async function detectIsbn(file) {
  if (!("BarcodeDetector" in window)) return "";
  try {
    const det = new window.BarcodeDetector({
      formats: ["ean_13", "ean_8", "upc_a", "upc_e", "code_128"],
    });
    const bmp = await createImageBitmap(file);
    const tryDetect = async (source) => {
      const codes = await det.detect(source);
      // Prefer checksum-valid bookland EAN-13
      const bookland = codes.filter((c) => isbn13CheckOk(c.rawValue));
      if (bookland.length) return bookland[0].rawValue;
      // Reject invalid-checksum 978/979 blobs (common OCR / misread)
      return "";
    };
    let hit = await tryDetect(bmp);
    if (hit) {
      bmp.close?.();
      return hit;
    }
    // Small / angled verso barcodes: bands, corners, rotations, contrast, upscale
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const w = bmp.width;
    const h = bmp.height;
    const crops = [
      { sx: 0, sy: Math.floor(h * 0.55), sw: w, sh: Math.floor(h * 0.45) },
      { sx: 0, sy: Math.floor(h * 0.72), sw: w, sh: Math.floor(h * 0.28) },
      { sx: Math.floor(w * 0.1), sy: Math.floor(h * 0.7), sw: Math.floor(w * 0.8), sh: Math.floor(h * 0.3) },
      { sx: Math.floor(w * 0.45), sy: Math.floor(h * 0.65), sw: Math.floor(w * 0.55), sh: Math.floor(h * 0.35) },
      { sx: 0, sy: Math.floor(h * 0.65), sw: Math.floor(w * 0.55), sh: Math.floor(h * 0.35) },
      { sx: Math.floor(w * 0.45), sy: 0, sw: Math.floor(w * 0.55), sh: Math.floor(h * 0.35) },
      { sx: 0, sy: 0, sw: Math.floor(w * 0.55), sh: Math.floor(h * 0.35) },
    ];
    const angles = [-12, 12, -20, 20, 90, 180, 270];
    for (const c of crops) {
      if (c.sw < 20 || c.sh < 20) continue;
      for (const scale of [2, 3]) {
        canvas.width = c.sw * scale;
        canvas.height = c.sh * scale;
        ctx.imageSmoothingEnabled = true;
        ctx.drawImage(bmp, c.sx, c.sy, c.sw, c.sh, 0, 0, canvas.width, canvas.height);
        // Boost contrast for faded ink
        try {
          const id = ctx.getImageData(0, 0, canvas.width, canvas.height);
          const d = id.data;
          for (let i = 0; i < d.length; i += 4) {
            const g = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
            const v = g < 140 ? 0 : 255;
            d[i] = d[i + 1] = d[i + 2] = v;
          }
          ctx.putImageData(id, 0, 0);
        } catch { /* ignore */ }
        hit = await tryDetect(canvas);
        if (hit) break;
        // Non-binarized upscale too
        ctx.drawImage(bmp, c.sx, c.sy, c.sw, c.sh, 0, 0, canvas.width, canvas.height);
        hit = await tryDetect(canvas);
        if (hit) break;
        for (const angle of angles) {
          const rot = document.createElement("canvas");
          const rad = (angle * Math.PI) / 180;
          const rw = Math.abs(canvas.width * Math.cos(rad)) + Math.abs(canvas.height * Math.sin(rad));
          const rh = Math.abs(canvas.width * Math.sin(rad)) + Math.abs(canvas.height * Math.cos(rad));
          rot.width = Math.ceil(rw) || 1;
          rot.height = Math.ceil(rh) || 1;
          const rctx = rot.getContext("2d");
          rctx.translate(rot.width / 2, rot.height / 2);
          rctx.rotate(rad);
          rctx.drawImage(canvas, -canvas.width / 2, -canvas.height / 2);
          hit = await tryDetect(rot);
          if (hit) break;
        }
        if (hit) break;
      }
      if (hit) break;
    }
    bmp.close?.();
    return hit || "";
  } catch {
    return "";
  }
}

/** Prefer ISBN from back cover (last images), then front. */
async function detectIsbnFromImages(files) {
  for (let i = files.length - 1; i >= 0; i--) {
    const code = await detectIsbn(files[i]);
    if (code) return code;
  }
  return "";
}

/** Client-side blur (Laplacian variance) + contrast check before OCR. */
async function assessImageQuality(file) {
  const bmp = await createImageBitmap(file);
  const w = Math.min(bmp.width, 320);
  const h = Math.round((bmp.height / bmp.width) * w);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(bmp, 0, 0, w, h);
  bmp.close?.();
  const { data } = ctx.getImageData(0, 0, w, h);
  const gray = new Float32Array(w * h);
  let min = 255;
  let max = 0;
  for (let i = 0, p = 0; i < data.length; i += 4, p++) {
    const g = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    gray[p] = g;
    if (g < min) min = g;
    if (g > max) max = g;
  }
  // Laplacian kernel approximation on interior pixels
  let sum = 0;
  let sumSq = 0;
  let n = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const i = y * w + x;
      const lap = gray[i - w] + gray[i + w] + gray[i - 1] + gray[i + 1] - 4 * gray[i];
      sum += lap;
      sumSq += lap * lap;
      n++;
    }
  }
  const mean = sum / Math.max(n, 1);
  const variance = sumSq / Math.max(n, 1) - mean * mean;
  const contrast = max - min;
  return {
    blurScore: variance,
    contrast,
    blurry: variance < 80,
    lowContrast: contrast < 40,
  };
}

async function assessCapturesQuality(files) {
  const results = [];
  for (const f of files) {
    try {
      const q = await assessImageQuality(f);
      const fill = await estimateBookFillRatio(f);
      results.push({ ...q, bookFillRatio: fill, bookTooSmall: fill < 0.22 });
    } catch {
      results.push({ blurry: false, lowContrast: false, bookFillRatio: 1, bookTooSmall: false });
    }
  }
  const fills = results.map((r) => r.bookFillRatio).filter((x) => Number.isFinite(x));
  const minFill = fills.length ? Math.min(...fills) : 1;
  return {
    blurry: results.some((r) => r.blurry),
    lowContrast: results.some((r) => r.lowContrast),
    bookTooSmall: results.some((r) => r.bookTooSmall) || minFill < 0.22,
    bookFillRatio: minFill,
  };
}

/** Rough book-vs-frame ratio: saturated / non-white pixels (distant XTRIKE shots). */
async function estimateBookFillRatio(file) {
  const bmp = await createImageBitmap(file);
  const size = 160;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) {
    bmp.close?.();
    return 1;
  }
  ctx.drawImage(bmp, 0, 0, size, size);
  bmp.close?.();
  const { data } = ctx.getImageData(0, 0, size, size);
  let interesting = 0;
  const total = size * size;
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const sat = max === 0 ? 0 : (max - min) / max;
    const bright = (r + g + b) / 3;
    // Book ink/color vs bright window / white wall / hand skin is harder —
    // count mid-tone saturated or darkish non-white pixels.
    if (sat > 0.18 && bright < 245) interesting += 1;
    else if (bright < 140 && sat > 0.05) interesting += 1;
  }
  return interesting / Math.max(total, 1);
}

const EMPTY = {
  isbn13: "", barcode_raw: "", barcode_symbology: "", barcode_kind: "",
  edition_kind: "",
  title: "", subtitle: "", authors: "", translators: "", publisher: "",
  series: "", edition: "", pub_year: "", pages: "", languages: "", subject: "",
  description: "", sale_price: "", category: "", tax_rate: "",
};

/** Client-side school / CNP heuristics when server edition_kind is missing. */
function looksLikeSchoolDraft(d) {
  if (!d) return false;
  if (d.edition_kind === "school_cnp" || d.raw?.school_edition || d.raw?.edition_kind === "school_cnp") {
    return true;
  }
  if (d.barcode_kind === "local_product" || d.raw?.barcode_non_isbn) return true;
  const bc = String(d.barcode_raw || "").trim();
  if (bc.startsWith("619")) return true;
  const blob = [
    d.title, d.subtitle, d.publisher, d.subject, d.description,
    ...(d.raw?.title_candidates || []),
    d.raw?.rejected_title, d.raw?.suggested_title, d.raw?.pre_repair_title,
  ].filter(Boolean).join(" ");
  return /math[eé]matiques?|ématiques|matiques|technologie\s+de\s+l['’]?informati|nologie\s+de\s+l['’]?informati|\bCNP\b|centre\s+national|ann[eé]e\s+(?:secondaire|primaire)|enseignement\s+secondaire|كتاب|مركز\s+وطني|التاريخ|تاريخ|جغرافيا|histoire|2[eè]me\s+ann/i.test(blob);
}

/** Drop OCR subject shards that must never autofill as authors. */
function scrubAuthors(authorsStr, title) {
  const blocked = /math|ématiques|matiques|nologie|technologie|informati|histoire|التاريخ|تاريخ|جغرافيا|sciences?|physique|chimie|fran[cç]ais|anglais|arabe|ann[eé]e|tome|secondaire|enseignement|manuel|scolaire|^cnp$|كتاب/i;
  return String(authorsStr || "")
    .split(",")
    .map((s) => s.trim())
    .filter((s) => {
      if (!s || s.length < 3) return false;
      if (blocked.test(s)) return false;
      if (title && title.toLowerCase().includes(s.toLowerCase())) return false;
      if (/^[a-zà-ÿ]{2,12}$/i.test(s) && s.length <= 12) return false;
      if (/[a-z][A-Z]/.test(s)) return false;
      return true;
    })
    .join(", ");
}

/** Portrait book guide (typical trade paperback ~2:3). Overlay only — full frame still captured. */
const BOOK_ASPECT = "2 / 3";

/**
 * Highest practical resolution for XTRIKE ME XPC01 / generic USB webcams.
 * Browser negotiates down when the device cannot meet ideal/max.
 */
const BOOK_VIDEO_CONSTRAINTS = {
  width: { ideal: 1920, max: 3840 },
  height: { ideal: 1080, max: 2160 },
  frameRate: { ideal: 30, max: 60 },
};

/** Try high-res constraints first; fall back so older webcams still open. */
async function openBookCameraStream(deviceId) {
  const attempts = [];
  if (deviceId) {
    attempts.push({ ...BOOK_VIDEO_CONSTRAINTS, deviceId: { exact: deviceId } });
    attempts.push({ ...BOOK_VIDEO_CONSTRAINTS, deviceId: { ideal: deviceId } });
  }
  attempts.push({ ...BOOK_VIDEO_CONSTRAINTS, facingMode: { ideal: "environment" } });
  attempts.push({
    width: { ideal: 1280, max: 1920 },
    height: { ideal: 720, max: 1080 },
    facingMode: { ideal: "environment" },
  });
  attempts.push({ facingMode: "environment" });
  attempts.push(true);

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

/** Nudge track toward max capability after open (helps some USB cams ignore ideal). */
async function bumpTrackResolution(stream) {
  const track = stream?.getVideoTracks?.()?.[0];
  if (!track?.getCapabilities || !track.applyConstraints) return;
  try {
    const caps = track.getCapabilities();
    const wMax = caps.width?.max;
    const hMax = caps.height?.max;
    if (!wMax && !hMax) return;
    const width = wMax ? { ideal: Math.min(wMax, 1920), max: Math.min(wMax, 3840) } : BOOK_VIDEO_CONSTRAINTS.width;
    const height = hMax ? { ideal: Math.min(hMax, 1080), max: Math.min(hMax, 2160) } : BOOK_VIDEO_CONSTRAINTS.height;
    await track.applyConstraints({ width, height });
  } catch { /* device may reject — keep negotiated settings */ }
}

export default function BookCreate({ onBack, onLogout }) {
  const { t } = useTranslation();
  const videoRef = useRef(null);
  const fileRef = useRef(null);
  const streamRef = useRef(null);
  const aliveRef = useRef(true);
  const abortRef = useRef(null);
  const jobIdRef = useRef(null);
  const [stream, setStream] = useState(null);
  const [images, setImages] = useState([]);       // [front?, back?]
  const [previews, setPreviews] = useState([]);
  const [captureStep, setCaptureStep] = useState("front"); // front | back | ready
  const [busy, setBusy] = useState(false);
  const [busyLabel, setBusyLabel] = useState("");
  const [scanProgress, setScanProgress] = useState(0);
  const [scanStage, setScanStage] = useState("");
  const [draft, setDraft] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [cameras, setCameras] = useState([]);
  const [cameraId, setCameraId] = useState(localStorage.getItem("teyssir_camera") || "");
  const [cats, setCats] = useState([]);
  const [taxes, setTaxes] = useState([]);
  const [barcodeCamera, setBarcodeCamera] = useState(false);

  const stopCamera = () => {
    const s = streamRef.current;
    streamRef.current = null;
    if (s) {
      try {
        s.getTracks().forEach((tk) => {
          try {
            tk.stop();
          } catch { /* already stopped */ }
        });
      } catch { /* ignore */ }
    }
    // Also clear any lingering tracks attached to the <video> element
    const v = videoRef.current;
    if (v) {
      try {
        const attached = v.srcObject;
        if (attached && attached !== s) {
          attached.getTracks?.().forEach((tk) => {
            try { tk.stop(); } catch { /* */ }
          });
        }
        v.srcObject = null;
        v.pause?.();
      } catch { /* ignore */ }
    }
    setStream(null);
  };

  /** Abort in-flight OCR poll/fetch and best-effort cancel backend ScanJob. */
  const abortScanWork = () => {
    const ctrl = abortRef.current;
    abortRef.current = null;
    try { ctrl?.abort(); } catch { /* */ }
    const jid = jobIdRef.current;
    jobIdRef.current = null;
    if (jid) {
      // Fire-and-forget — do not await on unmount/pagehide (would hang the refresh).
      cancelScanJob(jid).catch(() => {});
    }
  };

  // Attach stream after <video> mounts (getUserMedia often resolves before React paints it).
  useEffect(() => {
    if (videoRef.current && stream) {
      videoRef.current.srcObject = stream;
      videoRef.current.play?.().catch(() => {});
    }
  }, [stream]);

  useEffect(() => {
    aliveRef.current = true;
    listCategories().then(setCats).catch(() => {});
    listTaxRates().then((r) => {
      if (!aliveRef.current) return;
      setTaxes(r);
      const tva7 = r.find((x) => Number(x.rate_percent) === 7);
      const d = tva7 || r.find((x) => x.is_default);
      if (d) setForm((f) => ({ ...f, tax_rate: f.tax_rate || d.id }));
    }).catch(() => {});

    const onPageHide = () => {
      abortScanWork();
      stopCamera();
      setBarcodeCamera(false);
    };
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("beforeunload", onPageHide);

    return () => {
      aliveRef.current = false;
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("beforeunload", onPageHide);
      abortScanWork();
      stopCamera();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onBarcodeScan = (code) => {
    const raw = String(code || "").replace(/[-\s]/g, "").trim();
    if (!raw) return;
    setBarcodeCamera(false);
    setError("");
    setInfo(`${t("codeDetected")}: ${raw}`);
    setForm((f) => {
      const next = { ...f, barcode_raw: raw };
      if (isbn13CheckOk(raw)) {
        next.isbn13 = raw;
        next.barcode_kind = "isbn13";
      } else if (raw.startsWith("619")) {
        next.barcode_kind = "local_product";
        next.edition_kind = f.edition_kind || "school_cnp";
      }
      return next;
    });
  };

  const addCapture = (file, slot) => {
    setImages((prev) => {
      const next = [...prev];
      while (next.length <= slot) next.push(null);
      next[slot] = file;
      return next;
    });
    setPreviews((prev) => {
      const next = [...prev];
      while (next.length <= slot) next.push(null);
      if (next[slot]) URL.revokeObjectURL(next[slot]);
      next[slot] = URL.createObjectURL(file);
      return next;
    });
  };

  const listCameras = async () => {
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      setCameras(devs.filter((d) => d.kind === "videoinput"));
    } catch { /* unsupported */ }
  };

  const startCamera = async (deviceId) => {
    setError("");
    try {
      stopCamera();
      const s = await openBookCameraStream(deviceId || undefined);
      await bumpTrackResolution(s);
      streamRef.current = s;
      setStream(s);
      if (videoRef.current) videoRef.current.srcObject = s;
      if (deviceId) {
        setCameraId(deviceId);
        localStorage.setItem("teyssir_camera", deviceId);
      } else {
        const track = s.getVideoTracks?.()?.[0];
        const settingsId = track?.getSettings?.()?.deviceId;
        if (settingsId) {
          setCameraId(settingsId);
          localStorage.setItem("teyssir_camera", settingsId);
        }
      }
      await listCameras();
    } catch {
      setError(t("cameraUnavailable"));
    }
  };

  const capture = () => {
    const v = videoRef.current;
    if (!v) return;
    if (!v.videoWidth || !v.videoHeight) {
      setError(t("cameraNotReady"));
      return;
    }
    const canvas = document.createElement("canvas");
    // Use native stream resolution (requested via getUserMedia ideal/max).
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
    const slot = captureStep === "back" ? 1 : 0;
    const name = captureStep === "back" ? `back-${Date.now()}.jpg` : `front-${Date.now()}.jpg`;
    const nextStep = captureStep === "front" ? "back" : "ready";
    // CRITICAL: release webcam before async JPEG encode (recto + verso).
    stopCamera();
    canvas.toBlob((blob) => {
      if (!blob) {
        setError(t("cameraNotReady"));
        return;
      }
      addCapture(new File([blob], name, { type: "image/jpeg" }), slot);
      if (nextStep === "back") {
        setCaptureStep("back");
        setInfo(t("captureBackHint"));
      } else {
        setCaptureStep("ready");
        setInfo(t("capturesReady"));
      }
    }, "image/jpeg", 0.95);
  };

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const resetCaptures = () => {
    previews.forEach((u) => u && URL.revokeObjectURL(u));
    setImages([]);
    setPreviews([]);
    setCaptureStep("front");
    setInfo("");
    setDraft(null);
    stopCamera();
  };

  const stageLabel = (stage) => {
    if (!stage) return t("analyzingBook");
    const key = `scanStage_${stage}`;
    const label = t(key);
    return label === key ? t("analyzingBook") : label;
  };

  const applyScanProgress = (job) => {
    if (!job || !aliveRef.current) return;
    if (job.stage) {
      setScanStage(job.stage);
      setBusyLabel(stageLabel(job.stage));
    }
    if (job.progress != null) setScanProgress(Number(job.progress) || 0);
  };

  const analyze = async ({ force = false } = {}) => {
    const files = images.filter(Boolean);
    if (!files.length) return;
    // Always release the webcam before / during OCR (dock Camera stays open otherwise).
    stopCamera();
    setBarcodeCamera(false);
    abortScanWork();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    jobIdRef.current = null;
    setCaptureStep("ready");
    setBusy(true);
    setBusyLabel(t("analyzingBook"));
    setScanProgress(0);
    setScanStage("queued");
    setError("");
    setInfo("");
    try {
      if (!force) {
        setBusyLabel(t("checkingImageQuality"));
        const q = await assessCapturesQuality(files);
        if (ctrl.signal.aborted || !aliveRef.current) return;
        if (q.blurry || q.lowContrast) {
          setError(t("imageBlurryRetry"));
          setBusy(false);
          setBusyLabel("");
          return;
        }
        if (q.bookTooSmall) {
          setError(t("bookCloserShot"));
          setInfo(t("bookCloserShotHint"));
          setBusy(false);
          setBusyLabel("");
          return;
        }
      }
      setBusyLabel(t("detectingIsbn"));
      const detected = await detectIsbnFromImages(files);
      if (ctrl.signal.aborted || !aliveRef.current) return;
      const isbnHint = detected && isbn13CheckOk(detected) ? detected : "";
      setBusyLabel(t("runningOcr"));
      let d = await scanBook(files, isbnHint, { signal: ctrl.signal });
      if (ctrl.signal.aborted || !aliveRef.current) return;
      if (d.job_id) jobIdRef.current = d.job_id;
      if (d.status === "pending") {
        applyScanProgress(d);
        setBusyLabel(stageLabel(d.stage) || t("waitingOcr"));
        d = await pollScanJob(d.job_id, { onProgress: applyScanProgress, signal: ctrl.signal });
      } else {
        applyScanProgress(d);
      }
      if (ctrl.signal.aborted || !aliveRef.current) return;
      jobIdRef.current = null;
      if (d.status === "failed") {
        throw new Error(d.error || t("ocrFailed"));
      }
      setDraft(d);

      const serverIsbn = d.isbn13 && isbn13CheckOk(d.isbn13) ? d.isbn13 : "";
      const resolvedIsbn = serverIsbn
        || (!d.raw?.isbn_unconfirmed && !d.raw?.isbn_not_detected && isbnHint ? isbnHint : "");
      const isbnMissing = !resolvedIsbn;
      const hasProductBarcode = !!(
        (d.barcode_raw && String(d.barcode_raw).trim())
        || d.barcode_kind === "local_product"
        || d.raw?.barcode_detected
        || d.raw?.barcode_non_isbn
      );
      const isSchoolEdition = looksLikeSchoolDraft(d) || hasProductBarcode;
      const isIsbnEdition = !!(
        resolvedIsbn
        || d.edition_kind === "isbn_edition"
        || (d.barcode_kind === "isbn13" && !isSchoolEdition)
      );
      const bookFill = Number(d.raw?.book_fill_ratio);
      const bookTooSmall = !!(
        d.raw?.book_too_small
        || (Number.isFinite(bookFill) && bookFill < 0.22)
      );
      const bookFillsFrame = !bookTooSmall;
      // ISBN recrop ONLY when book fills frame, verso likely has ISBN, decode failed
      const showIsbnCloseup = isbnMissing && !hasProductBarcode && !isSchoolEdition
        && bookFillsFrame
        && !!(d.raw?.verso_isbn_band_likely || d.raw?.isbn_band_visible);
      const priceMissing = !d.price;
      const ocrErr = d.raw?.ocr_error || d.raw?.back?.ocr_error;
      const lowConf = d.raw?.ocr_low_confidence || (d.confidence != null && d.confidence < 0.35);
      const garbageLatin = !!(d.raw?.ocr_garbage_latin || d.raw?.ocr_arabic_likely || d.raw?.ocr_title_unusable);
      const garbageArabic = !!(d.raw?.ocr_garbage_arabic);
      const garbageOcr = garbageLatin || garbageArabic;
      const missingAra = !!(d.raw?.tess_missing_ara || (d.raw?.tess_missing_langs || []).includes?.("ara"));
      const weakTitleSearch = d.raw?.title_search_weak || (d.raw?.title_search && isbnMissing);
      const metaOk = !!(resolvedIsbn && (d.source === "openlibrary" || d.source === "googlebooks" || d.confidence >= 0.8));
      const weakDistant = bookTooSmall || garbageOcr || (lowConf && !d.title) || !!d.raw?.manual_assist;
      let warn = "";
      let note = "";
      if (ocrErr) {
        warn = `${t("ocrUnavailable")}\n${ocrErr}\n${t("imageBlurryRetry")}`;
      } else if (isSchoolEdition && isbnMissing) {
        // Success path for Tunisian school editions — NEVER "ISBN non détecté" / Recadrez ISBN
        note = hasProductBarcode ? t("localBarcodeDetected") : t("schoolEditionNoIsbn");
        if (d.title) {
          note = `${note}\n${t("schoolEditionTitleHint")}`;
        } else if (d.raw?.suggested_title || garbageOcr) {
          note = `${note}\n${t("noIsbnTitleAssist")}`;
        }
        if (garbageOcr && !hasProductBarcode) {
          note = note ? `${note}\n${t("ocrWeak")}` : t("ocrWeak");
        }
        if (bookTooSmall) {
          note = note ? `${note}\n${t("bookCloserShot")}` : t("bookCloserShot");
        }
      } else if (bookTooSmall || (weakDistant && !hasProductBarcode && isbnMissing && !metaOk)) {
        // Distant / tiny book in frame — closer shot is the primary advice (not ISBN recrop)
        warn = t("bookCloserShot");
        if (garbageOcr && (d.raw?.rejected_title || d.raw?.suggested_title)) {
          note = `${t("ocrGarbageTitleHint")} — ${d.raw.rejected_title || d.raw.suggested_title}`;
        } else if (lowConf) {
          note = t("ocrWeak");
        }
      } else if (garbageOcr || missingAra) {
        warn = missingAra ? t("ocrMissingAra") : t("ocrArabicWeak");
        if (d.raw?.rejected_title || d.raw?.suggested_title) {
          note = `${t("ocrGarbageTitleHint")} — ${d.raw.rejected_title || d.raw.suggested_title}`;
        }
        if (hasProductBarcode) {
          note = note ? `${note}\n${t("localBarcodeDetected")}` : t("localBarcodeDetected");
        } else if (showIsbnCloseup) {
          note = note ? `${note}\n${t("barcodeCloseupHint")}` : t("barcodeCloseupHint");
        } else if (!d.title) {
          note = note ? `${note}\n${t("bookCloserShot")}` : t("bookCloserShot");
        }
      } else if (lowConf && !d.title && !resolvedIsbn && !hasProductBarcode && !isSchoolEdition) {
        warn = t("bookCloserShot");
      } else if (lowConf && !metaOk && !hasProductBarcode && !isSchoolEdition) {
        note = `${t("ocrWeak")}${d.raw?.suggested_title ? ` — ${d.raw.suggested_title}` : ""}`;
        if (showIsbnCloseup) {
          note = `${note}\n${t("barcodeCloseupHint")}`;
        } else if (isbnMissing) {
          note = `${note}\n${t("bookCloserShot")}`;
        }
      } else if (isbnMissing && hasProductBarcode) {
        // Local CNP / product barcode without ISBN — success path, never ask to recrop ISBN
        note = t("localBarcodeDetected");
        if (!d.title && (d.raw?.suggested_title || garbageOcr)) {
          note = `${note}\n${t("noIsbnTitleAssist")}`;
        }
      } else if (isbnMissing && d.title && !isSchoolEdition) {
        warn = `${t("isbnNotDetected")}\n${t("noIsbnTitleAssist")}`;
        if (showIsbnCloseup) warn = `${warn}\n${t("barcodeCloseupHint")}`;
      } else if (isbnMissing && !isSchoolEdition) {
        warn = `${t("isbnNotDetected")}\n${t("isbnManualHint")}`;
        if (showIsbnCloseup) warn = `${warn}\n${t("barcodeCloseupHint")}`;
        else warn = `${warn}\n${t("bookCloserShot")}`;
      } else if (d.raw?.metadata_miss && !d.title) {
        warn = t("metadataMiss");
      } else if (d.source === "manual" || d.raw?.ocr_available === false) {
        warn = t("ocrUnavailable");
      } else if (!d.title && !resolvedIsbn && !hasProductBarcode && !isSchoolEdition) {
        warn = t("ocrEmptyManual");
      } else if (weakTitleSearch) {
        note = t("titleSearchWeak");
      } else if (d.raw?.title_search) {
        note = t("titleSearchHit");
      }
      if (!isbnMissing && priceMissing) {
        note = note ? `${note}\n${t("priceNotDetected")}` : t("priceNotDetected");
      } else if (isbnMissing && (hasProductBarcode || isSchoolEdition) && priceMissing) {
        note = note ? `${note}\n${t("priceNotDetected")}` : t("priceNotDetected");
      }
      if (d.raw?.suggested_isbn && isbnMissing && !hasProductBarcode && !isSchoolEdition) {
        note = note
          ? `${note}\n${t("isbnManualHint")} (${d.raw.suggested_isbn})`
          : `${t("isbnManualHint")} (${d.raw.suggested_isbn})`;
      }
      setError(warn);
      setInfo(note);

      const livre = cats.find((c) => /livre|book|كتاب|manuel/i.test(c.name_fr || ""));
      const tva7 = taxes.find((x) => Number(x.rate_percent) === 7);
      // Do not present garbage OCR as a confident title — even for school CNP.
      // Backend may repair to Mathématiques; keep only usable / repaired titles.
      const titleLooksRepaired = /Mathématiques|Mathematiques|Histoire|Technologie|التاريخ|Sciences|Physique|Français/i
        .test(d.title || "");
      const safeTitle = (garbageOcr || !!d.raw?.ocr_title_unusable) && !titleLooksRepaired
        ? ""
        : (d.title || "");
      const rawAuthors = (garbageOcr || !!d.raw?.ocr_title_unusable) && !titleLooksRepaired
        ? ""
        : (d.authors || []).join(", ");
      const safeAuthors = scrubAuthors(rawAuthors, safeTitle);
      let langList = d.languages || [];
      if (isSchoolEdition) {
        langList = langList.filter((x) => x !== "en");
        if (!langList.includes("fr") && !langList.includes("ar")) langList = ["fr"];
        else if (!langList.includes("fr") && langList.includes("ar")) langList = ["ar", "fr"];
      } else if (garbageOcr && !langList.includes("ar")) {
        // Don't invent ``ar`` for Latin mush — clear fake ``en`` instead
        langList = langList.filter((x) => x !== "en");
      }
      // Drop junk "Book: nologie…" descriptions
      let safeDesc = d.description || "";
      if (/^(Book|Livre)\s*[:/].*(nologie|ématiques|matiques)/i.test(safeDesc)) {
        safeDesc = "";
      }
      const editionKind = d.edition_kind
        || (isSchoolEdition ? "school_cnp" : (isIsbnEdition ? "isbn_edition" : ""));
      setForm((prev) => ({
        ...EMPTY,
        isbn13: resolvedIsbn,
        barcode_raw: d.barcode_raw || "",
        barcode_symbology: d.barcode_symbology || "",
        barcode_kind: d.barcode_kind || (isSchoolEdition && (d.barcode_raw || "").startsWith("619") ? "local_product" : ""),
        edition_kind: editionKind,
        title: safeTitle,
        subtitle: d.subtitle || "",
        authors: safeAuthors,
        translators: (d.translators || []).join(", "),
        publisher: d.publisher || "",
        series: d.series || "",
        edition: d.edition || "",
        pub_year: d.pub_year || "",
        pages: d.pages || "",
        languages: langList.join(", "),
        subject: d.subject || "",
        description: safeDesc,
        sale_price: garbageLatin && !d.raw?.price_detected && !isSchoolEdition ? "" : (d.price || ""),
        category: prev.category || livre?.id || "",
        tax_rate: prev.tax_rate || tva7?.id || "",
      }));
    } catch (err) {
      if (err?.name === "AbortError" || String(err?.message || err) === "cancelled") {
        // User left / refreshed — do not surface an error or leave busy=true.
        return;
      }
      if (!aliveRef.current) return;
      const msg = String(err.message || err);
      setError(/ocr|tesseract|empty|fail|échec/i.test(msg) ? `${t("imageBlurryRetry")}\n${msg}` : msg);
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
      if (!aliveRef.current) return;
      stopCamera();
      setBusy(false);
      setBusyLabel("");
      setScanProgress(0);
      setScanStage("");
    }
  };

  const save = async () => {
    setBusy(true);
    setBusyLabel(t("saving"));
    setError("");
    try {
      await createBook({
        ...form,
        authors: form.authors ? form.authors.split(",").map((s) => s.trim()).filter(Boolean) : [],
        translators: form.translators ? form.translators.split(",").map((s) => s.trim()).filter(Boolean) : [],
        languages: form.languages ? form.languages.split(",").map((s) => s.trim()).filter(Boolean) : [],
        pub_year: form.pub_year ? Number(form.pub_year) : null,
        pages: form.pages ? Number(form.pages) : null,
        category: form.category || null,
        tax_rate: form.tax_rate || null,
        source: draft?.source || "manual",
        confidence: draft?.confidence || 0,
        raw: draft?.raw || {},
        image_ids: draft?.image_ids || [],
      });
      setDone(true);
      resetCaptures();
      setForm((f) => ({ ...EMPTY, tax_rate: f.tax_rate, category: f.category }));
    } catch (err) {
      setError(String(err.message || err));
    } finally {
      setBusy(false);
      setBusyLabel("");
    }
  };

  const F = (label, key, props = {}) => (
    <TextField size="small" fullWidth label={label} value={form[key]}
               onChange={(e) => set(key, e.target.value)}
               inputProps={{ dir: "auto", ...(props.inputProps || {}) }}
               {...props} />
  );

  const stepIndex = captureStep === "front" ? 0 : captureStep === "back" ? 1 : 2;
  const captureLabel = captureStep === "back" ? t("captureBack") : t("captureFront");
  const readyFiles = images.filter(Boolean).length;
  const schoolMode = looksLikeSchoolDraft(draft) || form.edition_kind === "school_cnp"
    || (form.barcode_raw || "").startsWith("619") || form.barcode_kind === "local_product";
  const isbnMode = !schoolMode && (!!form.isbn13 || form.edition_kind === "isbn_edition"
    || (draft && draft.edition_kind === "isbn_edition"));

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "#f5f5f5" }}>
      <AppBar position="static" color="primary">
        <Toolbar sx={{ gap: 2 }}>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>{t("newBook")}</Typography>
          <Button color="inherit" onClick={onBack}>{t("pos")}</Button>
          <LangToggle />
          <Button color="inherit" onClick={onLogout}>{t("logout")}</Button>
        </Toolbar>
      </AppBar>

      {busy && (
        <Box sx={{ px: 2, pt: 1 }}>
          <LinearProgress variant={scanProgress > 0 ? "determinate" : "indeterminate"}
                          value={scanProgress} />
          {busyLabel && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
              {busyLabel}{scanProgress > 0 ? ` — ${scanProgress}%` : ""}
            </Typography>
          )}
        </Box>
      )}
      <Box sx={{ p: 2 }}>
        {error && (
          <Alert severity="warning" sx={{ mb: 2, whiteSpace: "pre-line" }}>{error}</Alert>
        )}
        {info && !error && (
          <Alert severity="info" sx={{ mb: 2 }} onClose={() => setInfo("")}>{info}</Alert>
        )}
        <Grid container spacing={2}>
          <Grid item xs={12} md={5}>
            <Paper sx={{ p: 2 }}>
              <Stepper activeStep={Math.min(stepIndex, 2)} alternativeLabel sx={{ mb: 2 }}>
                <Step completed={Boolean(images[0])}><StepLabel>{t("stepFront")}</StepLabel></Step>
                <Step completed={Boolean(images[1])}><StepLabel>{t("stepBack")}</StepLabel></Step>
                <Step completed={Boolean(draft)}><StepLabel>{t("stepAnalyze")}</StepLabel></Step>
              </Stepper>

              <Typography color="text.secondary" sx={{ mb: 1 }}>
                {captureStep === "front" && t("captureFrontHint")}
                {captureStep === "back" && (schoolMode ? t("captureBackHintSchool") : t("captureBackHint"))}
                {captureStep === "ready" && t("capturesReady")}
              </Typography>

              {stream && (
                <Box sx={{
                  position: "relative", bgcolor: "#000", borderRadius: 1, overflow: "hidden",
                  mb: 1, minHeight: 180,
                }}>
                  <video
                    ref={videoRef}
                    autoPlay
                    playsInline
                    muted
                    style={{ width: "100%", display: "block" }}
                  />
                  {/* Book-aspect guide: align cover inside the portrait frame */}
                  <Box
                    aria-hidden
                    sx={{
                      position: "absolute",
                      inset: 0,
                      pointerEvents: "none",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <Box
                      sx={{
                        width: "52%",
                        maxWidth: 280,
                        aspectRatio: BOOK_ASPECT,
                        maxHeight: "90%",
                        border: "2px solid rgba(255,255,255,0.92)",
                        borderRadius: 1,
                        boxShadow: "0 0 0 9999px rgba(0,0,0,0.42)",
                      }}
                    />
                    <Typography
                      variant="caption"
                      sx={{
                        position: "absolute",
                        bottom: 8,
                        left: 0,
                        right: 0,
                        textAlign: "center",
                        color: "#fff",
                        textShadow: "0 1px 2px #000",
                        px: 1,
                      }}
                    >
                      {captureStep === "back"
                        ? (schoolMode ? t("captureBackHintSchool") : t("captureBackHint"))
                        : t("captureFrontHint")}
                    </Typography>
                  </Box>
                </Box>
              )}
              {stream && cameras.length > 1 && (
                <Select size="small" fullWidth sx={{ mb: 1 }}
                        value={cameraId || cameras[0]?.deviceId || ""}
                        onChange={(e) => startCamera(e.target.value)}>
                  {cameras.map((c, i) => (
                    <MenuItem key={c.deviceId} value={c.deviceId}>{c.label || `${t("camera")} ${i + 1}`}</MenuItem>
                  ))}
                </Select>
              )}
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                {!stream ? (
                  <Button variant="outlined" onClick={() => startCamera(cameraId || undefined)}
                          disabled={captureStep === "ready" && readyFiles >= 2}>
                    {captureStep === "back" ? t("startCameraBack") : t("startCamera")}
                  </Button>
                ) : (
                  <>
                    <Button variant="contained" onClick={capture}>{captureLabel}</Button>
                    <Button variant="outlined" color="error" onClick={stopCamera}>{t("stopCamera")}</Button>
                  </>
                )}
                <Button variant="outlined" onClick={() => fileRef.current?.click()}>{t("choosePhotos")}</Button>
                <input ref={fileRef} type="file" accept="image/*" capture="environment" multiple hidden
                       onChange={(e) => {
                         const files = [...e.target.files];
                         files.forEach((f, i) => addCapture(f, Math.min(i, 1)));
                         stopCamera();
                         if (files.length >= 2) setCaptureStep("ready");
                         else if (files.length === 1) setCaptureStep("back");
                         e.target.value = "";
                       }} />
                <Button variant="contained" color="secondary"
                        disabled={!readyFiles || busy} onClick={() => analyze({ force: false })}>
                  {t("analyze")}
                </Button>
                {readyFiles > 0 && error && /floue|blur|contraste|Image floue/i.test(error) && (
                  <Button variant="outlined" color="warning" disabled={busy}
                          onClick={() => analyze({ force: true })}>
                    {t("analyzeAnyway")}
                  </Button>
                )}
                {readyFiles > 0 && (
                  <Button color="inherit" onClick={resetCaptures}>{t("resetCaptures")}</Button>
                )}
              </Stack>
              <Stack direction="row" spacing={1} sx={{ mt: 2, overflowX: "auto" }}>
                {previews.map((src, i) => src && (
                  <Box key={i} sx={{ textAlign: "center" }}>
                    <img src={src} alt="" style={{ height: 84, borderRadius: 6, display: "1px solid #ccc" }} />
                    <Typography variant="caption" display="block">
                      {i === 0 ? t("stepFront") : t("stepBack")}
                    </Typography>
                  </Box>
                ))}
                {(draft?.images || []).map((im) => (
                  <img key={im.id} src={im.url} alt="" style={{ height: 84, borderRadius: 6 }} />
                ))}
              </Stack>
            </Paper>
          </Grid>

          <Grid item xs={12} md={7}>
            <Paper sx={{ p: 2 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
                <Typography variant="h6">{t("bookTitle")}</Typography>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                  {draft && (
                    <Chip size="small" color={draft.confidence >= 0.8 ? "success" : draft.confidence > 0 ? "warning" : "default"}
                          label={`${t("confidence")}: ${Math.round((draft.confidence || 0) * 100)}% · ${draft.source || "—"}`} />
                  )}
                  {draft?.language_detected && (
                    <Chip size="small" color="info" variant="outlined"
                          label={`${t("languageDetected")}: ${
                            schoolMode
                              ? ((form.languages || "fr").split(",")[0].trim() || "fr")
                              : draft.language_detected
                          }`} />
                  )}
                </Stack>
              </Stack>
              <Grid container spacing={1.5}>
                <Grid item xs={12}>
                  <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ mb: 0.5 }}>
                    <Typography color="text.secondary" sx={{ flexGrow: 1, pt: 0.5 }}>
                      {t("scanBookBarcodeHint")}
                    </Typography>
                    <Button
                      variant={barcodeCamera ? "contained" : "outlined"}
                      size="small"
                      sx={{ minWidth: 52 }}
                      disabled={busy}
                      onClick={() => {
                        if (!barcodeCamera) stopCamera();
                        setBarcodeCamera((c) => !c);
                      }}
                      aria-label={t("scanWithCamera")}
                    >
                      📷
                    </Button>
                  </Stack>
                  {barcodeCamera && (
                    <CameraScanner
                      mode="pos"
                      stopOnDetect
                      onDetect={onBarcodeScan}
                      onClose={() => setBarcodeCamera(false)}
                    />
                  )}
                </Grid>
                {schoolMode ? (
                  <>
                    <Grid item xs={12} sm={6}>
                      {F(t("barcodeFieldLabel"), "barcode_raw")}
                    </Grid>
                    <Grid item xs={12} sm={6}>
                      {F(t("isbnFieldLabel"), "isbn13", {
                        helperText: t("isbnOptionalHint"),
                        FormHelperTextProps: { sx: { mx: 0 } },
                      })}
                    </Grid>
                  </>
                ) : (
                  <>
                    <Grid item xs={12} sm={6}>{F(t("isbnFieldLabel"), "isbn13")}</Grid>
                    <Grid item xs={12} sm={6}>{F(isbnMode ? "Code-barres" : t("barcodeFieldLabel"), "barcode_raw")}</Grid>
                  </>
                )}
                <Grid item xs={12} sm={6}>{F(t("priceF") + " (DT)", "sale_price", {
                  type: "number", inputProps: { min: 0, step: "0.001" },
                })}</Grid>
                <Grid item xs={12} sm={6}>
                  <FormControl fullWidth size="small">
                    <InputLabel>{t("category")}</InputLabel>
                    <Select label={t("category")} value={form.category}
                            onChange={(e) => set("category", e.target.value)}>
                      <MenuItem value="">{t("none")}</MenuItem>
                      {cats.map((c) => <MenuItem key={c.id} value={c.id}>{c.name_fr}</MenuItem>)}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <FormControl fullWidth size="small">
                    <InputLabel>{t("taxRate")}</InputLabel>
                    <Select label={t("taxRate")} value={form.tax_rate}
                            onChange={(e) => set("tax_rate", e.target.value)}>
                      {taxes.map((x) => (
                        <MenuItem key={x.id} value={x.id}>{x.name} ({x.rate_percent}%)</MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Grid>
                <Grid item xs={12}>{F(t("bookTitle"), "title")}</Grid>
                <Grid item xs={12}>{F(t("subtitleF"), "subtitle")}</Grid>
                <Grid item xs={12} sm={6}>{F(t("authorsF"), "authors")}</Grid>
                <Grid item xs={12} sm={6}>{F(t("translatorsF"), "translators")}</Grid>
                <Grid item xs={12} sm={6}>{F(t("publisherF"), "publisher")}</Grid>
                <Grid item xs={6} sm={3}>{F(t("yearF"), "pub_year", { type: "number" })}</Grid>
                <Grid item xs={6} sm={3}>{F(t("pagesF"), "pages", { type: "number", inputProps: { step: 1 } })}</Grid>
                <Grid item xs={12} sm={6}>{F(t("languagesF"), "languages")}</Grid>
                <Grid item xs={12} sm={6}>{F(t("subjectF"), "subject")}</Grid>
                <Grid item xs={12}>{F(t("descriptionF"), "description", { multiline: true, rows: 4 })}</Grid>
              </Grid>
              <Button variant="contained" size="large" sx={{ mt: 2 }} disabled={!form.title || busy} onClick={save}>
                {t("saveBook")}
              </Button>
            </Paper>
          </Grid>
        </Grid>
      </Box>

      <Snackbar open={done} autoHideDuration={5000} onClose={() => setDone(false)}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setDone(false)}>{t("bookSaved")}</Alert>
      </Snackbar>
    </Box>
  );
}
