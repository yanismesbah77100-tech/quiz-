#!/usr/bin/env python3
"""Vidéos verticales « verset + traduction » pour TikTok.

Le texte arabe (édition quran-uthmani), la traduction française (Hamidullah)
et la récitation (Mishary Alafasy) sont récupérés depuis api.alquran.cloud :
aucun texte n'est écrit à la main.

Modes :
  recit       : récitation, texte arabe + traduction affichés
  recit_trad  : pour chaque verset, récitation puis traduction lue en français

Usage :
  python3 coran/generate.py --queue coran/queue.json --out out
  python3 coran/generate.py --demo --out /tmp/x       (test local sans réseau)
"""
import argparse
import asyncio
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import urllib.request
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FPS = 30
SR = 44100
HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.alquran.cloud/v1/ayah/{s}:{a}/editions/quran-uthmani,fr.hamidullah,ar.alafasy"
FR_VOICE = "fr-FR-HenriNeural"
GOLD = (232, 196, 110)
CREAM = (250, 244, 228)
SOFT = (210, 225, 220)


# ---------------------------------------------------------------- polices
def find_font(cands):
    out = subprocess.run(["fc-list", "--format", "%{file}\n"], capture_output=True, text=True).stdout.split("\n")
    for c in cands:
        for f in out:
            if c.lower() in os.path.basename(f).lower():
                return f
    return None


AR_FONT = find_font(["AmiriQuran", "Amiri-Regular", "amiri-regular", "NotoNaskhArabic-Regular", "DejaVuSans.ttf"])
LAT_B = find_font(["DejaVuSans-Bold.ttf"])
LAT = find_font(["DejaVuSans.ttf"])
LAT_SERIF_I = find_font(["DejaVuSerif-Italic", "DejaVuSerif.ttf"]) or LAT


def F(path, size):
    return ImageFont.truetype(path, size)


# ---------------------------------------------------------------- données
def fetch(s, a):
    url = API.format(s=s, a=a)
    for _ in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                d = json.load(r)["data"]
            ar, fr, au = d
            text = ar["text"].strip()
            # l'API colle la basmala au 1er verset de chaque sourate (sauf 1 et 9)
            if a == 1 and s not in (1, 9) and text.startswith("بِسْمِ"):
                text = " ".join(text.split(" ")[4:]).strip()
            return dict(s=s, a=a, ar=text, fr=fr["text"].strip(), audio=au["audio"],
                        surah_ar=ar["surah"]["name"], surah=ar["surah"]["englishName"])
        except Exception as e:
            print("erreur API, nouvel essai :", e, file=sys.stderr)
    raise RuntimeError(f"verset {s}:{a} introuvable")


def download(url, path):
    for _ in range(4):
        try:
            urllib.request.urlretrieve(url, path)
            return path
        except Exception as e:
            print("erreur téléchargement :", e, file=sys.stderr)
    raise RuntimeError("téléchargement impossible : " + url)


def read_audio(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


async def _edge(text, path):
    import edge_tts
    await edge_tts.Communicate(text, FR_VOICE, rate="-5%").save(path)


def tts(text, path):
    for _ in range(3):
        try:
            asyncio.run(_edge(text, path))
            return read_audio(path)
        except Exception as e:
            print("edge-tts erreur :", e, file=sys.stderr)
    from gtts import gTTS
    gTTS(text, lang="fr").save(path)
    return read_audio(path)


# ---------------------------------------------------------------- fond
def star_tile(size=360, alpha=22):
    """Motif géométrique (étoile à 8 branches) répété, très discret."""
    t = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(t)
    c = size / 2
    for rot in (0, 45):
        pts = []
        for k in range(4):
            ang = math.radians(rot + k * 90 - 45)
            pts.append((c + math.cos(ang) * size * 0.32, c + math.sin(ang) * size * 0.32))
        d.polygon(pts, outline=(255, 255, 255, alpha), width=3)
    d.ellipse((c - size * 0.12, c - size * 0.12, c + size * 0.12, c + size * 0.12), outline=(255, 255, 255, alpha), width=3)
    return t


class Background:
    def __init__(self, seed):
        rnd = random.Random(seed)
        hh = H * 2
        t = (np.arange(hh) / hh)[:, None]
        cols = [np.array(c, np.float32) for c in [(6, 22, 34), (8, 52, 58), (14, 70, 56), (10, 30, 44)]]
        ph = (np.sin(t * 2 * np.pi * 2) + 1) / 2
        ph2 = (np.sin(t * 2 * np.pi * 2 + 2.1) + 1) / 2
        col = cols[0] * (1 - ph) + cols[1] * ph
        col = col * (1 - 0.5 * ph2) + cols[2] * 0.5 * ph2
        arr = np.repeat(col[:, None, :], W // 4, axis=1).astype(np.uint8)
        self.grad = Image.fromarray(arr, "RGB").resize((W, hh), Image.BILINEAR)
        tile = star_tile()
        self.pattern = Image.new("RGBA", (W + 360, H + 360), (0, 0, 0, 0))
        for y in range(0, H + 360, 360):
            for x in range(0, W + 360, 360):
                self.pattern.alpha_composite(tile, (x, y))
        # vignette
        vg = Image.new("L", (W, H), 0)
        ImageDraw.Draw(vg).ellipse((-300, -200, W + 300, H + 200), fill=255)
        vg = vg.filter(ImageFilter.GaussianBlur(220))
        self.vignette = Image.composite(Image.new("RGBA", (W, H), (0, 0, 0, 0)), Image.new("RGBA", (W, H), (0, 0, 0, 150)), vg)
        self.stars = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(1.5, 4), rnd.uniform(0, 6.28), rnd.uniform(0.4, 1.2))
                      for _ in range(90)]
        glow = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((150, 150, 450, 450), fill=(232, 196, 110, 40))
        self.glow = glow.filter(ImageFilter.GaussianBlur(90))

    def frame(self, t):
        off = int((t * 25) % H)
        img = self.grad.crop((0, off, W, off + H)).convert("RGBA")
        p = int(t * 12) % 360
        img.alpha_composite(self.pattern.crop((p, p, p + W, p + H)))
        gx = int(W / 2 - 300 + math.sin(t * 0.3) * 200)
        img.alpha_composite(self.glow, (gx, 500))
        d = ImageDraw.Draw(img)
        for x, y, r, ph, sp in self.stars:
            a = int(110 + 110 * math.sin(t * sp + ph))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 250, 230, max(0, a)))
        img.alpha_composite(self.vignette)
        return img


