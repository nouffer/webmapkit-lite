# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import os
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider
from .export_alg import ExportWebMap
from .publish_alg import PublishWebMap
from .edition import PROVIDER_ID, NAME
ICON = os.path.join(os.path.dirname(__file__), 'icon.svg')

class WebMapKitProvider(QgsProcessingProvider):

    def loadAlgorithms(self):
        self.addAlgorithm(ExportWebMap())
        self.addAlgorithm(PublishWebMap())

    def id(self):
        return PROVIDER_ID

    def name(self):
        return NAME

    def icon(self):
        return QIcon(ICON)

    def longName(self):
        return NAME + ': QGIS to web map'
