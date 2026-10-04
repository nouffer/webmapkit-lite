# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import http.server, os, re, socketserver, sys, webbrowser
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
RANGE = re.compile('bytes=(\\d*)-(\\d*)$')

class Handler(http.server.SimpleHTTPRequestHandler):
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
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
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
if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('127.0.0.1', PORT), Handler) as httpd:
        url = f'http://localhost:{PORT}'
        print(f'Your map is running at {url}  (press Ctrl+C to stop)')
        if '--no-browser' not in sys.argv:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print('\nStopped.')
