#!/usr/bin/env python3
"""Momentum watchlist robot.

Finds Kev's newest "TOP 3 STOCKS ... TRADING DAY" reel on YouTube Shorts or TikTok,
reads the on-screen text (OCR) and the spoken plan (speech-to-text), and writes
data/latest.json + data/<date>.json (+ a few key frames) for the scheduled Claude run.

Usage:
  python robot.py                       # normal nightly run
  python robot.py --local-video x.mp4   # test the reading part on a local file
  python robot.py --force               # re-run even if today's list is already saved
"""
import argparse, collections, datetime as dt, glob, json, os, re, shutil, subprocess, sys, urllib.request
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
WORK = os.path.join(ROOT, "work")

SOURCES = [
    ("youtube", "https://www.youtube.com/@trade.momentum/shorts"),
    ("tiktok", "https://www.tiktok.com/@momentum.official"),
]

# NYSE full-day closures
HOLIDAYS = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19",
    "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
    "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

# Words that look like tickers on Kev's charts but are not his picks
STOP = set("""
A I AM PM ET PT THE FOR AND TOP STOCK STOCKS DAY TRADING TRADE WATCH LIST WATCHLIST TODAY TOMORROW
MONDAY TUESDAY WEDNESDAY THURSDAY FRIDAY MON TUE TUES WED THU THUR THURS FRI
VWAP HOD LOD EMA SMA MACD RSI VOL VOLUME HIGH LOW OPEN CLOSE PRE POST MARKET PREMARKET AH
BUY SELL ENTRY TARGET TARGETS STOP RISK GAP GAPS NEWS FLOAT SHARES BREAK BREAKOUT SUPPORT RESIST
RESISTANCE LEVEL LEVELS KEY HOLD ABOVE BELOW OVER UNDER IF THEN NEXT PRIOR NEW ALL TIME ATH
USD NYSE NASDAQ OTC IPO CEO FDA SEC EPS UP DOWN ON IN OF TO IS IT MY ME WE YOU GO NOW
MOMENTUM KEV FOLLOW LIKE SHARE COMMENT FREE TRIAL LINK BIO DM TV ON CHART MIN HR SEC
""".split())


def log(*a):
    print(*a, flush=True)


def next_trading_day(d):
    d = d + dt.timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in HOLIDAYS:
        d += dt.timedelta(days=1)
    return d


def run(cmd, timeout=900):
    log("$", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def load_symbols():
    """Official US listed symbols, used to throw away OCR noise."""
    syms = set()
    for url in ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
                "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"):
        try:
            txt = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "ignore")
            for line in txt.splitlines()[1:]:
                s = line.split("|")[0].strip()
                if s.isalpha():
                    syms.add(s)
        except Exception as e:
            log("symbol list failed:", url, e)
    return syms


def title_matches(title, target):
    t = (title or "").upper()
    if "TOP" not in t:
        return None
    dates = {f"{target.month}/{target.day}/{target:%y}", f"{target.month}/{target.day}/{target.year}",
             f"{target:%m}/{target:%d}/{target:%y}", f"{target.month}/{target.day} "}
    if any(x in t for x in dates):
        return "strong"
    if target.strftime("%A").upper() in t:
        return "weak"
    return None


def find_reel(target, sources=SOURCES):
    errors = []
    for name, url in sources:
        r = run(["yt-dlp", "--flat-playlist", "-J", "--playlist-end", "8", url], timeout=180)
        if r.returncode != 0:
            errors.append(f"{name}: listing failed ({(r.stderr or '').strip().splitlines()[-1:] or ['?']})")
            continue
        try:
            entries = json.loads(r.stdout).get("entries") or []
        except Exception as e:
            errors.append(f"{name}: bad listing ({e})")
            continue
        best = None
        for i, e in enumerate(entries):
            title = e.get("title") or e.get("description") or ""
            m = title_matches(title, target)
            if m == "strong" or (m == "weak" and i < 3 and best is None):
                link = e.get("url") or e.get("webpage_url")
                if link and not link.startswith("http"):
                    link = f"https://www.youtube.com/shorts/{e.get('id')}" if name == "youtube" else link
                best = {"source": name, "url": link, "title": title, "match": m}
                if m == "strong":
                    break
        if best:
            return best, errors
        errors.append(f"{name}: no reel titled for {target:%A %-m/%-d} among the newest {len(entries)} posts")
    return None, errors


