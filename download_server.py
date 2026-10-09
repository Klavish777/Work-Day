#!/usr/bin/env python3
"""Small downloadable-file page for the three finished films and portraits.

Run with: python download_server.py
The server binds to 0.0.0.0 for Arena's live preview; all browser links are relative.
"""
from __future__ import annotations

import html
import mimetypes
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get('PORT', '8765'))
FILES = {
    '/download/ru': ('videos/sia_gate_ru_vertical.mp4', 'Sia_Russian_1080x1920.mp4'),
    '/download/en': ('videos/sia_gate_en_vertical.mp4', 'Sia_English_1080x1920.mp4'),
    '/download/fr': ('videos/sia_gate_fr_vertical.mp4', 'Sia_French_1080x1920.mp4'),
    '/download/all': ('Sia_3_languages_and_characters.zip', 'Sia_3_languages_and_characters.zip'),
    '/download/minjun': ('assets/minjun.png', 'Minjun.png'),
    '/download/sia': ('assets/sia.png', 'Sia.png'),
    '/download/dragon': ('assets/dragon_sia.png', 'Sia_returned.png'),
}

PAGE = '''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Сиа — скачать три ролика</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#080d15;color:#f5f0e9;font:16px/1.5 system-ui,-apple-system,Segoe UI,Arial,sans-serif}
main{max-width:840px;margin:auto;padding:52px 22px 72px}.eyebrow{text-transform:uppercase;letter-spacing:.22em;color:#ef9b83;font-size:12px;font-weight:700}
h1{font-size:clamp(31px,7vw,54px);line-height:1.08;letter-spacing:-.03em;margin:18px 0}p{color:#b7c0cc;max-width:650px}h2{font-size:23px;margin:45px 0 15px}.hero{border:1px solid #313646;border-radius:23px;padding:30px;background:linear-gradient(135deg,#202437,#131a25 55%,#241b24)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}.card{background:#121a25;border:1px solid #303746;border-radius:17px;padding:21px}.card strong{font-size:20px}.meta{font-size:13px;color:#a4aebd;margin:5px 0 18px}.button{display:inline-block;background:#e38b72;color:#151116!important;text-decoration:none;border-radius:11px;padding:11px 17px;font-weight:750;white-space:nowrap}.button:hover,.button:focus{background:#ffb294}.button.secondary{background:#293747;color:#f6f2ed!important}.button.secondary:hover{background:#43566b}.download-all{margin-top:18px}.portrait{width:100%;height:235px;object-fit:cover;object-position:top;border-radius:12px;margin-bottom:12px}small{color:#9aa7b5}.hint{border-left:3px solid #e38b72;padding-left:15px;margin-top:29px}footer{margin-top:44px;color:#9aa7b5;font-size:13px}
</style></head><body><main>
<div class="hero"><div class="eyebrow">СИА / ПО ТУ СТОРОНУ ВРАТ</div><h1>Три истории.<br>Три языка.</h1><p>Скачайте готовые вертикальные MP4-файлы по отдельности или одним архивом. Формат: 1080 × 1920, 9:16, H.264 + AAC.</p><a class="button download-all" href="/download/all" target="_blank" rel="noopener">↓ Скачать всё одним ZIP · 40 МБ</a></div>
<h2>Ролики</h2><div class="grid">
<div class="card"><strong>Русский</strong><div class="meta">1:45 · MP4 · ≈12 МБ</div><a class="button" href="/download/ru" target="_blank" rel="noopener">↓ Скачать видео</a></div>
<div class="card"><strong>English</strong><div class="meta">1:25 · MP4 · ≈9 МБ</div><a class="button" href="/download/en" target="_blank" rel="noopener">↓ Скачать видео</a></div>
<div class="card"><strong>Français</strong><div class="meta">1:44 · MP4 · ≈12 МБ</div><a class="button" href="/download/fr" target="_blank" rel="noopener">↓ Скачать видео</a></div></div>
<h2>Персонажи отдельно</h2><div class="grid">
<div class="card"><img class="portrait" src="/image/minjun" alt="Минджун"><strong>Минджун</strong><div class="meta">Охотник D-ранга</div><a class="button secondary" href="/download/minjun" target="_blank" rel="noopener">↓ Скачать PNG</a></div>
<div class="card"><img class="portrait" src="/image/sia" alt="Сиа"><strong>Чон Сиа</strong><div class="meta">При жизни</div><a class="button secondary" href="/download/sia" target="_blank" rel="noopener">↓ Скачать PNG</a></div>
<div class="card"><img class="portrait" src="/image/dragon" alt="Сиа после возвращения"><strong>Сиа</strong><div class="meta">После возвращения</div><a class="button secondary" href="/download/dragon" target="_blank" rel="noopener">↓ Скачать PNG</a></div></div>
<p class="hint">Если кнопка открывает файл вместо скачивания, используйте меню браузера «Сохранить файл» или «Скачать»; на телефоне удерживайте кнопку и выберите «Скачать по ссылке».</p>
<footer>Короткая адаптация текста. Движение камеры и звук смонтированы из кадров; покадрового lip-sync нет.</footer>
</main></body></html>'''.encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self): self.respond(head_only=True)
    def do_GET(self): self.respond(head_only=False)

    def respond(self, head_only=False):
        path = urlsplit(self.path).path
        if path in ('/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(PAGE)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if not head_only: self.wfile.write(PAGE)
            return
        download = path.startswith('/download/')
        if path.startswith('/image/'):
            key = '/download/' + path.split('/')[-1]
        else:
            key = path
        if key not in FILES:
            self.send_error(404, 'File not found')
            return
        relative, name = FILES[key]
        file = ROOT / relative
        if not file.is_file():
            self.send_error(404, 'Asset missing')
            return
        size = file.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get('Range', '')
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
            if not match or (not match[1] and not match[2]):
                self.send_error(416, 'Invalid range')
                return
            if match[1]: start = int(match[1])
            if match[2]: end = int(match[2]) if match[1] else size - int(match[2])
            if not match[1] and match[2]: start = max(0, size - int(match[2])); end = size - 1
            if start >= size or end < start:
                self.send_error(416, 'Range not satisfiable')
                return
            end = min(end, size - 1)
        self.send_response(206 if range_header else 200)
        self.send_header('Content-Type', mimetypes.guess_type(name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(end-start+1))
        self.send_header('Accept-Ranges', 'bytes')
        if range_header: self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if head_only: return
        try:
            with file.open('rb') as stream:
                stream.seek(start)
                left = end - start + 1
                while left:
                    chunk = stream.read(min(1024*256, left))
                    if not chunk: break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Download page listening on 0.0.0.0:{PORT}', flush=True)
    server.serve_forever()
