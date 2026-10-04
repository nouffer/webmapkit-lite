# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import datetime
import uuid
from qgis.PyQt.QtCore import QThread, pyqtSignal
from qgis.core import QgsProject
SCOPE = 'webmapkit'

def project_map_id(project=None, create=True):
    project = project or QgsProject.instance()
    pid = project.readEntry(SCOPE, 'project_id', '')[0]
    if not pid and create:
        pid = uuid.uuid4().hex[:12]
        project.writeEntry(SCOPE, 'project_id', pid)
    return pid

def ago(dt):
    if not dt:
        return 'unknown date'
    secs = (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()
    if secs < 60:
        return 'just now'
    if secs < 3600:
        return '%d min ago' % (secs // 60)
    if secs < 86400:
        h = int(secs // 3600)
        return '%d hour%s ago' % (h, '' if h == 1 else 's')
    if secs < 2 * 86400:
        return 'yesterday'
    if secs < 14 * 86400:
        return '%d days ago' % (secs // 86400)
    return dt.astimezone().strftime('%d %b %Y')

class Worker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    step = pyqtSignal(object)

    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self.fn, self.args = (fn, args)

    def run(self):
        try:
            self.done.emit(self.fn(*self.args))
        except Exception as e:
            self.error = e
            self.failed.emit(str(e) or e.__class__.__name__)

def friendly_error(msg):
    low = msg.lower()
    if 'urlopen error' in low or 'timed out' in low or 'name or service' in low or ('nodename' in low):
        return "Couldn't reach your hosting. Check your internet connection and try again."
    return msg
