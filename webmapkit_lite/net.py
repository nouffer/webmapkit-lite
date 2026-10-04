# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import urllib.request
_OPENER = urllib.request.OpenerDirector()
for _h in (urllib.request.ProxyHandler(), urllib.request.HTTPHandler(), urllib.request.HTTPSHandler(), urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPRedirectHandler(), urllib.request.HTTPErrorProcessor()):
    _OPENER.add_handler(_h)

def open_url(req, timeout=30):
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    if not url.lower().startswith(('https://', 'http://')):
        raise ValueError('Only web addresses can be opened: %s' % url)
    return _OPENER.open(req, timeout=timeout)
