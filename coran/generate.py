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
API = "https://api.alquran.cloud/v1/ayah/{s}:{a}/editions/quran-uthmani,fr.hamidullah"
AUDIO = "https://everyayah.com/data/{folder}/{s:03d}{a:03d}.mp3"
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
            ar, fr = d
            text = ar["text"].strip()
            # l'API colle la basmala au 1er verset de chaque sourate (sauf 1 et 9)
            if a == 1 and s not in (1, 9) and text.startswith("بِسْمِ"):
                text = " ".join(text.split(" ")[4:]).strip()
            return dict(s=s, a=a, ar=text, fr=fr["text"].strip(),
                        surah_ar=ar["surah"]["name"], surah=ar["surah"]["englishName"],
                        n_ayahs=ar["surah"]["numberOfAyahs"])
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


def trim_edges(sig, keep=0.35, thr=0.01):
    """Coupe seulement le silence en trop au début et à la fin d'un fichier de verset."""
    loud = np.where(np.abs(sig) > thr)[0]
    if len(loud) == 0:
        return sig
    a = max(0, loud[0] - int(keep * SR))
    b = min(len(sig), loud[-1] + int(keep * SR))
    return sig[a:b]


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


class PhotoBackground:
    """Vraie photo de paysage (libre de droits) avec lent zoom et déplacement, voile sombre pour le texte."""

    def __init__(self, path, total, seed):
        im = Image.open(path).convert("RGB")
        # on travaille sur une version dont la hauteur fait au moins 2100 px
        scale = max(2100 / im.height, 1300 / im.width, 1.0)
        if scale > 1:
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        self.im, self.total = im, total
        rnd = random.Random(seed)
        self.dir = rnd.choice([-1, 1])
        shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        g = np.zeros((H, 1), np.float32)
        y = np.linspace(0, 1, H)[:, None]
        g = 70 + 70 * np.abs(y - 0.45) * 2  # voile léger, plus sombre en haut et en bas
        a = np.repeat(np.clip(g, 0, 200).astype(np.uint8), W, axis=1)
        shade.putalpha(Image.fromarray(a, "L"))
        self.shade = shade

    def frame(self, t):
        p = t / max(self.total, 1)
        z = 1.0 + 0.12 * p
        ch = self.im.height / z
        cw = ch * W / H
        if cw > self.im.width:
            cw = self.im.width
            ch = cw * H / W
        room = self.im.width - cw
        x0 = room * (0.5 + self.dir * (p - 0.5) * 0.8)
        y0 = (self.im.height - ch) / 2
        img = self.im.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch)).convert("RGBA")
        img.alpha_composite(self.shade)
        return img


class VideoBackground:
    """Vraie vidéo de paysage (libre de droits) en boucle, recadrée en vertical, couleurs ravivées."""

    def __init__(self, path, total):
        vf = ("scale='if(gt(a,9/16),-2,1080)':'if(gt(a,9/16),1920,-2)',crop=1080:1920,"
              "eq=saturation=1.25:contrast=1.06,fps=30")
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "quiet", "-stream_loop", "-1", "-i", path, "-t", f"{total + 1:.2f}", "-an",
             "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        y = np.linspace(0, 1, H)[:, None]
        g = 60 + 80 * np.abs(y - 0.45) * 2
        a = np.repeat(np.clip(g, 0, 200).astype(np.uint8), W, axis=1)
        self.shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        self.shade.putalpha(Image.fromarray(a, "L"))
        self.last = None

    def frame(self, t):
        raw = self.proc.stdout.read(W * H * 3)
        if len(raw) == W * H * 3:
            self.last = Image.frombytes("RGB", (W, H), raw)
        img = (self.last or Image.new("RGB", (W, H), (10, 30, 40))).convert("RGBA")
        img.alpha_composite(self.shade)
        return img


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
        # on cale le haut réel des glyphes (les polices arabes ont de grandes marges)
        l, t, r, b = d.textbbox((0, 0), line, font=fnt, **kw)
        d.text(((W - (r - l)) / 2 - l, y - t + (lh - (b - t)) / 2), line, font=fnt, fill=fill,
               stroke_width=stroke, stroke_fill=(0, 0, 0, 120), **kw)
        y += lh
    return y


