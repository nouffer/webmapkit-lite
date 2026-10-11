# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
NAME = 'Web Map Kit Lite'
PROVIDER_ID = 'webmapkitlite'
UPGRADE_URL = 'https://mapship.link/webmap-kit/?utm_source=qgis-lite&utm_medium=plugin'
UPGRADE_TEXT = 'Upgrade to Web Map Kit'
UPGRADE_PITCH = 'Web Map Kit (full) adds response forms (people send answers and photos about places on your map back into QGIS, even without signal), maps saved on phones for field work, photos in popups, your own domain, a list of all your published maps, and no “Made with Web Map Kit” link. $17 once.'
FORMS_URL = 'https://mapship.link/webmap-kit/?utm_source=qgis-lite&utm_medium=plugin&utm_content=done-forms#forms'

def open_upgrade(*_):
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices
    QDesktopServices.openUrl(QUrl(UPGRADE_URL))