# ---------------------------------------------------------------- mise en page
def wrap(d, text, fnt, maxw, rtl=False):
    kw = dict(direction="rtl", language="ar") if rtl else {}
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if d.textlength(test, font=fnt, **kw) <= maxw:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def text_block(d, lines, fnt, y, lh, fill, rtl=False, stroke=0):
    kw = dict(direction="rtl", language="ar") if rtl else {}
    for line in lines:
        w = d.textlength(line, font=fnt, **kw)
        d.text(((W - w) / 2, y), line, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 120), **kw)
        y += lh
    return y


def layout(ayahs, cfg, highlight_fr=False, idx=None):
    """Calque transparent : en-tête, texte arabe, traduction, pied de page."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    first, last = ayahs[0], ayahs[-1]
    ref = f"{first['s']}:{first['a']}" + (f"-{last['a']}" if last["a"] != first["a"] else "")
    # en-tête
    hf = F(AR_FONT, 64)
    text_block(d, [first["surah_ar"]], hf, 150, 80, GOLD, rtl=True)
    sub = F(LAT_B, 36)
    text_block(d, [f"Sourate {first['surah']}  •  {ref}"], sub, 260, 44, SOFT)
    d.line((W / 2 - 160, 330, W / 2 + 160, 330), fill=GOLD + (180,), width=3)

    shown = ayahs if idx is None else [ayahs[idx]]
    ar = " ".join(f"{a['ar']} ﴿{to_ar_digits(a['a'])}﴾" for a in shown)
    fr = " ".join(a["fr"] for a in shown)
    # taille adaptée : arabe en haut (jusqu'à ~55 % de la place), traduction dessous
    top, bottom = 380, H - 260
    for size in (92, 84, 76, 68, 62, 56, 50, 46, 42):
        af = F(AR_FONT, size)
        al = wrap(d, ar, af, W - 140, rtl=True)
        alh = int(size * 1.75)
        if len(al) * alh <= (bottom - top) * 0.58:
            break
    for fsize in (46, 42, 38, 35, 32, 29, 26):
        ff = F(LAT_SERIF_I, fsize)
        fl = wrap(d, fr, ff, W - 160)
        flh = int(fsize * 1.4)
        if len(al) * alh + 70 + len(fl) * flh <= bottom - top:
            break
    total = len(al) * alh + 70 + len(fl) * flh
    y = top + (bottom - top - total) / 2
    y = text_block(d, al, af, y, alh, CREAM, rtl=True)
    d.line((W / 2 - 60, y + 30, W / 2 + 60, y + 30), fill=GOLD + (150,), width=2)
    text_block(d, fl, ff, y + 70, flh, (255, 255, 255) if highlight_fr else SOFT)

    small = F(LAT, 28)
    text_block(d, ["Traduction : Muhammad Hamidullah  •  Récitation : Mishary Alafasy"], small, H - 230, 34, (200, 210, 205))
    if cfg.get("handle"):
        text_block(d, [cfg["handle"]], F(LAT_B, 42), H - 170, 50, GOLD)
    return layer


def to_ar_digits(n):
    return str(n).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


def end_layer(cfg):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    text_block(d, ["Abonne-toi"], F(LAT_B, 96), 700, 110, CREAM, stroke=3)
    text_block(d, ["pour un verset chaque jour"], F(LAT, 54), 830, 64, SOFT)
    text_block(d, ["Partage-le à quelqu'un", "qui en a besoin"], F(LAT_B, 50), 1000, 64, GOLD)
    if cfg.get("handle"):
        text_block(d, [cfg["handle"]], F(LAT_B, 52), 1250, 60, GOLD)
    return layer


# ---------------------------------------------------------------- montage
def place(buf, sig, at):
    i = int(at * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i]


def build(ayahs, mode, out_path, cfg, demo=False):
    with tempfile.TemporaryDirectory() as tmp:
        segs, sounds = [], []  # segs: (start, dur, kind, idx, fr_on)
        t = 0.0
        intro = 1.2
        segs.append((0.0, intro, "page", None, False))
        t = intro
        for i, a in enumerate(ayahs):
            if demo:
                rec = np.zeros(int(SR * 4))
            else:
                rec = read_audio(download(a["audio"], os.path.join(tmp, f"r{i}.mp3")))
            idx = i if len(ayahs) > 1 else None
            segs.append((t, len(rec) / SR + 0.7, "page", idx, False))
            sounds.append((rec, t))
            t += len(rec) / SR + 0.7
            if mode == "recit_trad":
                fr = np.zeros(int(SR * 3)) if demo else tts(a["fr"], os.path.join(tmp, f"f{i}.mp3"))
                segs.append((t, len(fr) / SR + 0.8, "page", idx, True))
                sounds.append((fr * 0.9, t + 0.2))
                t += len(fr) / SR + 0.8
        segs.append((t, 3.5, "end", None, False))
        total = t + 3.5

        audio = np.zeros(int(SR * total) + SR)
        for sig, at in sounds:
            place(audio, sig, at)
        audio = audio[: int(SR * total)]
        peak = float(np.max(np.abs(audio))) or 1.0
        audio = audio / max(1.0, peak / 0.95)
        wav = os.path.join(tmp, "a.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((audio * 32767).astype(np.int16).tobytes())

        bg = Background(seed=ayahs[0]["s"] * 1000 + ayahs[0]["a"])
        cache = {}
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
             "-i", "-", "-i", wav, "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out_path], stdin=subprocess.PIPE)
        si = 0
        for f in range(int(total * FPS)):
            tt = f / FPS
            while si + 1 < len(segs) and tt >= segs[si + 1][0]:
                si += 1
            st, dur, kind, idx, fr_on = segs[si]
            key = (kind, idx, fr_on)
            if key not in cache:
                cache[key] = end_layer(cfg) if kind == "end" else layout(ayahs, cfg, fr_on, idx)
            frame = bg.frame(tt)
            # fondu d'entrée de chaque page
            lt = tt - st
            lay = cache[key]
            if lt < 0.5:
                a = lt / 0.5
                lay = Image.blend(Image.new("RGBA", (W, H), (0, 0, 0, 0)), lay, a)
            frame.alpha_composite(lay)
            if tt < 0.6:
                frame = Image.blend(Image.new("RGBA", (W, H), (0, 0, 0, 255)), frame, tt / 0.6)
            if tt > total - 0.6:
                frame = Image.blend(Image.new("RGBA", (W, H), (0, 0, 0, 255)), frame, max(0.0, (total - tt) / 0.6))
            proc.stdin.write(frame.convert("RGB").tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg a échoué")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue")
    ap.add_argument("--out", default="out")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg_path = os.path.join(HERE, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
    print("police arabe :", AR_FONT)
    if a.demo:
        demo = [dict(s=1, a=1, ar="بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ", fr="(texte de démonstration)",
                     audio=None, surah_ar="سُورَةُ ٱلْفَاتِحَةِ", surah="Al-Faatiha")]
        for mode in ("recit", "recit_trad"):
            build(demo, mode, os.path.join(a.out, f"demo-{mode}.mp4"), cfg, demo=True)
        return
    jobs = json.load(open(a.queue, encoding="utf-8"))["videos"]
    for v in jobs:
        if a.only is not None and v["name"] not in a.only:
            continue
        out = os.path.join(a.out, f"{v['name']}.mp4")
        ayahs = [fetch(v["surah"], n) for n in range(v["from"], v["to"] + 1)]
        dur = build(ayahs, v["mode"], out, cfg)
        print(f"ok {out} ({dur:.1f}s)")


if __name__ == "__main__":
    main()
