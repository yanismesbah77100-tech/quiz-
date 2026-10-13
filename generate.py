#!/usr/bin/env python3
"""Génère des vidéos quiz verticales (1080x1920) pour TikTok.

Usage : python3 generate.py <id> [<id> ...] [--out videos] [--no-voice]

Chaque vidéo : la voix lit la question pendant que la question et les 3 choix
s'affichent, puis compte à rebours de 5 secondes, puis révélation de la bonne
réponse (lue par la voix) et appel à s'abonner.
"""
import argparse
import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FPS = 30
SR = 44100
COUNTDOWN = 5
HANDLE = "@quiz.ou.pas"
VOICE = "fr-FR-HenriNeural"

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"]


def font(size, bold=True):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


# ---------- couleurs ----------
BG_TOP = (24, 16, 64)
BG_BOT = (74, 20, 110)
CARD = (255, 255, 255)
CARD_TXT = (30, 24, 60)
ACCENT = (255, 196, 0)
GOOD = (34, 197, 94)
BAD = (239, 68, 68)
LETTERS = ["A", "B", "C"]


def background():
    t = np.linspace(0, 1, H)[:, None, None]
    top = np.array(BG_TOP)[None, None, :]
    bot = np.array(BG_BOT)[None, None, :]
    arr = (top * (1 - t) + bot * t).repeat(W, axis=1).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    d = ImageDraw.Draw(img)
    # quelques cercles décoratifs
    for (cx, cy, r, a) in [(120, 300, 180, 18), (980, 1500, 260, 14), (900, 220, 90, 22)]:
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(overlay).ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, a))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return img


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


def centered(draw, y, text, fnt, fill):
    w = draw.textlength(text, font=fnt)
    draw.text(((W - w) / 2, y), text, font=fnt, fill=fill)


def fit_question(draw, text, maxw, maxlines=4):
    for size in (78, 70, 64, 58, 52, 46):
        f = font(size)
        lines = wrap(draw, text, f, maxw)
        if len(lines) <= maxlines:
            return f, lines
    return f, lines


class Renderer:
    def __init__(self, q):
        self.q = q
        self.bg = background()
        d = ImageDraw.Draw(self.bg)
        self.qfont, self.qlines = fit_question(d, q["q"], W - 180)
        self.cfont = font(56)
        self.small = font(40)
        self.big = font(150)

    def frame(self, phase, progress=0.0, remaining=COUNTDOWN, pop=1.0):
        """phase: 'ask' | 'count' | 'reveal'"""
        img = self.bg.copy()
        d = ImageDraw.Draw(img)
        q = self.q

        # entête
        centered(d, 150, "QUIZ", font(96), ACCENT)
        centered(d, 265, q.get("cat", "Culture générale").upper(), self.small, (220, 210, 255))

        # carte question
        lh = self.qfont.size + 18
        qh = lh * len(self.qlines) + 80
        top = 380
        d.rounded_rectangle((60, top, W - 60, top + qh), radius=40, fill=CARD)
        y = top + 40
        for line in self.qlines:
            centered(d, y, line, self.qfont, CARD_TXT)
            y += lh

        # choix
        cy = top + qh + 70
        for i, c in enumerate(q["choices"]):
            box = (90, cy, W - 90, cy + 140)
            fill, txt, lfill, ltxt = (255, 255, 255, 235), CARD_TXT, ACCENT, CARD_TXT
            if phase == "reveal":
                if i == q["answer"]:
                    fill, txt, lfill, ltxt = GOOD, (255, 255, 255), (255, 255, 255), GOOD
                else:
                    fill, txt, lfill, ltxt = (110, 90, 140), (200, 190, 220), (150, 130, 180), (90, 70, 120)
            if phase == "reveal" and i == q["answer"] and pop != 1.0:
                grow = int(20 * (pop - 1.0) / 0.08)
                box = (box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow)
            d.rounded_rectangle(box, radius=70, fill=fill[:3])
            r = 46
            lx, ly = box[0] + 30 + r, (box[1] + box[3]) // 2
            d.ellipse((lx - r, ly - r, lx + r, ly + r), fill=lfill)
            lw = d.textlength(LETTERS[i], font=self.cfont)
            d.text((lx - lw / 2, ly - 34), LETTERS[i], font=self.cfont, fill=ltxt)
            cf = self.cfont
            while d.textlength(c, font=cf) > box[2] - lx - r - 60 and cf.size > 34:
                cf = font(cf.size - 4)
            d.text((lx + r + 30, ly - cf.size * 0.62), c, font=cf, fill=txt)
            cy += 175

        # zone basse : minuteur / message
        by = cy + 40
        if phase == "ask":
            centered(d, by + 40, "Prépare ta réponse...", font(54), (255, 255, 255))
        elif phase == "count":
            cx_, cy_ = W // 2, by + 120
            R = 110
            d.ellipse((cx_ - R, cy_ - R, cx_ + R, cy_ + R), outline=(255, 255, 255, 80), width=14)
            end = -90 + 360 * (1 - progress)
            if end > -90:
                d.arc((cx_ - R, cy_ - R, cx_ + R, cy_ + R), start=-90, end=end, fill=ACCENT, width=14)
            n = str(max(1, math.ceil(remaining)))
            nw = d.textlength(n, font=self.big)
            d.text((cx_ - nw / 2, cy_ - 92), n, font=self.big, fill=(255, 255, 255))
        else:
            centered(d, by + 10, "Tu avais bon ?", font(60), ACCENT)
            centered(d, by + 95, "Dis-le en commentaire !", font(48), (255, 255, 255))

        # pied
        centered(d, H - 170, "Abonne-toi pour un quiz chaque jour", font(38, bold=False), (230, 225, 255))
        centered(d, H - 115, HANDLE, font(44), ACCENT)
        return img