YT_CLIENTS = [None, "youtube:player_client=tv_embedded,web_embedded", "youtube:player_client=ios,mweb",
              "youtube:player_client=tv,web_safari"]


def download(url, source):
    """Try a few download variants; YouTube often bot-checks GitHub's servers."""
    last = "?"
    for extra in (YT_CLIENTS if source == "youtube" else [None]):
        shutil.rmtree(WORK, ignore_errors=True)
        os.makedirs(WORK)
        out = os.path.join(WORK, "video.%(ext)s")
        cmd = ["yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b", "--merge-output-format", "mp4", "-o", out]
        if extra:
            cmd += ["--extractor-args", extra]
        r = run(cmd + [url], timeout=600)
        files = glob.glob(os.path.join(WORK, "video.*"))
        if r.returncode == 0 and files:
            return files[0]
        last = ((r.stderr or "").strip().splitlines() or ["?"])[-1][:200]
    raise RuntimeError(f"{source} download failed: {last}")


def ocr_frames(video):
    from PIL import Image, ImageOps
    fdir = os.path.join(WORK, "frames")
    os.makedirs(fdir, exist_ok=True)
    run(["ffmpeg", "-loglevel", "error", "-i", video, "-vf", "fps=1", os.path.join(fdir, "f_%04d.png")])
    frames = sorted(glob.glob(os.path.join(fdir, "f_*.png")))
    results = []
    for f in frames:
        img = Image.open(f).convert("L")
        if img.width < 900:
            img = img.resize((img.width * 2, img.height * 2))
        texts = []
        for variant in (img, ImageOps.invert(img)):
            p = f + ".tmp.png"
            variant.save(p)
            r = run(["tesseract", p, "stdout", "--psm", "11"], timeout=60)
            texts.append(r.stdout)
        results.append({"t": int(os.path.basename(f)[2:6]) - 1, "file": f, "text": "\n".join(texts)})
    return results


def transcribe(video):
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        return None, f"speech-to-text unavailable ({e})"
    try:
        model = WhisperModel("base.en", device="cpu", compute_type="int8")
        segs, _ = model.transcribe(video, beam_size=1, vad_filter=True)
        return [{"t": round(s.start, 1), "text": s.text.strip()} for s in segs], None
    except Exception as e:
        return None, f"speech-to-text failed ({e})"


TOKEN = re.compile(r"\$?\b[A-Z]{2,5}\b")
PRICE = re.compile(r"\$\s?\d{1,4}(?:\.\d{1,3})?|\b\d{1,3}\.\d{1,3}\b")


def pick_tickers(frames, transcript, symbols):
    seen = collections.defaultdict(set)
    for fr in frames:
        for tok in TOKEN.findall(fr["text"]):
            s = tok.lstrip("$")
            if s in STOP or (symbols and s not in symbols):
                continue
            seen[s].add(fr["t"])
    spoken = collections.Counter()
    if transcript:
        full = " ".join(x["text"] for x in transcript)
        for s in seen:
            spelled = r"\b" + r"[\s\.\-]*".join(s) + r"\b"
            spoken[s] = len(re.findall(spelled, full, flags=re.I))
    scored = sorted(seen, key=lambda s: (len(seen[s]) + 3 * spoken[s]), reverse=True)
    cands = [{"ticker": s, "frames": len(seen[s]), "first_second": min(seen[s]), "spoken": spoken[s]}
             for s in scored if len(seen[s]) >= 2 or spoken[s] > 0][:8]
    return cands


