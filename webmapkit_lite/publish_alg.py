# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import datetime
import hashlib
import hmac
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import html
from qgis.PyQt.QtCore import QCoreApplication
from .net import open_url
from qgis.core import Qgis, QgsProcessingAlgorithm, QgsProcessingException, QgsProcessingParameterBoolean, QgsProcessingParameterFile, QgsProcessingParameterString, QgsSettings
SETTINGS = 'webmapkit/r2/'
SKIP_FILES = {'serve.py', 'CONFIG.md', 'config.backup.json', '.DS_Store', 'Thumbs.db'}
SKIP_PATTERNS = [re.compile('_export_tmp'), re.compile('^\\.')]
TYPES = {'.pmtiles': 'application/octet-stream', '.json': 'application/json', '.geojson': 'application/geo+json', '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.txt': 'text/plain; charset=utf-8', '.md': 'text/markdown; charset=utf-8', '.ico': 'image/x-icon', '.woff2': 'font/woff2'}

def _uri_encode(s, keep_slash=True):
    safe = '-_.~' + ('/' if keep_slash else '')
    return urllib.parse.quote(s, safe=safe)

def _hmac(key, msg):
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()

def sign_request(method, url, access_key, secret_key, headers=None, payload_hash='UNSIGNED-PAYLOAD', region='auto', service='s3', now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime('%Y%m%dT%H%M%SZ')
    datestamp = now.strftime('%Y%m%d')
    u = urllib.parse.urlsplit(url)
    host = u.netloc
    canonical_uri = _uri_encode(urllib.parse.unquote(u.path) or '/')
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    canonical_query = '&'.join(('%s=%s' % (_uri_encode(k, False), _uri_encode(v, False)) for k, v in sorted(q)))
    hdrs = {k.lower(): str(v).strip() for k, v in (headers or {}).items()}
    hdrs['host'] = host
    hdrs['x-amz-date'] = amz_date
    hdrs['x-amz-content-sha256'] = payload_hash
    signed = sorted((h for h in hdrs if h in ('host', 'content-type', 'content-md5') or h.startswith('x-amz-')))
    canonical_headers = ''.join(('%s:%s\n' % (h, re.sub('\\s+', ' ', hdrs[h])) for h in signed))
    signed_headers = ';'.join(signed)
    canonical_request = '\n'.join([method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash])
    scope = '%s/%s/%s/aws4_request' % (datestamp, region, service)
    string_to_sign = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()])
    k = _hmac(('AWS4' + secret_key).encode('utf-8'), datestamp)
    k = _hmac(k, region)
    k = _hmac(k, service)
    k = _hmac(k, 'aws4_request')
    signature = hmac.new(k, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    out = {h: hdrs[h] for h in hdrs if h != 'host'}
    out['Authorization'] = 'AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s' % (access_key, scope, signed_headers, signature)
    return out

def r2_endpoint(account_id, jurisdiction=''):
    j = (jurisdiction or '').strip().lower()
    return 'https://%s.%sr2.cloudflarestorage.com' % (account_id.strip(), j + '.' if j and j != 'default' else '')

class R2Client:

    def __init__(self, account_id, access_key, secret_key, endpoint=None, jurisdiction=None):
        if jurisdiction is None:
            st = QgsSettings()
            jurisdiction = st.value(SETTINGS + 'jurisdiction', '') if st.value(SETTINGS + 'account', '') == account_id.strip() else ''
        self.endpoint = (endpoint or r2_endpoint(account_id, jurisdiction)).rstrip('/')
        self.ak, self.sk = (access_key.strip(), secret_key.strip())

    def _url(self, bucket, key=''):
        return '%s/%s%s' % (self.endpoint, bucket, '/' + _uri_encode(key) if key else '')

    def request(self, method, bucket, key='', body=None, headers=None, length=None):
        url = self._url(bucket, key)
        h = dict(headers or {})
        if length is not None:
            h['Content-Length'] = str(length)
        signed = sign_request(method, url, self.ak, self.sk, headers=h)
        req = urllib.request.Request(url, data=body, method=method)
        for k, v in signed.items():
            req.add_header(k, v)
        if 'Content-Length' in h:
            req.add_header('Content-Length', h['Content-Length'])
        try:
            with open_url(req, timeout=300) as r:
                return (r.status, r.read())
        except urllib.error.HTTPError as e:
            return (e.code, e.read())

    def bucket_exists(self, bucket):
        return self.request('HEAD', bucket)[0]

    def put_file(self, bucket, key, path, content_type, cache_control):
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            status, body = self.request('PUT', bucket, key, body=f, length=size, headers={'Content-Type': content_type, 'Cache-Control': cache_control})
        if status not in (200, 201):
            raise QgsProcessingException('Upload of %s failed (HTTP %s): %s' % (key, status, _s3_error(body)))
        return size

    def put_bytes(self, bucket, key, data, content_type, cache_control='no-cache'):
        status, body = self.request('PUT', bucket, key, body=data, length=len(data), headers={'Content-Type': content_type, 'Cache-Control': cache_control})
        if status not in (200, 201):
            raise QgsProcessingException('Upload of %s failed (HTTP %s): %s' % (key, status, _s3_error(body)))

    def get(self, bucket, key):
        return self.request('GET', bucket, key)

    def delete(self, bucket, key):
        status, body = self.request('DELETE', bucket, key)
        if status not in (200, 204, 404):
            raise RuntimeError("Couldn't delete %s (HTTP %s): %s" % (key, status, _s3_error(body)))

    def list(self, bucket, prefix=''):
        out, token = ([], None)
        while True:
            q = {'list-type': '2', 'max-keys': '1000'}
            if prefix:
                q['prefix'] = prefix
            if token:
                q['continuation-token'] = token
            url = self._url(bucket) + '?' + urllib.parse.urlencode(q, quote_via=lambda v, *a, **k: urllib.parse.quote(v, safe='-_.~'))
            signed = sign_request('GET', url, self.ak, self.sk)
            req = urllib.request.Request(url, method='GET')
            for k, v in signed.items():
                req.add_header(k, v)
            try:
                with open_url(req, timeout=60) as r:
                    body = r.read()
            except urllib.error.HTTPError as e:
                raise RuntimeError(_friendly_status(e.code, bucket) or 'Listing failed (HTTP %s): %s' % (e.code, _s3_error(e.read())))
            text = body.decode('utf-8', 'replace')
            for c in re.findall('<Contents>(.*?)</Contents>', text, re.S):
                mod = _xml_value(c, 'LastModified') or ''
                try:
                    dt = datetime.datetime.strptime(mod[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=datetime.timezone.utc)
                except ValueError:
                    dt = None
                out.append({'key': _xml_value(c, 'Key'), 'size': int(_xml_value(c, 'Size') or 0), 'modified': dt})
            if (_xml_value(text, 'IsTruncated') or '').lower() == 'true':
                token = _xml_value(text, 'NextContinuationToken')
                if not token:
                    break
            else:
                break
        return out

def _friendly_status(code, bucket):
    if code == 404:
        return 'Bucket "%s" was not found. Check the name in Hosting settings.' % bucket
    if code in (401, 403):
        return 'Cloudflare refused the keys. Check them in Hosting settings (the token needs Object Read & Write).'
    return None
MANIFEST = 'webmapkit.json'

def client_from_settings(h):
    return R2Client(h['account'], h['access_key'], h['secret'], endpoint=os.environ.get('WEBMAPKIT_R2_ENDPOINT'), jurisdiction=h.get('jurisdiction', ''))

def _read_json(client, bucket, key):
    status, body = client.get(bucket, key)
    if status != 200:
        return None
    try:
        return json.loads(body.decode('utf-8'))
    except Exception:
        return None

def map_info(client, bucket, name):
    cfg = _read_json(client, bucket, '%s/config.json' % name)
    if cfg is None:
        return None
    man = _read_json(client, bucket, '%s/%s' % (name, MANIFEST)) or {}
    return {'name': name, 'title': cfg.get('title') or name, 'description': cfg.get('description') or '', 'project_id': man.get('project_id', ''), 'project': man.get('project', ''), 'published': man.get('published', '')}

def unique_name(client, bucket, base):
    taken = {o['key'].split('/', 1)[0] for o in client.list(bucket) if '/' in o['key']}
    if base not in taken:
        return base
    i = 2
    while '%s-%d' % (base, i) in taken:
        i += 1
    return '%s-%d' % (base, i)

def _xml_value(text, tag):
    m = re.search('<%s>(.*?)</%s>' % (tag, tag), text, re.S)
    return html.unescape(m.group(1)) if m else None

def _s3_error(body):
    text = body.decode('utf-8', 'replace') if isinstance(body, bytes) else str(body)
    m = re.search('<Message>(.*?)</Message>', text, re.S)
    return (m.group(1) if m else text[:200]).strip()

def _title_of(folder):
    try:
        with open(os.path.join(folder, 'config.json'), encoding='utf-8') as f:
            return json.load(f).get('title', '')
    except Exception:
        return ''

def slug(s):
    s = re.sub('[^a-z0-9]+', '-', s.lower()).strip('-')
    return s or 'map'
_ADVANCED = getattr(getattr(Qgis, 'ProcessingParameterFlag', object), 'Advanced', None) or getattr(__import__('qgis.core', fromlist=['QgsProcessingParameterDefinition']).QgsProcessingParameterDefinition, 'FlagAdvanced')

class PublishWebMap(QgsProcessingAlgorithm):
    FOLDER = 'FOLDER'
    MAP_NAME = 'MAP_NAME'
    ACCOUNT = 'ACCOUNT'
    BUCKET = 'BUCKET'
    ACCESS_KEY = 'ACCESS_KEY'
    R2_SK = 'SECRET'
    PUBLIC_URL = 'PUBLIC_URL'
    REMEMBER = 'REMEMBER'
    PROJECT_ID = 'PROJECT_ID'
    PROJECT = 'PROJECT'

    def tr(self, s):
        return QCoreApplication.translate('WebMapKit', s)

    def createInstance(self):
        return PublishWebMap()

    def name(self):
        return 'publishwebmapr2'

    def displayName(self):
        return self.tr('Publish web map to Cloudflare R2')

    def group(self):
        return ''

    def groupId(self):
        return ''

    def shortHelpString(self):
        return self.tr("Uploads a map folder made by 'Export web map' to your Cloudflare R2 bucket and returns a public link.\n\nOne-time set-up: create an R2 bucket, enable its public r2.dev URL and create an R2 API token with Object Read & Write. Paste the Account ID, keys, bucket name and public URL here.\n\nMap name becomes part of the link, e.g. https://pub-….r2.dev/my-map/index.html. Publishing again with the same name updates the map.\n\n'Remember' stores these settings in your QGIS profile on this computer (the secret key is stored in plain text).")

    def flags(self):
        f = super().flags()
        nt = getattr(getattr(Qgis, 'ProcessingAlgorithmFlag', object), 'NoThreading', None) or getattr(QgsProcessingAlgorithm, 'FlagNoThreading', None)
        return f | nt if nt is not None else f

    def initAlgorithm(self, config=None):
        st = QgsSettings()
        folder = getattr(QgsProcessingParameterFile, 'Folder', None)
        if folder is None:
            folder = Qgis.ProcessingFileParameterBehavior.Folder
        self.addParameter(QgsProcessingParameterFile(self.FOLDER, self.tr('Map folder (made by Export web map)'), behavior=folder))
        self.addParameter(QgsProcessingParameterString(self.MAP_NAME, self.tr('Map name in the link (letters, numbers, dashes)'), optional=True))
        self.addParameter(QgsProcessingParameterString(self.ACCOUNT, self.tr('Cloudflare Account ID'), defaultValue=st.value(SETTINGS + 'account', '')))
        self.addParameter(QgsProcessingParameterString(self.BUCKET, self.tr('R2 bucket name'), defaultValue=st.value(SETTINGS + 'bucket', '')))
        self.addParameter(QgsProcessingParameterString(self.ACCESS_KEY, self.tr('R2 Access Key ID'), defaultValue=st.value(SETTINGS + 'access_key', '')))
        self.addParameter(QgsProcessingParameterString(self.R2_SK, self.tr('R2 Secret Access Key'), defaultValue=st.value(SETTINGS + 'secret', '')))
        self.addParameter(QgsProcessingParameterString(self.PUBLIC_URL, self.tr('Bucket public URL (r2.dev or your domain)'), defaultValue=st.value(SETTINGS + 'public_url', '')))
        self.addParameter(QgsProcessingParameterBoolean(self.REMEMBER, self.tr('Remember these settings on this computer'), defaultValue=True))
        for pid, label in ((self.PROJECT_ID, 'Project id (set by the Web Map Kit window)'), (self.PROJECT, 'Project name')):
            p = QgsProcessingParameterString(pid, self.tr(label), optional=True)
            p.setFlags(p.flags() | _ADVANCED)
            self.addParameter(p)

    def processAlgorithm(self, parameters, context, feedback):
        folder = self.parameterAsFile(parameters, self.FOLDER, context)
        if not folder or not os.path.exists(os.path.join(folder, 'index.html')) or (not os.path.exists(os.path.join(folder, 'config.json'))):
            raise QgsProcessingException("This folder is not a web map: it needs index.html and config.json. Run 'Export web map' first.")
        name = slug(self.parameterAsString(parameters, self.MAP_NAME, context) or os.path.basename(os.path.normpath(folder)))
        account = self.parameterAsString(parameters, self.ACCOUNT, context).strip()
        bucket = self.parameterAsString(parameters, self.BUCKET, context).strip()
        ak = self.parameterAsString(parameters, self.ACCESS_KEY, context).strip()
        sk = self.parameterAsString(parameters, self.R2_SK, context).strip()
        public = self.parameterAsString(parameters, self.PUBLIC_URL, context).strip().rstrip('/')
        missing = [n for n, v in [('Account ID', account), ('bucket', bucket), ('Access Key ID', ak), ('Secret Access Key', sk)] if not v]
        if missing:
            raise QgsProcessingException("Missing: %s. See the set-up steps in the tool's help." % ', '.join(missing))
        if public and (not public.startswith('http')):
            public = 'https://' + public
        if self.parameterAsBool(parameters, self.REMEMBER, context):
            st = QgsSettings()
            for k, v in [('account', account), ('bucket', bucket), ('access_key', ak), ('secret', sk), ('public_url', public)]:
                st.setValue(SETTINGS + k, v)
        client = R2Client(account, ak, sk, endpoint=os.environ.get('WEBMAPKIT_R2_ENDPOINT'))
        feedback.setProgressText('Checking the bucket…')
        status = client.bucket_exists(bucket)
        if status == 404:
            raise QgsProcessingException('Bucket "%s" was not found. Create it in Cloudflare > R2 first (check the spelling).' % bucket)
        if status in (401, 403):
            raise QgsProcessingException('Cloudflare refused the keys (HTTP %d). Check the Account ID, Access Key ID and Secret, and that the token has Object Read & Write on this bucket.' % status)
        files = []
        for root, dirs, names in os.walk(folder):
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            for n in names:
                rel = os.path.relpath(os.path.join(root, n), folder).replace(os.sep, '/')
                if n in SKIP_FILES or any((p.search(rel) for p in SKIP_PATTERNS)):
                    continue
                files.append(rel)
        files.sort(key=lambda r: (r.endswith('.html'), r.endswith('config.json'), r))
        total = sum((os.path.getsize(os.path.join(folder, f)) for f in files)) or 1
        feedback.setProgressText('Uploading %d files (%.1f MB)…' % (len(files), total / 1048576.0))
        done = 0
        for rel in files:
            if feedback.isCanceled():
                return {}
            ext = os.path.splitext(rel)[1].lower()
            ctype = TYPES.get(ext) or mimetypes.guess_type(rel)[0] or 'application/octet-stream'
            cache = 'public, max-age=60' if ext in ('.html', '.json') else 'public, max-age=3600'
            done += client.put_file(bucket, '%s/%s' % (name, rel), os.path.join(folder, rel), ctype, cache)
            feedback.setProgress(100.0 * done / total)
            feedback.pushInfo('  uploaded %s' % rel)
        manifest = {'title': _title_of(folder), 'project': self.parameterAsString(parameters, self.PROJECT, context), 'project_id': self.parameterAsString(parameters, self.PROJECT_ID, context), 'published': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'files': len(files), 'tool': 'Web Map Kit'}
        client.put_bytes(bucket, '%s/%s' % (name, MANIFEST), json.dumps(manifest).encode('utf-8'), 'application/json')
        keep = set(files) | {MANIFEST}
        try:
            stale = [o['key'] for o in client.list(bucket, name + '/') if o['key'].split('/', 1)[1] not in keep]
            for k in stale:
                client.delete(bucket, k)
            if stale:
                feedback.pushInfo('  removed %d old file%s' % (len(stale), '' if len(stale) == 1 else 's'))
        except Exception as e:
            feedback.pushInfo("  (couldn't tidy old files: %s)" % e)
        link = '%s/%s/index.html' % (public, name) if public else None
        feedback.pushInfo('')
        feedback.pushInfo("Published %d files to bucket '%s' under '%s/'." % (len(files), bucket, name))
        if link:
            feedback.pushInfo('Your map: %s' % link)
            self._check_live(link, feedback)
        else:
            feedback.pushInfo("Add the bucket's public URL to get your link: <public URL>/%s/index.html" % name)
        return {'URL': link or '', 'FILES': len(files), 'NAME': name}

    def _check_live(self, link, feedback):
        ua = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 WebMapKit-QGIS', 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8', 'Accept-Language': 'en'}

        def fetch(url, extra=None, tries=3):
            last = None
            for i in range(tries):
                try:
                    req = urllib.request.Request(url, headers=dict(ua, **extra or {}))
                    with open_url(req, timeout=20) as r:
                        return (r.status, r.read(7))
                except urllib.error.HTTPError as e:
                    last = e
                    if e.code == 404 and i < tries - 1:
                        time.sleep(2)
                        continue
                    return (e.code, b'')
                except Exception as e:
                    last = e
                    if i < tries - 1:
                        time.sleep(2)
            raise last
        try:
            page, _ = fetch(link)
            tiles = link.rsplit('/', 1)[0] + '/data/data.pmtiles'
            rng, head = fetch(tiles, {'Range': 'bytes=0-126'})
        except Exception as e:
            feedback.pushInfo("Uploaded. The live check couldn't reach %s (%s). Open the link to see your map; if public access was just enabled, give it a minute." % (link, e))
            return
        if page == 200 and rng == 206 and (head == b'PMTiles'):
            feedback.pushInfo('Checked: the page and the map data are live.')
        elif 403 in (page, rng):
            feedback.pushInfo("Uploaded. Cloudflare's bot protection blocked the automatic check (this is normal and doesn't affect visitors). Open the link to see your map.")
        elif page == 200 and rng == 200:
            feedback.reportError("Uploaded, but the map data is served without range requests, so the map may not load. Check that the bucket's custom domain isn't behind a cache rule that strips Range headers.", False)
        else:
            feedback.reportError("Uploaded, but the live check didn't pass (page %s, data %s). Check that public access is enabled on the bucket, then open the link." % (page, rng), False)
