# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import os
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication
from .net import open_url
from .preview import PreviewServer
from .provider import WebMapKitProvider
from .edition import NAME, UPGRADE_TEXT, open_upgrade
HERE = os.path.dirname(__file__)
MENU = '&Web Map Kit Lite'
GUIDE_URL = 'https://mapship.link/webmap-kit/guide/'

def open_guide(*_):
    import urllib.request
    try:
        open_url(urllib.request.Request(GUIDE_URL, method='HEAD'), timeout=3).close()
        QDesktopServices.openUrl(QUrl(GUIDE_URL))
    except Exception:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.join(HERE, 'help', 'index.html')))

class WebMapKitPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []
        self.preview = PreviewServer()

    def initProcessing(self):
        self.provider = WebMapKitProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        icon = QIcon(os.path.join(HERE, 'icon.svg'))
        main = QAction(icon, 'Publish web map…', self.iface.mainWindow())
        main.setToolTip(NAME + ': turn this project into a web map')
        main.triggered.connect(self.open_dialog)
        maps = QAction(UPGRADE_TEXT + '…', self.iface.mainWindow())
        maps.setToolTip('Photos in popups, your own domain, all your maps in one list, no badge')
        maps.triggered.connect(open_upgrade)
        hosting = QAction('Hosting settings…', self.iface.mainWindow())
        hosting.triggered.connect(self.open_hosting)
        help_ = QAction('Guide and help', self.iface.mainWindow())
        help_.triggered.connect(open_guide)
        for a in (main, hosting, help_, maps):
            self.iface.addPluginToWebMenu(MENU, a)
            self.actions.append(a)
        from qgis.PyQt.QtWidgets import QMenu, QToolButton
        self.tb_menu = QMenu()
        self.tb_menu.addAction(main)
        self.tb_menu.addSeparator()
        for a in (hosting, help_, maps):
            self.tb_menu.addAction(a)
        self.tb_button = QToolButton()
        self.tb_button.setDefaultAction(main)
        self.tb_button.setMenu(self.tb_menu)
        self.tb_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.toolbar_action = self.iface.addWebToolBarWidget(self.tb_button)

    def unload(self):
        for a in self.actions:
            self.iface.removePluginWebMenu(MENU, a)
        if getattr(self, 'toolbar_action', None):
            self.iface.removeWebToolBarIcon(self.toolbar_action)
            self.tb_button.deleteLater()
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
        self.preview.stop()

    def open_dialog(self):
        from .dialog import WebMapDialog
        dlg = WebMapDialog(self.iface, self.preview)
        dlg.exec_()

    def open_hosting(self):
        from .setup_wizard import SetupWizard
        SetupWizard(self.iface.mainWindow()).exec_()