def layout(ayahs, cfg, highlight_fr=False, idx=None):
    """Calque transparent : en-tête, texte arabe, traduction, pied de page."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    shown0 = ayahs[idx] if idx is not None else ayahs[0]
    first = shown0
    ref = "Basmala" if shown0.get("basmala") else f"{shown0['s']}:{shown0['a']}"
    # en-tête
    hf = F(AR_FONT, 70)
    text_block(d, [first["surah_ar"]], hf, 130, 110, GOLD, rtl=True)
    sub = F(LAT_B, 38)
    text_block(d, [f"Sourate {first['surah']}  •  {ref}"], sub, 255, 50, SOFT)
    d.line((W / 2 - 160, 330, W / 2 + 160, 330), fill=GOLD + (180,), width=3)

    shown = ayahs if idx is None else [ayahs[idx]]
    ar = " ".join(a["ar"] if a.get("basmala") else f"{a['ar']} ﴿{to_ar_digits(a['a'])}﴾" for a in shown)
    fr = " ".join(a["fr"] for a in shown)
    # taille adaptée : arabe en haut (jusqu'à ~55 % de la place), traduction dessous
    top, bottom = 380, H - 260
    for size in (118, 108, 98, 90, 82, 74, 66, 58, 52, 46, 42):
        af = F(AR_FONT, size)
        al = wrap(d, ar, af, W - 140, rtl=True)
        alh = int(size * 1.9)
        if len(al) * alh <= (bottom - top) * 0.58:
            break
    show_fr = cfg.get("translation", True)
    if not show_fr:
        # sans traduction : l'arabe prend toute la place
        for size in (124, 112, 102, 94, 86, 78, 70, 62, 56, 50, 44):
            af = F(AR_FONT, size)
            al = wrap(d, ar, af, W - 140, rtl=True)
            alh = int(size * 1.9)
            if len(al) * alh <= (bottom - top) * 0.92:
                break
        y = top + (bottom - top - len(al) * alh) / 2
        text_block(d, al, af, y, alh, CREAM, rtl=True)
        credit = "Récitation : " + cfg_global.get("_reciter", "")
    else:
        for fsize in (56, 52, 48, 44, 40, 36, 32, 29, 26):
            ff = F(LAT_SERIF_I, fsize)
            fl = wrap(d, fr, ff, W - 160)
            flh = int(fsize * 1.4)
            if len(al) * alh + 110 + len(fl) * flh <= bottom - top:
                break
        total = len(al) * alh + 110 + len(fl) * flh
        y = top + (bottom - top - total) / 2
        y = text_block(d, al, af, y, alh, CREAM, rtl=True)
        d.line((W / 2 - 60, y + 50, W / 2 + 60, y + 50), fill=GOLD + (150,), width=2)
        text_block(d, fl, ff, y + 110, flh, (255, 255, 255) if highlight_fr else SOFT)
        credit = "Traduction : Muhammad Hamidullah  •  Récitation : " + cfg_global.get("_reciter", "")

    small = F(LAT, 28)
    text_block(d, [credit], small, H - 230, 34, (200, 210, 205))
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
HOOKS = [
    ("Prends une minute", "et écoute ce rappel"),
    ("Écoute jusqu'à la fin", "ce rappel est pour toi"),
    ("Que ces versets", "apaisent ton cœur"),
    ("Arrête-toi un instant", "et écoute la Parole d'Allah"),
    ("Ce rappel est peut-être", "un signe pour toi"),
]
MIN_DUR = 62.0
INTRO, END, GAP_REC, GAP_FR = 3.2, 4.0, 0.0, 0.8
MAX_EDGE_SILENCE = 0.35  # on garde la respiration naturelle du récitateur, sans blanc ajouté


def place(buf, sig, at):
    i = int(at * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i]


def hook_layer(lines, cfg):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    text_block(d, ["بِسْمِ اللَّهِ"], F(AR_FONT, 90), 520, 150, GOLD, rtl=True)
    text_block(d, [lines[0]], F(LAT_B, 76), 780, 100, CREAM, stroke=3)
    text_block(d, [lines[1]], F(LAT_SERIF_I, 58), 890, 80, SOFT)
    text_block(d, ["Écoute jusqu'au bout et abonne-toi"], F(LAT, 38), 1120, 50, GOLD)
    return layer


def cta_banner():
    b = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(b)
    d.rounded_rectangle((130, 1560, W - 130, 1660), 50, fill=(232, 196, 110, 235))
    text_block(d, ["Abonne-toi pour ne rien rater"], F(LAT_B, 42), 1585, 50, (12, 40, 40))
    return b


cfg_global = {}


def gather(v, tmp, folder):
    """Récupère le passage demandé. Si la vidéo fait moins de MIN_DUR secondes :
    - on continue le passage dans la même sourate (suite cohérente) ;
    - si la sourate est finie, on ajoute la sourate suivante EN ENTIER.
    On ne coupe jamais une sourate commencée par une extension. Avant chaque
    sourate commencée au verset 1 (sauf 1 et 9), on met la basmala récitée."""
    mode = v["mode"] if cfg_global.get("spoken_translation", True) else "recit"
    items = []
    basm_cache = {}

    def add_basmala(s):
        if "t" not in basm_cache:
            b = fetch(1, 1)
            b["rec"] = trim_edges(read_audio(download(AUDIO.format(folder=folder, s=1, a=1),
                                                      os.path.join(tmp, "basmala.mp3"))), MAX_EDGE_SILENCE / 2)
            basm_cache["t"] = b
        ref = fetch(s, 1)
        b = dict(basm_cache["t"], s=s, a=0, basmala=True, surah_ar=ref["surah_ar"], surah=ref["surah"],
                 n_ayahs=ref["n_ayahs"])
        items.append(b)

    def add(s, a):
        if a == 1 and s not in (1, 9):
            add_basmala(s)
        ay = fetch(s, a)
        ay["rec"] = trim_edges(read_audio(download(AUDIO.format(folder=folder, s=s, a=a),
                                                    os.path.join(tmp, f"r{s}_{a}.mp3"))), MAX_EDGE_SILENCE / 2)
        if mode == "recit_trad":
            ay["frv"] = tts(ay["fr"], os.path.join(tmp, f"f{s}_{a}.mp3"))
        items.append(ay)

    def duration():
        t = INTRO + END
        for ay in items:
            t += len(ay["rec"]) / SR + GAP_REC
            if "frv" in ay:
                t += len(ay["frv"]) / SR + GAP_FR
        return t

    for n in range(v["from"], v["to"] + 1):
        add(v["surah"], n)
    while duration() < MIN_DUR:
        last = items[-1]
        if last["a"] < last["n_ayahs"]:
            add(last["s"], last["a"] + 1)
        elif last["s"] < 114:
            nxt = last["s"] + 1
            first = fetch(nxt, 1)
            if first["n_ayahs"] > 45:  # on n'ajoute pas une longue sourate entière
                break
            for n in range(1, first["n_ayahs"] + 1):
                add(nxt, n)
        else:
            break
    return items, duration()


def build(items, mode, out_path, cfg, hook, bg_path=None):
    with tempfile.TemporaryDirectory() as tmp:
        segs, sounds = [("hook", 0.0, INTRO, None, False)], []
        t = INTRO
        for i, ay in enumerate(items):
            r = len(ay["rec"]) / SR + GAP_REC
            segs.append(("page", t, r, i, False))
            sounds.append((ay["rec"], t))
            t += r
            if "frv" in ay:
                f = len(ay["frv"]) / SR + GAP_FR
                segs.append(("page", t, f, i, True))
                sounds.append((ay["frv"] * 0.9, t + 0.2))
                t += f
        segs.append(("end", t, END, None, False))
        total = t + END
        cta_from, cta_to = total * 0.45, total * 0.45 + 4.0

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

        if bg_path and bg_path.endswith((".webm", ".mp4", ".mov")):
            bg = VideoBackground(bg_path, total)
        elif bg_path:
            bg = PhotoBackground(bg_path, total, items[0]["s"])
        else:
            bg = Background(seed=items[0]["s"] * 1000 + items[0]["a"])
        cache = {}
        banner = cta_banner()
        empty = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
             "-i", "-", "-i", wav, "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-maxrate", "1500k", "-bufsize", "3000k", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out_path], stdin=subprocess.PIPE)
        si = 0
        for f in range(int(total * FPS)):
            tt = f / FPS
            while si + 1 < len(segs) and tt >= segs[si + 1][1]:
                si += 1
            kind, st, dur, idx, fr_on = segs[si]
            key = (kind, idx, fr_on)
            if key not in cache:
                if kind == "hook":
                    cache[key] = hook_layer(hook, cfg)
                elif kind == "end":
                    cache[key] = end_layer(cfg)
                else:
                    cache[key] = layout(items, cfg, fr_on, idx)
            frame = bg.frame(tt)
            lt = tt - st
            lay = cache[key]
            # pas de fondu quand on passe de la récitation à la traduction du même verset
            same = kind == "page" and fr_on and lt < 0.5
            if lt < 0.25 and not same:
                lay = Image.blend(empty, lay, lt / 0.25)
            frame.alpha_composite(lay)
            if cta_from <= tt <= cta_to and kind == "page":
                k = min(1.0, (tt - cta_from) / 0.4, (cta_to - tt) / 0.4)
                frame.alpha_composite(Image.blend(empty, banner, max(0.0, k)))
            # barre de progression
            d = ImageDraw.Draw(frame)
            d.rectangle((0, 0, W, 10), fill=(255, 255, 255, 40))
            d.rectangle((0, 0, int(W * tt / total), 10), fill=GOLD + (230,))
            if tt < 0.5:
                frame = Image.blend(black, frame, tt / 0.5)
            if tt > total - 0.6:
                frame = Image.blend(black, frame, max(0.0, (total - tt) / 0.6))
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
    ap.add_argument("--bg", help="photo de fond (test)")
    ap.add_argument("--fonds", default="fonds", help="dossier des photos de fond")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg_path = os.path.join(HERE, "config.json")
    cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
    cfg_global.update(cfg)
    print("police arabe :", AR_FONT)
    if a.demo:
        items = [dict(s=1, a=i, ar="بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ", fr="(texte de démonstration)",
                      surah_ar="سُورَةُ ٱلْفَاتِحَةِ", surah="Al-Faatiha", rec=np.zeros(SR * 4),
                      frv=np.zeros(SR * 3)) for i in (1, 2)]
        build(items, "recit_trad", os.path.join(a.out, "demo.mp4"), cfg, HOOKS[0], a.bg)
        return
    jobs = json.load(open(a.queue, encoding="utf-8"))["videos"]
    for v in jobs:
        if a.only is not None and v["name"] not in a.only:
            continue
        out = os.path.join(a.out, f"{v['name']}.mp4")
        with tempfile.TemporaryDirectory() as tmp:
            recs = cfg.get("reciters") or [{"name": "Mishary Alafasy", "folder": "Alafasy_128kbps"}]
            rec = next((r for r in recs if r["folder"] == v.get("reciter")), None) or recs[int(v["name"].lstrip("c") or 0) % len(recs)]
            cfg_global["_reciter"] = rec["name"]
            items, _ = gather(v, tmp, rec["folder"])
            num = int(v["name"].lstrip("c") or 0)
            hook = HOOKS[num % len(HOOKS)]
            bgs = json.load(open(os.path.join(HERE, "backgrounds.json"), encoding="utf-8"))
            bg_path = os.path.join(a.fonds, v.get("bg") or bgs[(num * 5) % len(bgs)]["file"])
            if not os.path.exists(bg_path):
                print("fond introuvable :", bg_path, file=sys.stderr)
                bg_path = None
            dur = build(items, v["mode"] if cfg.get("spoken_translation", True) else "recit", out, cfg, hook, bg_path)
        print(f"ok {out} ({dur:.1f}s, {cfg_global['_reciter']}, versets {items[0]['s']}:{items[0]['a']} -> {items[-1]['s']}:{items[-1]['a']})")


if __name__ == "__main__":
    main()
