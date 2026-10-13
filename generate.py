#!/usr/bin/env python3
"""Génère des vidéos quiz verticales (1080x1920) de plus d'une minute pour TikTok.

Usage :
  python3 generate.py --name v0001 --ids 3 17 104 131 206 [--out videos] [--no-voice]
  python3 generate.py --queue queue.json [--out videos]

Déroulé d'une vidéo : intro -> 5 questions (facile, facile, moyen, moyen,
difficile) -> pause « like et abonne-toi » après la 3e -> écran de score.
Chaque question : la voix lit la question et les choix, compte à rebours de
5 s, puis révélation de la bonne réponse, lue par la voix.
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
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
FPS = 30
SR = 44100
COUNTDOWN = 5
HANDLE = "@quiz.ou.pas"
VOICE = "fr-FR-HenriNeural"
HERE = os.path.dirname(os.path.abspath(__file__))
LETTERS = ["A", "B", "C"]

LEVELS = {
    1: ("FACILE", (34, 197, 94)),
    2: ("MOYEN", (250, 170, 20)),
    3: ("DIFFICILE", (239, 68, 68)),
}
# dégradés animés : (haut, milieu, bas)
PALETTES = {
    "intro": [(20, 10, 60), (90, 30, 160), (230, 60, 140)],
    1: [(5, 30, 80), (10, 110, 170), (40, 200, 190)],
    2: [(40, 10, 90), (130, 40, 180), (240, 90, 160)],
    3: [(60, 5, 20), (170, 30, 50), (250, 120, 30)],
    "like": [(80, 5, 40), (220, 30, 80), (255, 120, 90)],
}
WHITE = (255, 255, 255)
INK = (28, 22, 58)
GOLD = (255, 205, 40)
GOOD = (34, 197, 94)


def font(size, bold=True):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def ease(x):
    x = max(0.0, min(1.0, x))
    return 1 - (1 - x) ** 3


# ---------------------------------------------------------------- fond animé
class Background:
    """Dégradé qui ondule lentement + bulles lumineuses qui montent."""

    def __init__(self, seed=0):
        self.cache = {}
        rnd = random.Random(seed)
        self.sprites = []
        for _ in range(16):
            r = rnd.randint(40, 170)
            spr = Image.new("RGBA", (r * 4, r * 4), (0, 0, 0, 0))
            a = rnd.randint(25, 70)
            ImageDraw.Draw(spr).ellipse((r, r, r * 3, r * 3), fill=(255, 255, 255, a))
            spr = spr.filter(ImageFilter.GaussianBlur(r * 0.45))
            self.sprites.append(dict(
                img=spr, x=rnd.uniform(-100, W), y0=rnd.uniform(0, H + 400),
                speed=rnd.uniform(25, 90), sway=rnd.uniform(20, 70),
                ph=rnd.uniform(0, 6.28), size=r * 4))

    def gradient(self, key):
        if key not in self.cache:
            top, mid, bot = (np.array(c, dtype=np.float32) for c in PALETTES[key])
            hh = H * 2
            t = (np.arange(hh) / hh)[:, None]
            # deux cycles complets pour pouvoir faire défiler sans couture
            ph = (np.sin(t * 2 * np.pi * 2) + 1) / 2
            c1 = top * (1 - ph) + mid * ph
            ph2 = (np.sin(t * 2 * np.pi * 2 + 1.7) + 1) / 2
            col = c1 * (1 - ph2 * 0.6) + bot * (ph2 * 0.6)
            arr = np.repeat(col[:, None, :], W // 4, axis=1).astype(np.uint8)
            img = Image.fromarray(arr, "RGB").resize((W, hh), Image.BILINEAR)
            self.cache[key] = img
        return self.cache[key]

    def frame(self, key, t):
        g = self.gradient(key)
        off = int((t * 60) % H)
        img = g.crop((0, off, W, off + H))
        for s in self.sprites:
            y = (s["y0"] - t * s["speed"]) % (H + 600) - 400
            x = s["x"] + math.sin(t * 0.7 + s["ph"]) * s["sway"]
            img.paste(s["img"], (int(x - s["size"] / 2), int(y - s["size"] / 2)), s["img"])
        return img


# ---------------------------------------------------------------- dessin texte
def wrap(draw, text, fnt, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=fnt) <= maxw:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def centered(d, y, text, fnt, fill, stroke=0, stroke_fill=(0, 0, 0)):
    w = d.textlength(text, font=fnt)
    d.text(((W - w) / 2, y), text, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def shadow_box(layer, box, radius, fill):
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((box[0], box[1] + 14, box[2], box[3] + 14), radius, fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    layer.alpha_composite(sh)
    ImageDraw.Draw(layer).rounded_rectangle(box, radius, fill=fill)


def heart(d, cx, cy, s, fill):
    r = s * 0.3
    d.ellipse((cx - s * 0.5, cy - s * 0.35, cx - s * 0.5 + 2 * r, cy - s * 0.35 + 2 * r), fill=fill)
    d.ellipse((cx + s * 0.5 - 2 * r, cy - s * 0.35, cx + s * 0.5, cy - s * 0.35 + 2 * r), fill=fill)
    d.polygon([(cx - s * 0.49, cy - s * 0.02), (cx + s * 0.49, cy - s * 0.02), (cx, cy + s * 0.5)], fill=fill)


def footer(d):
    centered(d, H - 200, "Like  •  Abonne-toi  •  Un quiz chaque jour", font(34, False), (255, 255, 255, 230))
    centered(d, H - 150, HANDLE, font(46), GOLD, stroke=3, stroke_fill=(0, 0, 0, 120))


def progress_dots(d, idx, total=5):
    gap, r = 70, 16
    x0 = W / 2 - gap * (total - 1) / 2
    for i in range(total):
        x = x0 + i * gap
        fill = GOLD if i < idx else (WHITE if i == idx else (255, 255, 255, 90))
        rr = r + 6 if i == idx else r
        d.ellipse((x - rr, 110 - rr, x + rr, 110 + rr), fill=fill)


# ---------------------------------------------------------------- calques
def shuffled(q):
    order = [0, 1, 2]
    random.Random(q["id"] * 7919).shuffle(order)
    return [q["choices"][i] for i in order], order.index(0)


def question_layer(q, idx, reveal=False):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    choices, ans = shuffled(q)
    progress_dots(d, idx)
    lvl_name, lvl_col = LEVELS[q["lvl"]]
    centered(d, 160, f"QUESTION {idx + 1}/5", font(64), WHITE, stroke=4, stroke_fill=(0, 0, 0, 110))
    bf = font(40)
    bw = d.textlength(lvl_name, font=bf) + 70
    d.rounded_rectangle(((W - bw) / 2, 250, (W + bw) / 2, 315), 33, fill=lvl_col)
    centered(d, 258, lvl_name, bf, WHITE)

    # carte question
    for size in (72, 66, 60, 54, 48):
        qf = font(size)
        lines = wrap(d, q["q"], qf, W - 200)
        if len(lines) <= 4:
            break
    lh = qf.size + 16
    top = 360
    qh = lh * len(lines) + 90
    shadow_box(layer, (60, top, W - 60, top + qh), 44, (255, 255, 255, 250))
    d = ImageDraw.Draw(layer)
    y = top + 45
    for line in lines:
        centered(d, y, line, qf, INK)
        y += lh

    cy = top + qh + 60
    cf0 = font(54)
    boxes = []
    for i, c in enumerate(choices):
        box = (90, cy, W - 90, cy + 135)
        if reveal and i == ans:
            fill, txt, cir, ltxt = GOOD + (255,), WHITE, WHITE, GOOD
        elif reveal:
            fill, txt, cir, ltxt = (255, 255, 255, 70), (255, 255, 255, 150), (255, 255, 255, 90), (255, 255, 255, 160)
        else:
            fill, txt, cir, ltxt = (255, 255, 255, 240), INK, GOLD, INK
        shadow_box(layer, box, 68, fill)
        d = ImageDraw.Draw(layer)
        r = 46
        lx, ly = box[0] + 28 + r, (box[1] + box[3]) // 2
        d.ellipse((lx - r, ly - r, lx + r, ly + r), fill=cir)
        lw = d.textlength(LETTERS[i], font=cf0)
        d.text((lx - lw / 2, ly - 33), LETTERS[i], font=cf0, fill=ltxt)
        cf = cf0
        while d.textlength(c, font=cf) > box[2] - lx - r - 50 and cf.size > 32:
            cf = font(cf.size - 3)
        d.text((lx + r + 28, ly - cf.size * 0.6), c, font=cf, fill=txt)
        boxes.append(box)
        cy += 165
    if reveal:
        centered(d, cy + 50, "+1 point si tu avais bon !", font(56), GOLD, stroke=4, stroke_fill=(0, 0, 0, 120))
    footer(d)
    return layer, cy


def intro_layer():
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    centered(d, 470, "QUIZ", font(200), GOLD, stroke=8, stroke_fill=(0, 0, 0, 120))
    centered(d, 720, "5 QUESTIONS", font(110), WHITE, stroke=6, stroke_fill=(0, 0, 0, 120))
    centered(d, 870, "de plus en plus dures", font(64), WHITE, stroke=4, stroke_fill=(0, 0, 0, 100))
    y = 1040
    for lvl in (1, 2, 3):
        name, col = LEVELS[lvl]
        f = font(46)
        w = d.textlength(name, font=f) + 80
        d.rounded_rectangle(((W - w) / 2, y, (W + w) / 2, y + 80), 40, fill=col)
        centered(d, y + 14, name, f, WHITE)
        y += 110
    centered(d, 1420, "Compte tes points !", font(70), GOLD, stroke=5, stroke_fill=(0, 0, 0, 120))
    footer(d)
    return layer


def like_layer(scale=1.0):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    centered(d, 330, "TOUT BON", font(110), WHITE, stroke=6, stroke_fill=(0, 0, 0, 120))
    centered(d, 470, "jusqu'ici ?", font(80), WHITE, stroke=5, stroke_fill=(0, 0, 0, 120))
    heart(d, W / 2, 790, 300 * scale, (255, 40, 80))
    heart(d, W / 2, 790, 200 * scale, (255, 90, 120))
    centered(d, 990, "LIKE", font(120), WHITE, stroke=6, stroke_fill=(0, 0, 0, 120))
    bw, bh = 700 * scale, 150 * scale
    bx, by = (W - bw) / 2, 1200
    d.rounded_rectangle((bx, by, bx + bw, by + bh), bh / 2, fill=(255, 40, 80))
    f = font(int(76 * scale))
    centered(d, by + (bh - f.size) / 2 - 6, "+ ABONNE-TOI", f, WHITE)
    centered(d, 1430, "La suite est plus dure...", font(60), GOLD, stroke=4, stroke_fill=(0, 0, 0, 120))
    footer(d)
    return layer


def outro_layer():
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    centered(d, 330, "TON SCORE ?", font(120), GOLD, stroke=7, stroke_fill=(0, 0, 0, 120))
    rows = [("5/5", "Génie absolu", GOOD), ("3-4", "Très fort", (250, 170, 20)), ("0-2", "Retente demain", (239, 68, 68))]
    y = 560
    for score, label, col in rows:
        shadow_box(layer, (110, y, W - 110, y + 160), 50, (255, 255, 255, 240))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((130, y + 20, 360, y + 140), 40, fill=col)
        f = font(70)
        sw = d.textlength(score, font=f)
        d.text((245 - sw / 2, y + 38), score, font=f, fill=WHITE)
        d.text((400, y + 48), label, font=font(60), fill=INK)
        y += 200
    centered(d, 1200, "Dis ton score", font(76), WHITE, stroke=5, stroke_fill=(0, 0, 0, 120))
    centered(d, 1295, "en commentaire !", font(76), WHITE, stroke=5, stroke_fill=(0, 0, 0, 120))
    centered(d, 1440, "Abonne-toi pour le quiz de demain", font(48), GOLD, stroke=3, stroke_fill=(0, 0, 0, 120))
    footer(d)
    return layer


# ---------------------------------------------------------------- audio
def tone(freq, dur, vol=0.3, decay=8.0):
    t = np.arange(int(SR * dur)) / SR
    return vol * np.sin(2 * np.pi * freq * t) * np.exp(-decay * t)


def tick():
    return tone(1500, 0.08, 0.3, 40) + tone(3000, 0.08, 0.08, 60)


def ding():
    return tone(880, 1.0, 0.28, 3.5) + tone(1320, 1.0, 0.16, 3.5) + tone(1760, 1.0, 0.08, 4)


def whoosh():
    n = int(SR * 0.45)
    noise = np.random.default_rng(1).standard_normal(n)
    env = np.sin(np.linspace(0, np.pi, n)) ** 2
    k = np.ones(30) / 30
    return 0.12 * np.convolve(noise, k, mode="same") * env


def music(dur):
    """Boucle musicale générée (accords + basse + arpège + kick), libre de droits."""
    bpm = 104
    beat = 60 / bpm
    n = int(SR * dur)
    out = np.zeros(n)
    prog = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]  # Am F C G
    hz = lambda m: 440 * 2 ** ((m - 69) / 12)
    bar = beat * 4
    t_bar = 0.0
    i = 0
    while t_bar < dur:
        ch = prog[i % 4]
        # nappe
        L = int(SR * bar)
        t = np.arange(L) / SR
        padsig = sum(np.sin(2 * np.pi * hz(m) * t) for m in ch) / 3 * 0.035
        s = int(t_bar * SR)
        e = min(n, s + L)
        out[s:e] += padsig[: e - s]
        for b in range(4):
            tb = t_bar + b * beat
            # basse
            place(out, tone(hz(ch[0] - 12), beat * 0.9, 0.09, 3), tb)
            # kick
            kt = np.arange(int(SR * 0.18)) / SR
            kick = 0.18 * np.sin(2 * np.pi * (50 + 90 * np.exp(-30 * kt)) * kt) * np.exp(-14 * kt)
            place(out, kick, tb)
            # arpège en croches
            for k in range(2):
                m = ch[(b * 2 + k) % 3] + 12
                place(out, tone(hz(m), beat / 2, 0.035, 7), tb + k * beat / 2)
        t_bar += bar
        i += 1
    return out


def place(buf, sig, at):
    i = int(at * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i]


def read_audio(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


async def _edge(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="+8%").save(path)


class Voice:
    def __init__(self, enabled, tmp):
        self.enabled, self.tmp, self.n = enabled, tmp, 0

    def say(self, text, fallback):
        """Renvoie le signal audio, ou un silence de `fallback` secondes."""
        if not self.enabled:
            return np.zeros(int(SR * fallback))
        self.n += 1
        path = os.path.join(self.tmp, f"v{self.n}.mp3")
        for attempt in range(3):
            try:
                asyncio.run(_edge(text, path))
                return read_audio(path)
            except Exception as e:
                print("edge-tts erreur :", e, file=sys.stderr)
        from gtts import gTTS
        gTTS(text, lang="fr").save(path)
        return read_audio(path)


# ---------------------------------------------------------------- montage
def build(questions, out_path, voice=True):
    with tempfile.TemporaryDirectory() as tmp:
        V = Voice(voice, tmp)
        segs = []  # (kind, start, dur, data)
        voices = []  # (signal, start)
        sfx = []
        t = 0.0

        def add(kind, dur, data=None, speech=None, delay=0.25):
            nonlocal t
            if speech is not None:
                voices.append((speech, t + delay))
            segs.append((kind, t, dur, data))
            sfx.append((whoosh(), t))
            t += dur

        v = V.say("Cinq questions, de plus en plus dures. Compte tes points. C'est parti !", 3.5)
        add("intro", max(3.5, len(v) / SR + 0.6), None, v)

        for idx, q in enumerate(questions):
            if idx == 3:
                v = V.say("Si t'as tout bon jusqu'ici, like et abonne-toi ! La suite est plus dure.", 4.0)
                add("like", max(4.0, len(v) / SR + 0.6), None, v)
            choices, ans = shuffled(q)
            text = f"Question {idx + 1}. {q['q']} " + ". ".join(
                f"{LETTERS[i]} : {c}" for i, c in enumerate(choices)) + "."
            v = V.say(text, 4.0)
            ask_d = len(v) / SR + 0.5
            add("ask", ask_d, (q, idx), v, delay=0.3)
            segs[-1] = ("ask", segs[-1][1], ask_d, (q, idx))
            # le compte à rebours suit sans whoosh
            cs = t
            segs.append(("count", t, COUNTDOWN, (q, idx)))
            for s in range(COUNTDOWN):
                sfx.append((tick(), cs + s))
            t += COUNTDOWN
            v = V.say(f"Réponse {LETTERS[ans]} : {choices[ans]} !", 2.0)
            rd = max(3.0, len(v) / SR + 1.0)
            segs.append(("reveal", t, rd, (q, idx)))
            sfx.append((ding(), t))
            voices.append((v, t + 0.35))
            t += rd

        v = V.say("Alors, combien de points ? Dis ton score en commentaire, et abonne-toi pour le quiz de demain !", 5.0)
        add("outro", max(5.0, len(v) / SR + 1.0), None, v)
        total = t

        # ---- audio
        voice_track = np.zeros(int(SR * total) + SR)
        for sig, at in voices:
            place(voice_track, sig, at)
        env = np.convolve(np.abs(voice_track) > 0.02, np.ones(SR // 5) / (SR // 5), mode="same")
        duck = 1.0 - 0.55 * np.clip(env * 3, 0, 1)
        audio = music(total + 1)[: len(voice_track)] * duck
        audio += voice_track * 1.0
        for sig, at in sfx:
            place(audio, sig, at)
        # fondu de fin
        fade = int(SR * 1.0)
        end = int(SR * total)
        audio[end - fade:end] *= np.linspace(1, 0, fade)
        audio = audio[:end]
        peak = np.max(np.abs(audio)) or 1
        audio = audio / max(1.0, peak / 0.95)
        wav = os.path.join(tmp, "a.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((audio * 32767).astype(np.int16).tobytes())

        # ---- vidéo
        bg = Background(seed=sum(q["id"] for q in questions))
        layers = {}

        def get(key, fn):
            if key not in layers:
                layers[key] = fn()
            return layers[key]

        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
             "-r", str(FPS), "-i", "-", "-i", wav, "-c:v", "libx264", "-preset", "medium", "-crf", "21",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
             out_path], stdin=subprocess.PIPE)
        si = 0
        for f in range(int(total * FPS)):
            tt = f / FPS
            while si + 1 < len(segs) and tt >= segs[si + 1][1]:
                si += 1
            kind, st, dur, data = segs[si]
            lt = tt - st
            if kind in ("ask", "count", "reveal"):
                q, idx = data
                pal = q["lvl"]
            else:
                pal = "like" if kind == "like" else "intro"
            frame = bg.frame(pal, tt).convert("RGBA")
            dy = 0
            if kind == "intro":
                lay = get("intro", intro_layer)
                dy = int((1 - ease(lt / 0.5)) * 300)
            elif kind == "like":
                pulse = 1 + 0.06 * math.sin(lt * 2 * math.pi * 1.6)
                lay = get(("like", round(pulse, 2)), lambda: like_layer(pulse))
            elif kind == "outro":
                lay = get("outro", outro_layer)
                dy = int((1 - ease(lt / 0.5)) * 300)
            elif kind in ("ask", "count"):
                lay, cy = get(("q", q["id"]), lambda: question_layer(q, idx))
                if kind == "ask":
                    dy = int((1 - ease(lt / 0.45)) * 400)
            else:
                lay, cy = get(("r", q["id"]), lambda: question_layer(q, idx, reveal=True))
            if dy:
                frame.alpha_composite(lay.crop((0, 0, W, H - dy)), (0, dy))
            else:
                frame.alpha_composite(lay)
            d = ImageDraw.Draw(frame)
            if kind == "ask" and lt > 0.5:
                centered(d, cy + 60, "Réfléchis bien...", font(54), WHITE, stroke=4, stroke_fill=(0, 0, 0, 110))
            if kind == "count":
                cx_, cyy, R = W // 2, cy + 150, 110
                d.ellipse((cx_ - R - 10, cyy - R - 10, cx_ + R + 10, cyy + R + 10), fill=(0, 0, 0, 90))
                d.ellipse((cx_ - R, cyy - R, cx_ + R, cyy + R), outline=(255, 255, 255, 120), width=14)
                end_a = -90 + 360 * (1 - lt / COUNTDOWN)
                if end_a > -90:
                    d.arc((cx_ - R, cyy - R, cx_ + R, cyy + R), -90, end_a, fill=GOLD, width=14)
                n = str(max(1, math.ceil(COUNTDOWN - lt)))
                bf = font(140)
                nw = d.textlength(n, font=bf)
                d.text((cx_ - nw / 2, cyy - 86), n, font=bf, fill=WHITE)
            proc.stdin.write(frame.convert("RGB").tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg a échoué")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name")
    ap.add_argument("--ids", nargs="+", type=int)
    ap.add_argument("--queue")
    ap.add_argument("--out", default=os.path.join(HERE, "videos"))
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    bank = {q["id"]: q for q in json.load(open(os.path.join(HERE, "questions.json"), encoding="utf-8"))}
    jobs = []
    if a.queue:
        jobs = [(v["name"], v["ids"]) for v in json.load(open(a.queue))["videos"]]
    if a.name:
        jobs.append((a.name, a.ids))
    for name, ids in jobs:
        out = os.path.join(a.out, f"{name}.mp4")
        if os.path.exists(out):
            print("déjà fait :", out)
            continue
        dur = build([bank[i] for i in ids], out, voice=not a.no_voice)
        print(f"ok {out} ({dur:.1f}s)")


if __name__ == "__main__":
    main()
