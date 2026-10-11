# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings
PREFIX = 'webmapkit/r2/'
AUTH_ID = PREFIX + 'auth_id'
PLAIN = ('account', 'bucket', 'public_url', 'account_name', 'jurisdiction')
SECRET = ('access_key', 'secret', 'api_token')
KEYS = PLAIN + SECRET
ENTRY_NAME = 'Web Map Kit hosting (Cloudflare)'

def _mgr():
    try:
        m = QgsApplication.authManager()
    except Exception:
        return None
    return m if m is not None and (not m.isDisabled()) else None

def vault_id():
    return (QgsSettings().value(AUTH_ID, '') or '').strip()

def in_vault():
    return bool(vault_id())

def _read_vault():
    m, aid = (_mgr(), vault_id())
    if not m or not aid:
        return None
    cfg = QgsAuthMethodConfig()
    if not m.loadAuthenticationConfig(aid, cfg, True) or not cfg.id():
        return None
    return {k: (cfg.config(k, '') or '').strip() for k in SECRET}

def _write_vault(secrets):
    m = _mgr()
    if not m:
        return False
    current = _read_vault() or {}
    current.update({k: v for k, v in secrets.items() if k in SECRET})
    cfg = QgsAuthMethodConfig('Basic')
    cfg.setName(ENTRY_NAME)
    cfg.setConfig('username', current.get('access_key', ''))
    cfg.setConfig('password', current.get('secret', ''))
    for k in SECRET:
        cfg.setConfig(k, current.get(k, '') or '')
    aid = vault_id()
    if aid:
        cfg.setId(aid)
        ok = m.updateAuthenticationConfig(cfg)
    else:
        ok = m.storeAuthenticationConfig(cfg)
        if ok and cfg.id():
            QgsSettings().setValue(AUTH_ID, cfg.id())
    return bool(ok and cfg.id())

def load():
    st = QgsSettings()
    out = {k: (st.value(PREFIX + k, '') or '').strip() for k in KEYS}
    if in_vault():
        secrets = _read_vault()
        if secrets is not None:
            out.update(secrets)
    return out

def save(**values):
    st = QgsSettings()
    secrets = {k: values[k] or '' for k in SECRET if k in values}
    for k, v in values.items():
        if k in PLAIN:
            st.setValue(PREFIX + k, v or '')
    if secrets:
        if in_vault() and _write_vault(secrets):
            for k in secrets:
                st.remove(PREFIX + k)
        else:
            for k, v in secrets.items():
                st.setValue(PREFIX + k, v)