# ---------- audio ----------
def tone(freq, dur, vol=0.3, decay=8.0):
    t = np.arange(int(SR * dur)) / SR
    return vol * np.sin(2 * np.pi * freq * t) * np.exp(-decay * t)


def tick():
    return tone(1500, 0.08, 0.35, 40) + tone(3000, 0.08, 0.1, 60)


def ding():
    return tone(880, 0.9, 0.3, 3.5) + tone(1320, 0.9, 0.18, 3.5) + tone(1760, 0.9, 0.1, 4)


def pad(dur):
    """Nappe de fond douce générée (aucune musique protégée)."""
    t = np.arange(int(SR * dur)) / SR
    chord = [220.0, 277.18, 329.63]
    sig = sum(np.sin(2 * np.pi * f * t) for f in chord) / len(chord)
    pulse = 0.6 + 0.4 * np.sin(2 * np.pi * 2.0 * t) ** 2
    return 0.06 * sig * pulse


def read_audio(path):
    """Décode n'importe quel fichier audio en mono float 44.1 kHz via ffmpeg."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
        check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


async def _edge(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="+5%").save(path)


def speak(text, tmp, name):
    path = os.path.join(tmp, name + ".mp3")
    try:
        asyncio.run(_edge(text, path))
    except Exception as e:  # repli sur gTTS
        print("edge-tts indisponible, essai gTTS :", e, file=sys.stderr)
        from gtts import gTTS
        gTTS(text, lang="fr").save(path)
    return read_audio(path)


def place(buf, sig, at):
    i = int(at * SR)
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i]


# ---------- assemblage ----------
def build(q, out_path, voice=True):
    with tempfile.TemporaryDirectory() as tmp:
        if voice:
            v_q = speak(q["q"] + " " + ", ".join(
                f"{LETTERS[i]} : {c}" for i, c in enumerate(q["choices"])) + ".", tmp, "q")
            v_a = speak("La bonne réponse était : " + q["choices"][q["answer"]] + " !", tmp, "a")
            ask = len(v_q) / SR + 0.4
        else:
            v_q = v_a = None
            ask = 3.0
        reveal_at = ask + COUNTDOWN
        total = reveal_at + max(3.5, (len(v_a) / SR + 1.2) if v_a is not None else 0)

        audio = pad(total)
        if v_q is not None:
            place(audio, v_q * 0.95, 0.2)
        for s in range(COUNTDOWN):
            place(audio, tick(), ask + s)
        place(audio, ding(), reveal_at)
        if v_a is not None:
            place(audio, v_a * 0.95, reveal_at + 0.5)
        audio = np.clip(audio, -1, 1)
        wav_path = os.path.join(tmp, "a.wav")
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((audio * 32767).astype(np.int16).tobytes())

        r = Renderer(q)
        nframes = int(total * FPS)
        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", wav_path,
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out_path],
            stdin=subprocess.PIPE)
        cache = {}
        for f in range(nframes):
            t = f / FPS
            if t < ask:
                key = ("ask",)
                if key not in cache:
                    cache = {key: r.frame("ask").tobytes()}
                data = cache[key]
            elif t < reveal_at:
                el = t - ask
                data = r.frame("count", progress=el / COUNTDOWN, remaining=COUNTDOWN - el).tobytes()
            else:
                el = t - reveal_at
                pop = 1.0 + 0.08 * math.sin(math.pi * min(el / 0.35, 1.0)) if el < 0.35 else 1.0
                key = ("reveal", round(pop, 3))
                if key not in cache:
                    cache[key] = r.frame("reveal", pop=pop).tobytes()
                data = cache[key]
            proc.stdin.write(data)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg a échoué")
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="+", type=int)
    ap.add_argument("--out", default=os.path.join(HERE, "videos"))
    ap.add_argument("--no-voice", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    bank = {q["id"]: q for q in json.load(open(os.path.join(HERE, "questions.json"), encoding="utf-8"))}
    for i in a.ids:
        out = os.path.join(a.out, f"quiz-{i:04d}.mp4")
        if os.path.exists(out):
            print("déjà fait :", out)
            continue
        dur = build(bank[i], out, voice=not a.no_voice)
        print(f"ok {out} ({dur:.1f}s)")


if __name__ == "__main__":
    main()
