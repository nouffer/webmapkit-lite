# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
def classFactory(iface):
    from .plugin import WebMapKitPlugin
    return WebMapKitPlugin(iface)