def plan_by_ticker(tickers, transcript):
    """Group spoken sentences under the ticker most recently mentioned, and pull prices."""
    out = {t: {"said": [], "prices": []} for t in tickers}
    if not transcript:
        return out
    cur = None
    for seg in transcript:
        for t in tickers:
            if re.search(r"\b" + r"[\s\.\-]*".join(t) + r"\b", seg["text"], flags=re.I):
                cur = t
        if cur:
            out[cur]["said"].append(seg["text"])
            out[cur]["prices"] += PRICE.findall(seg["text"])
    return out


def save_keyframes(frames, cands, date_s):
    from PIL import Image
    d = os.path.join(DATA, "frames", date_s)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    saved = []
    for c in cands[:3]:
        fr = next(f for f in frames if f["t"] == c["first_second"])
        img = Image.open(fr["file"]).convert("RGB")
        img.thumbnail((720, 1280))
        p = os.path.join(d, f"{c['ticker']}.jpg")
        img.save(p, quality=80)
        saved.append(os.path.relpath(p, ROOT))
    return saved


def write(rec):
    os.makedirs(DATA, exist_ok=True)
    for name in (f"{rec['target_date']}.json", "latest.json"):
        with open(os.path.join(DATA, name), "w") as f:
            json.dump(rec, f, indent=2)
    log(json.dumps({k: rec[k] for k in ("target_date", "status", "tickers", "reason") if k in rec}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local-video")
    ap.add_argument("--target")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    now = dt.datetime.now(ET)
    target = dt.date.fromisoformat(a.target) if a.target else next_trading_day(now.date())
    rec = {"target_date": target.isoformat(), "target_label": f"{target:%a} {target:%-m/%-d/%y}",
           "checked_at": now.isoformat(timespec="minutes"), "status": "pending", "tickers": []}

    existing = os.path.join(DATA, f"{target}.json")
    if not a.force and not a.local_video and os.path.exists(existing):
        if json.load(open(existing)).get("status") == "ok":
            log("Already captured", target, "- nothing to do")
            return

    try:
        if a.local_video:
            reel, errors = {"source": "local", "url": a.local_video, "title": "local test", "match": "test"}, []
            video = a.local_video
        else:
            reel, video, errors = None, None, []
            for name, url in SOURCES:
                cand, errs = find_reel(target, [(name, url)])
                errors += errs
                if not cand:
                    continue
                try:
                    video = download(cand["url"], cand["source"])
                    reel = cand
                    break
                except Exception as e:
                    errors.append(str(e))
            rec["source_errors"] = errors
            if not video:
                rec["status"] = "failed" if any("download failed" in e for e in errors) else "pending"
                rec["reason"] = ("Reel found but every download was blocked: " if rec["status"] == "failed"
                                 else "Reel not found yet: ") + "; ".join(errors)
                write(rec)
                return
        rec["reel"] = reel
        frames = ocr_frames(video)
        transcript, terr = transcribe(video)
        if terr:
            rec["transcript_error"] = terr
        symbols = load_symbols()
        cands = pick_tickers(frames, transcript, symbols)
        rec["candidates"] = cands
        rec["tickers"] = [c["ticker"] for c in cands[:3]]
        rec["plan"] = plan_by_ticker(rec["tickers"], transcript)
        rec["transcript"] = transcript
        rec["ocr_lines"] = sorted({ln.strip() for fr in frames for ln in fr["text"].splitlines()
                                   if len(ln.strip()) >= 3 and re.search(r"[A-Za-z$]", ln)})[:400]
        rec["keyframes"] = save_keyframes(frames, cands, target.isoformat()) if cands else []
        if len(rec["tickers"]) >= 3:
            rec["status"] = "ok"
        else:
            rec["status"] = "partial"
            rec["reason"] = f"Only found {len(rec['tickers'])} ticker(s) in the video"
    except Exception as e:
        rec["status"] = "failed"
        rec["reason"] = str(e)[:500]
    write(rec)


if __name__ == "__main__":
    main()
