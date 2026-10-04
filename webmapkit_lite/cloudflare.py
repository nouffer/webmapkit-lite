# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
API = os.environ.get('WEBMAPKIT_CF_API', 'https://api.cloudflare.com/client/v4').rstrip('/')
CF_KEY_LABEL = 'Web Map Kit'
PERMISSIONS = [{'key': 'workers_r2', 'type': 'edit'}]
DASH = 'https://dash.cloudflare.com'
SIGNUP_URL = DASH + '/sign-up'

def token_url():
    q = urllib.parse.urlencode({'permissionGroupKeys': json.dumps(PERMISSIONS, separators=(',', ':')), 'accountId': '*', 'zoneId': 'all', 'name': CF_KEY_LABEL})
    return DASH + '/profile/api-tokens?' + q

def r2_url(account_id=None):
    return '%s/%s/r2/overview' % (DASH, account_id) if account_id else DASH + '/?to=/:account/r2/overview'

def s3_keys(token_id, token):
    return (token_id, hashlib.sha256(token.strip().encode('utf-8')).hexdigest())

class CFError(Exception):

    def __init__(self, message, code=None, status=None):
        super().__init__(message)
        self.code, self.status = (code, status)
R2_NOT_ENABLED = {10042}

class Cloudflare:

    def __init__(self, token):
        self.token = token.strip()

    def call(self, method, path, body=None, params=None):
        url = API + path + ('?' + urllib.parse.urlencode(params) if params else '')
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header('Authorization', 'Bearer ' + self.token)
        req.add_header('Content-Type', 'application/json')
        req.add_header('User-Agent', 'WebMapKit-QGIS')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, raw = (r.status, r.read())
        except urllib.error.HTTPError as e:
            status, raw = (e.code, e.read())
        except urllib.error.URLError as e:
            raise CFError("Couldn't reach Cloudflare. Check your internet connection. (%s)" % e.reason)
        try:
            doc = json.loads(raw.decode('utf-8') or '{}')
        except ValueError:
            raise CFError('Cloudflare sent an unexpected answer (HTTP %s).' % status, status=status)
        if not doc.get('success', 200 <= status < 300):
            errs = doc.get('errors') or [{}]
            e = errs[0] if isinstance(errs[0], dict) else {}
            raise CFError(e.get('message') or 'Cloudflare said no (HTTP %s).' % status, code=e.get('code'), status=status)
        return doc.get('result')

    def verify(self):
        r = self.call('GET', '/user/tokens/verify') or {}
        if r.get('status') not in (None, 'active'):
            raise CFError('This token is %s. Create a new one.' % r.get('status'))
        return r.get('id')

    def accounts(self):
        return [{'id': a['id'], 'name': a.get('name') or a['id']} for a in self.call('GET', '/accounts', params={'per_page': 50}) or []]

    def buckets(self, account):
        r = self.call('GET', '/accounts/%s/r2/buckets' % account, params={'per_page': 1000}) or {}
        items = r.get('buckets', r) if isinstance(r, dict) else r
        return [b['name'] for b in items or []]

    def bucket_jurisdiction(self, account, bucket):
        try:
            r = self.call('GET', '/accounts/%s/r2/buckets/%s' % (account, bucket)) or {}
        except CFError:
            return ''
        j = (r.get('jurisdiction') or '').lower()
        return '' if j in ('', 'default') else j

    def create_bucket(self, account, name):
        self.call('POST', '/accounts/%s/r2/buckets' % account, body={'name': name})

    def public_address(self, account, bucket, enable=True):
        path = '/accounts/%s/r2/buckets/%s/domains/managed' % (account, bucket)
        r = self.call('GET', path) or {}
        if enable and (not r.get('enabled')):
            r = self.call('PUT', path, body={'enabled': True}) or {}
        dom = r.get('domain')
        return 'https://' + dom if dom else ''

def detail(e):
    reason = getattr(e, 'reason', None)
    return '%s: %s' % (e.__class__.__name__, reason if reason is not None else e)

def friendly(e, step=''):
    msg = str(e)
    low = msg.lower()
    code = getattr(e, 'code', None)
    status = getattr(e, 'status', None)
    if low.startswith('upload test'):
        return "Your account is set up, but the upload server didn't answer. Click Try again in a minute. If it keeps failing, send us the details below."
    if 'urlopen error' in low or 'timed out' in low or 'connection refused' in low or ("couldn't reach" in low):
        return "Couldn't reach Cloudflare. Check your internet connection and try again."
    if code in R2_NOT_ENABLED or 'enable r2' in low:
        return "R2 isn't switched on in this Cloudflare account yet."
    if status in (401, 403) or code in (1000, 9109, 10000, 6003, 6111):
        return "Cloudflare didn't accept this token. Copy it again (it is shown only once) or create a new one."
    return msg
