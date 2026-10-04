# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import contextlib
import functools
import http.server
import os
import re
import socketserver
import threading
RANGE = re.compile('bytes=(\\d*)-(\\d*)$')

class _Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, '.pmtiles': 'application/octet-stream', '.json': 'application/json', '.geojson': 'application/geo+json', '.js': 'text/javascript'}

    def end_headers(self):
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def send_head(self):
        m = RANGE.match(self.headers.get('Range', '').strip())
        path = self.translate_path(self.path)
        if not m or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        start, end = (m.group(1), m.group(2))
        if start == '':
            start, end = (max(0, size - int(end or 0)), size - 1)
        else:
            start, end = (int(start), min(int(end) if end else size - 1, size - 1))
        if start >= size or start > end:
            self.send_error(416, 'Requested range not satisfiable')
            return None
        f = open(path, 'rb')
        f.seek(start)
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, '_remaining', None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._remaining = None

    def log_message(self, fmt, *args):
        pass

class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

class PreviewServer:

    def __init__(self):
        self._server, self._thread, self.folder = (None, None, None)

    def url_for(self, folder):
        folder = os.path.abspath(folder)
        if self._server is None or folder != self.folder:
            self.stop()
            handler = functools.partial(_Handler, directory=folder)
            last_err = None
            for port in (8765, 8766, 8767, 8768, 0):
                try:
                    self._server = _Server(('127.0.0.1', port), handler)
                    break
                except OSError as e:
                    last_err = e
            else:
                raise last_err
            self.folder = folder
            self._thread = threading.Thread(target=self._server.serve_forever, name='WebMapKitPreview', daemon=True)
            self._thread.start()
        return 'http://127.0.0.1:%d/index.html' % self._server.server_address[1]

    def stop(self):
        if self._server is not None:
            with contextlib.suppress(Exception):
                self._server.shutdown()
                self._server.server_close()
        self._server, self._thread, self.folder = (None, None, None)
