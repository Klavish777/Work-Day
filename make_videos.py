#!/usr/bin/env python3
"""Make three vertical cinematic story adaptations from the supplied portraits and speech.

Requires: pip install pillow numpy imageio-ffmpeg soundfile mutagen
Inputs in assets/ and audio/. The images are still images; this script does not
claim to synthesize live-action motion or true phoneme-aligned lip animation.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf
from mutagen.mp3 import MP3
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / '.cache' / 'workday'
OUT = ROOT / 'videos'
CACHE.mkdir(parents=True, exist_ok=True)
OUT.mkdir(exist_ok=True)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FPS = 30
W, H = 1080, 1920
SR = 32000

TEXT = {
    'ru': {
        'series': 'СИА  /  ПО ТУ СТОРОНУ ВРАТ',
        'end': 'ПРОДОЛЖЕНИЕ СЛЕДУЕТ',
        'scenes': [
            ('memorial', '25 АВГУСТА 2024', 'ГОД СПУСТЯ.'),
            ('sia', 'ВОСПОМИНАНИЕ', 'ЧОН СИА\n2000 — 2023'),
            ('fire_gate', 'ВРАТА D-РАНГА', 'ТРОЕ РАНЕНЫ.\nОДНА ПОГИБЛА.'),
            ('minjun', 'НАВЫК: ??? (EX)', 'ЕСЛИ БЫ Я БЫЛ\nСИЛЬНЕЕ...'),
            ('memorial', 'ГОДОВЩИНА', 'ТОКПОККИ. РИСОВОЕ ВИНО.\nНЕВРУЧЁННЫЙ ПОДАРОК.'),
            ('sia', 'ПОСЛЕДНИЙ РАЗГОВОР', '«ДАВАЙ ПОЕДИМ\nТОКПОККИ ВМЕСТЕ!»'),
            ('memorial', 'СЛИШКОМ ПОЗДНО', 'Я ОТДАЛ БЫ ВСЁ\nЗА ЕЩЁ ОДИН ВЕЧЕР.'),
            ('rupture', 'ЗЕМЛЯ ДРОГНУЛА', 'ПРОРЫВ ВРАТ.'),
            ('dragon_sia', 'НЕЗНАКОМКА ИЗ ИНОГО МИРА', 'АЛЫЕ ВОЛОСЫ.\nРУБИНОВЫЕ ГЛАЗА.'),
            ('minjun', 'ОНА ПРОИЗНЕСЛА', '«ОППА?»'),
            ('dragon_sia', 'ДВЕ ТЫСЯЧИ ЛЕТ СПУСТЯ', '«МИНДЖУН-ОППА!\nЭТО Я. ТВОЯ СИА!»'),
            ('dragon_sia', 'И ЕЁ ПЕРВЫЙ ВОПРОС...', '«ТЫ НЕ ЗАВЁЛ\nДРУГУЮ ДЕВУШКУ?»'),
        ],
    },
    'en': {
        'series': 'SIA  /  BEYOND THE GATE',
        'end': 'TO BE CONTINUED',
        'scenes': [
            ('memorial', 'AUGUST 25, 2024', 'ONE YEAR LATER.'),
            ('sia', 'A MEMORY', 'JEONG SIA\n2000 — 2023'),
            ('fire_gate', 'A D-RANK GATE', 'THREE INJURED.\nONE DEAD.'),
            ('minjun', 'ABILITY: ??? (EX)', 'IF ONLY I HAD\nBEEN STRONGER...'),
            ('memorial', 'THE ANNIVERSARY', 'TTEOKBOKKI. RICE WINE.\nAN UNOPENED GIFT.'),
            ('sia', 'THE LAST CONVERSATION', '“LET’S JUST HAVE\nTTEOKBOKKI TOGETHER!”'),
            ('memorial', 'TOO LATE', 'I WOULD GIVE ANYTHING\nFOR ONE MORE EVENING.'),
            ('rupture', 'THE EARTH SHOOK', 'A GATE BREAK.'),
            ('dragon_sia', 'A STRANGER FROM ANOTHER WORLD', 'SCARLET HAIR.\nRUBY EYES.'),
            ('minjun', 'SHE CALLED ME', '“OPPA?”'),
            ('dragon_sia', 'TWO THOUSAND YEARS LATER', '“MINJUN-OPPA!\nIT’S ME. YOUR SIA!”'),
            ('dragon_sia', 'AND HER FIRST QUESTION...', '“YOU HAVEN’T FOUND\nANOTHER GIRLFRIEND?”'),
        ],
    },
    'fr': {
        'series': 'SIA  /  AU-DELÀ DU PORTAIL',
        'end': 'À SUIVRE',
        'scenes': [
            ('memorial', '25 AOÛT 2024', 'UN AN PLUS TARD.'),
            ('sia', 'UN SOUVENIR', 'JEONG SIA\n2000 — 2023'),
            ('fire_gate', 'PORTAIL DE RANG D', 'TROIS BLESSÉS.\nUNE MORTE.'),
            ('minjun', 'POUVOIR : ??? (EX)', 'SI SEULEMENT J’AVAIS\nÉTÉ PLUS FORT...'),
            ('memorial', 'L’ANNIVERSAIRE', 'TTEOKBOKKI. VIN DE RIZ.\nUN CADEAU INOUVERT.'),
            ('sia', 'LEUR DERNIÈRE CONVERSATION', '« MANGEONS JUSTE DES\nTTEOKBOKKI ENSEMBLE ! »'),
            ('memorial', 'TROP TARD', 'JE DONNERAIS TOUT\nPOUR UN AUTRE SOIR.'),
            ('rupture', 'LA TERRE A TREMBLÉ', 'UNE BRÈCHE S’OUVRE.'),
            ('dragon_sia', 'UNE INCONNUE VENUE D’AILLEURS', 'CHEVEUX ÉCARLATES.\nYEUX DE RUBIS.'),
            ('minjun', 'ELLE M’A APPELÉ', '« OPPA ? »'),
            ('dragon_sia', 'DEUX MILLE ANS PLUS TARD', '« MINJUN-OPPA !\nC’EST MOI. TA SIA ! »'),
            ('dragon_sia', 'ET SA PREMIÈRE QUESTION...', '« TU N’AS PAS TROUVÉ\nUNE AUTRE PETITE AMIE ? »'),
        ],
    },
}


def cmd(args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def duration(path):
    return MP3(path).info.length


def fit_font(draw, lines, max_width=666, start=48):
    size = start
    while size > 22:
        font = ImageFont.truetype(BOLD, size)
        if max(draw.textbbox((0, 0), line, font=font)[2] for line in lines) < max_width:
            return font
        size -= 2
    return ImageFont.truetype(BOLD, 22)


def make_card(lang, idx, image_name, eyebrow, title):
    original = Image.open(ROOT / 'assets' / (image_name + '.png')).convert('RGB')
    # Exact 9:16 crop; preserves the original photographic framing.
    ow, oh = original.size
    target_ratio = 9 / 16
    if ow / oh > target_ratio:
        nw = round(oh * target_ratio)
        original = original.crop(((ow - nw) // 2, 0, (ow + nw) // 2, oh))
    else:
        nh = round(ow / target_ratio)
        original = original.crop((0, (oh - nh) // 2, ow, (oh + nh) // 2))
    original = original.resize((768, 1366), Image.Resampling.LANCZOS)
    overlay = Image.new('RGBA', original.size)
    d = ImageDraw.Draw(overlay)
    # Gradients leave the faces clear, keeping titles legible on every shot.
    for y in range(1366):
        bottom = max(0, (y - 690) / 676)
        top = max(0, 1 - y / 400)
        opacity = min(217, int((bottom ** 1.25) * 205 + top * 42))
        if opacity:
            d.line([(0, y), (768, y)], fill=(4, 9, 19, opacity))
    d.rectangle((48, 58, 110, 63), fill=(242, 110, 94, 220))
    head_font = ImageFont.truetype(BOLD, 20)
    d.text((48, 80), TEXT[lang]['series'], font=head_font, fill=(249, 249, 246, 225), stroke_width=1, stroke_fill=(3, 5, 10, 130))
    kicker_font = ImageFont.truetype(BOLD, 23)
    lines = title.split('\n')
    title_font = fit_font(d, lines)
    title_step = title_font.size + 17
    title_h = len(lines) * title_step
    title_y = 1190 - title_h
    d.text((50, title_y - 49), eyebrow, font=kicker_font, fill=(255, 150, 130, 255), stroke_width=2, stroke_fill=(3, 5, 10, 160))
    for i, line in enumerate(lines):
        d.text((48, title_y + i * title_step), line, font=title_font, fill=(255, 252, 247, 255), stroke_width=2, stroke_fill=(8, 12, 19, 170))
    d.rectangle((50, 1236, 145, 1239), fill=(242, 110, 94, 205))
    small = ImageFont.truetype(FONT, 16)
    d.text((50, 1251), f'{idx + 1:02d} / 12', font=small, fill=(240, 240, 242, 195))
    card = Image.alpha_composite(original.convert('RGBA'), overlay).convert('RGB')
    path = CACHE / f'{lang}_{idx:02d}.jpg'
    card.save(path, quality=94, subsampling=0)
    return path


def decode(path):
    # Decode to the same sample rate as the background to avoid accumulated drift.
    output = CACHE / (path.stem + '.wav')
    cmd([FFMPEG, '-y', '-loglevel', 'error', '-i', str(path), '-ar', str(SR), '-ac', '1', '-c:a', 'pcm_f32le', str(output)])
    data, _ = sf.read(output, dtype='float32')
    return data


def soundscape(total, starts, durations, lang):
    n = math.ceil(total * SR)
    rng = np.random.default_rng(9001)
    t = np.arange(n, dtype=np.float32) / SR
    # A quiet original three-note score, wind, rain and subterranean rumble.
    white = rng.standard_normal(n).astype(np.float32)
    smoothed = np.convolve(white[::160], np.ones(24, np.float32)/24, mode='same')
    slow_noise = np.interp(np.arange(n), np.arange(len(smoothed))*160, smoothed).astype(np.float32)
    swell = 0.68 + 0.23 * np.sin(2*np.pi*0.045*t)
    drone = (np.sin(2*np.pi*55*t) + .46*np.sin(2*np.pi*82.4*t + .4)) * .014 * swell
    wind = slow_noise * .035 + white * .0018
    bed = drone + wind
    # Restrained three-note memorial theme; synthesized and original, no samples.
    motif_times = [2.0, starts[1] * .30, starts[1] * .79]
    for j, at in enumerate(motif_times):
        start = int(at*SR)
        length = min(n-start, int(4.3*SR))
        if length <= 0: continue
        tt = np.arange(length, dtype=np.float32)/SR
        freq = [220, 261.63, 329.63][j]
        bell = (.007*np.sin(2*np.pi*freq*tt) + .003*np.sin(2*np.pi*2*freq*tt)) * np.exp(-tt/1.35)
        bed[start:start+length] += bell
    # Fire crackle, ground shock and portal opening aligned with picture cuts.
    fx = np.zeros(n, np.float32)
    hits = [(starts[0]+durations[0]*.35, .045, 3),
            (starts[1]+durations[1]*.37, .10, 5),
            (starts[1]+durations[1]*.62, .08, 4),
            (starts[2], .045, 2)]
    for at, amp, sec in hits:
        start = int(at * SR)
        length = min(int(sec*SR), n-start)
        if length <= 0: continue
        tt = np.arange(length, dtype=np.float32)/SR
        sub = np.sin(2*np.pi*(46-18*tt/max(sec, 1))*tt) * np.exp(-tt*.9)
        hiss = rng.standard_normal(length).astype(np.float32) * np.exp(-tt*2.8)
        fx[start:start+length] += amp*(sub*.78 + hiss*.10)
    bed += fx
    # Discreet opening and ending fades.
    fade = np.minimum(1, t / .8) * np.minimum(1, np.maximum(0, (total-t))/2.2)
    bed *= fade
    mix = np.stack([bed, np.roll(bed, 301)], axis=1)
    for i in range(3):
        voice = decode(ROOT / 'audio' / f'{lang}_{i+1}.mp3')
        start = int(starts[i]*SR)
        end = min(n, start+len(voice))
        # A slight L/R asymmetry creates space without moving the voice off center.
        mix[start:end, 0] += .85*voice[:end-start]
        mix[start:end, 1] += .85*voice[:end-start]
    peak = np.max(np.abs(mix))
    if peak > .96: mix *= .96/peak
    path = CACHE / f'{lang}_mix.wav'
    sf.write(path, mix, SR, subtype='PCM_16')
    return path


def render_clip(lang, idx, path, seconds):
    frames = max(1, round(seconds * FPS))
    dest = CACHE / f'{lang}_clip_{idx:02d}.mp4'
    # Independent slow camera pushes and a subtle frame-coherent grain.
    direction = 1 if idx % 2 == 0 else -1
    x = '(iw-iw/zoom)*(0.5+0.16*sin(on/88))'
    y = f'(ih-ih/zoom)*(0.5+{direction}*0.11*sin(on/124))'
    vf = (f"zoompan=z='min(zoom+0.00023,1.075)':x='{x}':y='{y}':"
          f"d=1:s={W}x{H}:fps={FPS},"
          "vignette=PI/6,format=yuv420p")
    command = [FFMPEG, '-y', '-loglevel', 'error', '-loop', '1', '-framerate', str(FPS),
               '-i', str(path), '-vf', vf, '-frames:v', str(frames),
               '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
               '-pix_fmt', 'yuv420p', '-r', str(FPS), '-an', str(dest)]
    cmd(command)
    return dest


def build(lang):
    clips = [ROOT / 'audio' / f'{lang}_{i}.mp3' for i in (1, 2, 3)]
    d1, d2, d3 = [duration(x) for x in clips]
    starts = [0.8, 0.8+d1+1.1, 0.8+d1+1.1+d2+1.0]
    total = starts[2] + d3 + 2.4
    # Proportions place relevant artwork near the spoken plot points.
    boundaries = [0, starts[0]+d1*.15, starts[0]+d1*.34,
                  starts[0]+d1*.55, starts[0]+d1*.78, starts[1],
                  starts[1]+d2*.21, starts[1]+d2*.37,
                  starts[1]+d2*.62, starts[1]+d2*.86,
                  starts[2], starts[2]+d3*.67, total]
    print(f'{lang}: {total:.1f}s, {W}x{H}, {FPS} fps', flush=True)
    cards = TEXT[lang]['scenes']
    segments = []
    for i, (image, eyebrow, title) in enumerate(cards):
        card = make_card(lang, i, image, eyebrow, title)
        segments.append(render_clip(lang, i, card, boundaries[i+1]-boundaries[i]))
        print(f'  scene {i+1}/12', flush=True)
    listfile = CACHE / f'{lang}_list.txt'
    listfile.write_text(''.join(f"file '{x.resolve()}'\n" for x in segments))
    picture = CACHE / f'{lang}_picture.mp4'
    cmd([FFMPEG, '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', str(listfile),
         '-c', 'copy', str(picture)])
    soundtrack = soundscape(total, starts, [d1, d2, d3], lang)
    target = OUT / f'sia_gate_{lang}_vertical.mp4'
    cmd([FFMPEG, '-y', '-loglevel', 'error', '-i', str(picture), '-i', str(soundtrack),
         '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
         '-ar', '32000', '-ac', '2', '-movflags', '+faststart', '-shortest', str(target)])
    print(f'  completed: {target.relative_to(ROOT)} ({target.stat().st_size/1048576:.1f} MiB)', flush=True)


if __name__ == '__main__':
    import sys
    languages = sys.argv[1:] or ['ru', 'en', 'fr']
    for language in languages:
        if language not in TEXT: raise ValueError(language)
        build(language)
