# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import contextlib
import os
import re
import time
from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QFont, QGuiApplication
from qgis.PyQt.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget
from qgis.core import QgsIconUtils, QgsProcessingContext, QgsProcessingException, QgsProcessingFeedback, QgsProject, QgsVectorLayer
from .export_alg import geometry_kind, plan_zooms
from .common import Worker, ago, friendly_error, project_map_id
from .edition import PROVIDER_ID, NAME, UPGRADE_PITCH, UPGRADE_URL, open_upgrade
from .publish_alg import client_from_settings, map_info, slug, unique_name
from .settings_dialog import hosting_ready, load_hosting
SCOPE = 'webmapkit'

def short_slug(text, limit=40):
    s = slug(text)
    if len(s) <= limit:
        return s
    cut = s[:limit]
    return (cut.rsplit('-', 1)[0] if '-' in cut else cut) or s[:limit]
BASEMAPS = [('Light grey', 'positron'), ('Streets', 'liberty'), ('Bright', 'bright'), ('No background map', 'none')]
MARKERS = [('Circles (like QGIS)', 0), ('Round icon badges', 1), ('Map pins', 2)]
DETAIL = [('Automatic (recommended)', 0), ('Overview: world or country', 8), ('Region', 10), ('City', 12), ('Neighbourhood', 14), ('Street level', 16)]
CSS = '\nQLabel#h1 { font-size: 17px; font-weight: 600; }\nQLabel#sub { color: #667085; }\nQLabel#section { font-weight: 600; font-size: 13px; margin-top: 4px; }\nQLabel#hint { color: #667085; }\nQLabel#ok { color: #067647; }\nQLabel#warn { color: #b42318; }\nQFrame#line { color: #eaecf0; }\nQPushButton#primary { font-weight: 600; padding: 6px 16px; }\nQToolButton#link { border: none; color: #175cd3; padding: 0 2px; }\nQToolButton#link:hover { text-decoration: underline; }\nQLabel#doneTitle { font-size: 18px; font-weight: 600; }\nQLabel#prefix { color: #667085; }\nQLabel#addr { padding: 6px 10px; border-radius: 6px; background: #f2f4f7; color: #344054; }\nQLabel#addr[kind="new"] { background: #ecfdf3; color: #067647; }\nQLabel#addr[kind="mine"] { background: #eff8ff; color: #175cd3; }\nQLabel#addr[kind="taken"] { background: #fffaeb; color: #93370d; }\nQPushButton#danger { font-weight: 600; padding: 5px 14px; color: white; background: #d92d20; border: 1px solid #b42318; border-radius: 5px; }\n'

def _hline():
    f = QFrame()
    f.setObjectName('line')
    f.setFrameShape(QFrame.HLine)
    f.setFrameShadow(QFrame.Plain)
    return f

def _label(text, name=None, wrap=True):
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    l.setWordWrap(wrap)
    return l

def _link(text, slot):
    b = QToolButton()
    b.setObjectName('link')
    b.setText(text)
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    return b

class _Feedback(QgsProcessingFeedback):

    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self._t = 0
        self.progressChanged.connect(self._progress)

    def _pump(self, force=False):
        now = time.monotonic()
        if force or now - self._t > 0.05:
            self._t = now
            QApplication.processEvents()

    def _progress(self, p):
        if p > 0 and self.dlg.bar.maximum() == 0:
            self.dlg.bar.setRange(0, 100)
        self.dlg.bar.setValue(int(p))
        self._pump()

    def setProgressText(self, text):
        super().setProgressText(text)
        self.dlg.step.setText(text)
        self._pump(True)

    def pushInfo(self, msg):
        super().pushInfo(msg)
        self.dlg.log.appendPlainText(msg)
        self._pump()

    def pushWarning(self, msg):
        with contextlib.suppress(Exception):
            super().pushWarning(msg)
        self.dlg.log.appendPlainText('⚠ ' + msg)
        self.dlg.warnings.append(msg)
        self._pump()

    def reportError(self, msg, fatalError=False):
        super().reportError(msg, fatalError)
        self.dlg.log.appendPlainText('✖ ' + msg)
        self.dlg.warnings.append(msg)
        self._pump()

class WebMapDialog(QDialog):

    def __init__(self, iface=None, preview=None, parent=None):
        super().__init__(parent or (iface.mainWindow() if iface else None))
        self.iface, self.preview = (iface, preview)
        self.addr_state, self.addr_info, self._name_touched, self.workers = (None, None, False, set())
        self.addr_timer = QTimer(self)
        self.addr_timer.setSingleShot(True)
        self.addr_timer.setInterval(600)
        self.addr_timer.timeout.connect(self._check_address)
        self.project = QgsProject.instance()
        self.feedback, self.warnings, self.result_url, self.result_dir = (None, [], '', '')
        self.setWindowTitle('Web Map Kit')
        self.setStyleSheet(CSS)
        self.setMinimumSize(640, 680)
        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)
        self.stack.addWidget(self._form_page())
        self.stack.addWidget(self._progress_page())
        self.stack.addWidget(self._done_page())
        self._restore()
        self._refresh_hosting()
        self._update_estimate()
        self._publish_toggled(self.publish.isChecked())

    def _form_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(22, 18, 22, 16)
        v.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(_label('Publish a web map', 'h1', wrap=False))
        top.addStretch(1)
        top.addWidget(_link('Upgrade', open_upgrade))
        top.addWidget(_label('·', 'hint', wrap=False))
        top.addWidget(_link('Hosting', self._hosting))
        top.addWidget(_label('·', 'hint', wrap=False))
        top.addWidget(_link('Help', self._help))
        v.addLayout(top)
        v.addWidget(_label('Turn this QGIS project into a fast, shareable web map, with your colours, legend, popups and search.', 'sub'))
        v.addWidget(_hline())
        v.addWidget(_label('Map', 'section'))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.title = QLineEdit()
        self.title.setPlaceholderText('e.g. Power plants of the world')
        self.desc = QLineEdit()
        self.desc.setPlaceholderText('Optional: one line under the title')
        form.addRow('Title', self.title)
        form.addRow('Description', self.desc)
        v.addLayout(form)
        lrow = QHBoxLayout()
        lrow.addWidget(_label('Layers', 'section', wrap=False))
        lrow.addStretch(1)
        lrow.addWidget(_link('Visible', lambda: self._check_layers('visible')))
        lrow.addWidget(_label('·', 'hint', wrap=False))
        lrow.addWidget(_link('All', lambda: self._check_layers('all')))
        lrow.addWidget(_label('·', 'hint', wrap=False))
        lrow.addWidget(_link('None', lambda: self._check_layers('none')))
        v.addLayout(lrow)
        self.layers = QListWidget()
        self.layers.setMinimumHeight(130)
        self.layers.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._fill_layers()
        self.layers.itemChanged.connect(lambda *_: self._update_estimate())
        v.addWidget(self.layers, 1)
        v.addWidget(_label('Look', 'section'))
        look = QFormLayout()
        look.setLabelAlignment(Qt.AlignRight)
        look.setHorizontalSpacing(12)
        look.setVerticalSpacing(8)
        self.basemap = QComboBox()
        for name, _ in BASEMAPS:
            self.basemap.addItem(name)
        self.markers = QComboBox()
        for name, _ in MARKERS:
            self.markers.addItem(name)
        self.detail = QComboBox()
        for name, _ in DETAIL:
            self.detail.addItem(name)
        self.detail.currentIndexChanged.connect(lambda *_: self._update_estimate())
        self.search = QCheckBox('Add a search box')
        self.search.setChecked(True)
        self.startview = QComboBox()
        self.startview.addItem('What you see in QGIS now')
        self.startview.addItem('Fit all layers')
        self.startview.setToolTip('Where the web map opens. Zoom QGIS to the area you want people to see first, then publish.')
        self.estimate = _label('', 'hint')
        look.addRow('Background', self.basemap)
        look.addRow('Points', self.markers)
        look.addRow('Detail', self.detail)
        look.addRow('Opens at', self.startview)
        look.addRow('', self.estimate)
        look.addRow('', self.search)
        v.addLayout(look)
        v.addWidget(_label('Save and publish', 'section'))
        drow = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText('Folder for the web map files')
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse)
        drow.addWidget(_label('Folder', wrap=False))
        drow.addWidget(self.folder, 1)
        drow.addWidget(browse)
        v.addLayout(drow)
        prow = QHBoxLayout()
        self.publish = QCheckBox('Publish online and get a shareable link')
        self.host_status = _label('', 'hint', wrap=False)
        prow.addWidget(self.publish)
        prow.addStretch(1)
        prow.addWidget(self.host_status)
        prow.addWidget(_link('Hosting settings…', self._hosting))
        v.addLayout(prow)
        self.addr_box = QWidget()
        av = QVBoxLayout(self.addr_box)
        av.setContentsMargins(22, 0, 0, 0)
        av.setSpacing(6)
        arow = QHBoxLayout()
        arow.setSpacing(4)
        arow.addWidget(_label('Web address', wrap=False))
        arow.addSpacing(8)
        self.addr_prefix = _label('', 'prefix', wrap=False)
        self.mapname = QLineEdit()
        self.mapname.setPlaceholderText('map-name')
        self.mapname.setToolTip('The part of the link that names this map. Letters, numbers and dashes.')
        self.mapname.textEdited.connect(self._name_edited)
        arow.addWidget(self.addr_prefix)
        arow.addWidget(self.mapname, 1)
        av.addLayout(arow)
        srow = QHBoxLayout()
        self.addr_status = _label('', 'addr')
        self.addr_status.setTextFormat(Qt.RichText)
        self.addr_status.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.addr_status.linkActivated.connect(self._addr_link)
        srow.addWidget(self.addr_status, 1)
        av.addLayout(srow)
        v.addWidget(self.addr_box)
        self.publish.toggled.connect(self._publish_toggled)
        v.addWidget(_hline())
        brow = QHBoxLayout()
        brow.addWidget(_link('Help', self._help))
        brow.addStretch(1)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        self.go = QPushButton('Create web map')
        self.go.setObjectName('primary')
        self.go.setDefault(True)
        self.go.clicked.connect(self.run)
        brow.addWidget(close)
        brow.addWidget(self.go)
        v.addLayout(brow)
        return w

    def _progress_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(22, 40, 22, 16)
        v.setSpacing(12)
        self.run_title = _label('Creating your web map…', 'h1')
        v.addWidget(self.run_title)
        self.step = _label('Starting…', 'sub')
        v.addWidget(self.step)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        v.addWidget(self.bar)
        v.addWidget(_label('Large maps can take a few minutes. You can keep this window open while it works.', 'hint'))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        f = QFont('Menlo')
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(max(9, f.pointSize() - 1))
        self.log.setFont(f)
        self.log.setVisible(False)
        tog = _link('Show details', lambda: (self.log.setVisible(not self.log.isVisible()), tog.setText('Hide details' if self.log.isVisible() else 'Show details')))
        v.addWidget(tog, 0, Qt.AlignLeft)
        v.addWidget(self.log, 1)
        v.addStretch(1)
        brow = QHBoxLayout()
        brow.addStretch(1)
        self.cancel_btn = QPushButton('Cancel')
        self.cancel_btn.clicked.connect(self._cancel)
        brow.addWidget(self.cancel_btn)
        v.addLayout(brow)
        return w

    def _done_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(22, 30, 22, 16)
        v.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.done_icon = QLabel('✓')
        self.done_icon.setFixedSize(40, 40)
        self.done_icon.setAlignment(Qt.AlignCenter)
        self.done_icon.setStyleSheet('background:#dcfae6; color:#067647; border-radius:20px; font-size:20px; font-weight:700;')
        head.addWidget(self.done_icon, 0, Qt.AlignTop)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        self.done_title = _label('Your web map is ready', 'doneTitle')
        self.done_text = _label('', 'sub')
        tv.addWidget(self.done_title)
        tv.addWidget(self.done_text)
        head.addLayout(tv, 1)
        v.addLayout(head)
        v.addSpacing(4)
        self.link_row = QWidget()
        lr = QHBoxLayout(self.link_row)
        lr.setContentsMargins(0, 0, 0, 0)
        self.link = QLineEdit()
        self.link.setReadOnly(True)
        self.link.setMinimumHeight(30)
        copy = QPushButton('Copy link')
        copy.clicked.connect(self._copy)
        openb = QPushButton('Open map')
        openb.setObjectName('primary')
        openb.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.result_url)))
        lr.addWidget(self.link, 1)
        lr.addWidget(copy)
        lr.addWidget(openb)
        v.addWidget(self.link_row)
        arow = QHBoxLayout()
        self.preview_btn = QPushButton('Preview on this computer')
        self.preview_btn.clicked.connect(self._preview)
        folder_btn = QPushButton('Open folder')
        folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.result_dir)))
        self.publish_now = QPushButton('Publish online…')
        self.publish_now.clicked.connect(self._publish_now)
        arow.addWidget(self.preview_btn)
        arow.addWidget(self.publish_now)
        arow.addWidget(folder_btn)
        self.maps_btn = QPushButton('Remove the badge, add photos…')
        self.maps_btn.setToolTip(UPGRADE_PITCH)
        self.maps_btn.clicked.connect(open_upgrade)
        arow.addWidget(self.maps_btn)
        arow.addStretch(1)
        v.addLayout(arow)
        self.notes = _label('', 'hint')
        self.notes.setTextFormat(Qt.RichText)
        self.notes.setOpenExternalLinks(True)
        v.addWidget(self.notes)
        v.addWidget(_hline())
        v.addWidget(_label("What's next", 'section'))
        tips = _label('', 'hint')
        tips.setTextFormat(Qt.RichText)
        tips.setText('• <b>Share it:</b> the link works on phones and computers, no login needed.<br>• <b>Update it:</b> change your data or styles in QGIS and run Web Map Kit again. The same link updates.<br>• <b>Fine-tune it:</b> colours, popups and labels can be adjusted in <i>config.json</i> in the folder (see CONFIG.md), and the look in <i>style.css</i>.')
        v.addWidget(tips)
        v.addStretch(1)
        brow = QHBoxLayout()
        back = QPushButton('Change settings')
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        brow.addWidget(back)
        brow.addStretch(1)
        brow.addWidget(close)
        v.addLayout(brow)
        return w

    def _vector_layers(self):
        root = self.project.layerTreeRoot()
        out = []
        for node in root.findLayers():
            lyr = node.layer()
            if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and geometry_kind(lyr):
                out.append((lyr, node.isVisible()))
        return out

    def _fill_layers(self):
        self.layers.clear()
        for lyr, visible in self._vector_layers():
            n = lyr.featureCount()
            count = '· %s feature%s' % (format(n, ','), '' if n == 1 else 's') if n >= 0 else ''
            it = QListWidgetItem(QgsIconUtils.iconForLayer(lyr), '%s   %s' % (lyr.name(), count))
            it.setData(Qt.UserRole, lyr.id())
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if visible else Qt.Unchecked)
            self.layers.addItem(it)

    def _check_layers(self, mode):
        vis = {l.id(): v for l, v in self._vector_layers()}
        self.layers.blockSignals(True)
        for i in range(self.layers.count()):
            it = self.layers.item(i)
            on = mode == 'all' or (mode == 'visible' and vis.get(it.data(Qt.UserRole)))
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.layers.blockSignals(False)
        self._update_estimate()

    def _chosen_layers(self):
        ids = [self.layers.item(i).data(Qt.UserRole) for i in range(self.layers.count()) if self.layers.item(i).checkState() == Qt.Checked]
        return [self.project.mapLayer(i) for i in ids if self.project.mapLayer(i)]

    def _update_estimate(self):
        layers = self._chosen_layers()
        if not layers:
            self.estimate.setObjectName('warn')
            self.estimate.setText('Tick at least one layer.')
        else:
            ctx = QgsProcessingContext()
            ctx.setProject(self.project)
            try:
                z, est = plan_zooms(layers, 0, DETAIL[self.detail.currentIndex()][1], ctx, QgsProcessingFeedback())
                speed = 'quick' if est < 20000 else 'a few minutes' if est < 120000 else 'several minutes'
                self.estimate.setObjectName('hint')
                self.estimate.setText('Detail level %d · up to %s tiles · %s' % (z, format(est, ','), speed))
            except QgsProcessingException:
                self.estimate.setObjectName('warn')
                self.estimate.setText('Too detailed for an area this large. Choose Automatic or a lower level.')
        self.estimate.style().unpolish(self.estimate)
        self.estimate.style().polish(self.estimate)
        self.go.setEnabled(bool(layers) and self.estimate.objectName() != 'warn')

    def _default_folder(self):
        base = os.path.join(os.path.expanduser('~'), 'Documents', 'Web Map Kit')
        return os.path.join(base, slug(self.title.text() or 'my-map'))

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, 'Choose a folder for the web map', self.folder.text() or os.path.expanduser('~'))
        if d:
            self.folder.setText(d)

    def _refresh_hosting(self):
        h = load_hosting()
        host = h['public_url'].split('://', 1)[-1] if h['public_url'] else h['bucket'] or 'your-hosting'
        self.addr_prefix.setText(host + '/')
        if hosting_ready(h):
            self.host_status.setText('✓ %s' % h['bucket'])
            self.publish.setEnabled(True)
        else:
            self.host_status.setText('Not set up yet')
            self.publish.setChecked(False)
            self.publish.setEnabled(False)
        self._publish_toggled(self.publish.isChecked())

    def _hosting(self):
        from .setup_wizard import SetupWizard
        if SetupWizard(self).exec_():
            self._refresh_hosting()
            if hosting_ready():
                self.publish.setChecked(True)

    def _help(self):
        from .plugin import open_guide
        open_guide()

    def _copy(self):
        QGuiApplication.clipboard().setText(self.result_url)
        self.notes.setText('Link copied.')

    def _preview(self):
        try:
            url = self.preview.url_for(self.result_dir)
            QDesktopServices.openUrl(QUrl(url))
        except Exception as e:
            self.notes.setText("Couldn't start the preview: %s" % e)

    def _cancel(self):
        if self.feedback:
            self.feedback.cancel()
            self.step.setText('Cancelling…')
            self.cancel_btn.setEnabled(False)

    def _map_name(self):
        return short_slug(self.mapname.text().strip() or self.title.text().strip() or 'my-map', 60)

    def _name_edited(self, text):
        self._name_touched = True
        clean = re.sub('[^a-z0-9-]+', '-', text.lower())
        if clean != text:
            pos = self.mapname.cursorPosition()
            self.mapname.setText(clean)
            self.mapname.setCursorPosition(pos)
        self._address_changed()

    def _publish_toggled(self, on):
        self.addr_box.setVisible(bool(on) and hosting_ready())
        if on:
            self._address_changed()

    def _address_changed(self):
        if not (self.publish.isChecked() and hosting_ready()):
            return
        self._set_addr(None, 'Checking this address…')
        self.addr_timer.start()

    def _set_addr(self, kind, html):
        self.addr_state = kind
        self.addr_status.setProperty('kind', kind or '')
        self.addr_status.style().unpolish(self.addr_status)
        self.addr_status.style().polish(self.addr_status)
        self.addr_status.setText(html)
        QTimer.singleShot(0, self._fit)

    def _fit(self):
        need = self.layout().minimumSize().height() if self.layout() else 0
        need = max(need, self.stack.currentWidget().minimumSizeHint().height())
        if need > self.height():
            self.resize(self.width(), need)

    def _check_address(self):
        name = self._map_name()
        h = load_hosting()
        w = Worker(map_info, client_from_settings(h), h['bucket'], name, parent=self)
        self.workers.add(w)
        w.done.connect(lambda info, n=name: self._addr_result(n, info))
        w.failed.connect(lambda msg, n=name: n == self._map_name() and self._set_addr('error', "Couldn't check this address right now (%s)." % friendly_error(msg)))
        w.finished.connect(lambda: self.workers.discard(w))
        w.start()

    @staticmethod
    def _published_ago(info):
        try:
            import datetime
            dt = datetime.datetime.strptime(info.get('published', ''), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
            return ago(dt)
        except ValueError:
            return ''

    def _addr_result(self, name, info):
        if name != self._map_name():
            return
        self.addr_info = info
        if info is None:
            return self._set_addr('new', '✓ <b>New map.</b> This address is free.')
        when = self._published_ago(info)
        when = ' (published %s)' % when if when else ''
        if info.get('project_id') and info['project_id'] == project_map_id(self.project, create=False):
            return self._set_addr('mine', '↻ <b>Updates your map</b>%s. Same link, new content.' % when)
        src = ' from <i>%s</i>' % info['project'] if info.get('project') else ''
        self._set_addr('taken', '⚠ <b>“%s”</b>%s already uses this address%s. Publishing will replace it. <a href="new">Use a new address instead</a>' % (info['title'], src, when))

    def _addr_link(self, href):
        if href == 'new':
            h = load_hosting()
            try:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                name = unique_name(client_from_settings(h), h['bucket'], self._map_name())
            except Exception as e:
                return self._set_addr('error', "Couldn't find a free address (%s)." % friendly_error(str(e)))
            finally:
                QApplication.restoreOverrideCursor()
            self._name_touched = True
            self.mapname.setText(name)
            self._address_changed()

    def _confirm_address(self):
        name = self._map_name()
        info = self.addr_info if self.addr_state in ('new', 'mine', 'taken') and name == self._map_name() else None
        if self.addr_state not in ('new', 'mine', 'taken'):
            h = load_hosting()
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                info = map_info(client_from_settings(h), h['bucket'], name)
                self._addr_result(name, info)
            except Exception:
                info = None
            finally:
                QApplication.restoreOverrideCursor()
        if info is None or self.addr_state != 'taken':
            return name
        choice = ReplaceDialog(self, info, self.addr_prefix.text() + name + '/', self._published_ago(info)).exec_()
        if choice == ReplaceDialog.NEW:
            h = load_hosting()
            try:
                name = unique_name(client_from_settings(h), h['bucket'], name)
            except Exception:
                name = name + '-2'
            self._name_touched = True
            self.mapname.setText(name)
            return name
        return name if choice == ReplaceDialog.REPLACE else None

    def _restore(self):
        p = self.project
        read = lambda k, d='': p.readEntry(SCOPE, k, d)[0]
        self.title.setText(read('title', p.title() or os.path.splitext(os.path.basename(p.fileName() or 'My web map'))[0]))
        self.desc.setText(read('description'))
        self.folder.setText(read('folder') or self._default_folder())
        self.basemap.setCurrentIndex(int(read('basemap', '0') or 0))
        self.markers.setCurrentIndex(int(read('markers', '0') or 0))
        self.detail.setCurrentIndex(int(read('detail', '0') or 0))
        self.search.setChecked(read('search', '1') == '1')
        self.startview.setCurrentIndex(int(read('startview', '0') or 0))
        self.publish.setChecked(read('publish', '0') == '1')
        saved_name = read('map_name')
        self._name_touched = bool(saved_name) and saved_name != short_slug(self.title.text())
        self.mapname.setText(saved_name or short_slug(self.title.text() or 'my-map'))
        saved = [x for x in read('layers').split(';') if x]
        if saved:
            self.layers.blockSignals(True)
            for i in range(self.layers.count()):
                it = self.layers.item(i)
                it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in saved else Qt.Unchecked)
            self.layers.blockSignals(False)
        self.title.textEdited.connect(self._title_edited)

    def _title_edited(self, text):
        default_base = os.path.join(os.path.expanduser('~'), 'Documents', 'Web Map Kit')
        if os.path.dirname(self.folder.text()) == default_base:
            self.folder.setText(self._default_folder())
        if not self._name_touched:
            self.mapname.setText(short_slug(text or 'my-map'))
            self._address_changed()

    def _save(self):
        p = self.project
        w = lambda k, v: p.writeEntry(SCOPE, k, str(v))
        w('title', self.title.text())
        w('description', self.desc.text())
        w('folder', self.folder.text())
        w('basemap', self.basemap.currentIndex())
        w('markers', self.markers.currentIndex())
        w('detail', self.detail.currentIndex())
        w('search', '1' if self.search.isChecked() else '0')
        w('startview', self.startview.currentIndex())
        w('publish', '1' if self.publish.isChecked() else '0')
        w('layers', ';'.join((l.id() for l in self._chosen_layers())))
        w('map_name', self._map_name())

    def _start(self, title):
        self.warnings = []
        self.log.clear()
        self.bar.setRange(0, 0)
        self.run_title.setText(title)
        self.cancel_btn.setEnabled(True)
        self.stack.setCurrentIndex(1)
        self.feedback = _Feedback(self)
        QApplication.processEvents()

    def _start_view(self):
        if self.startview.currentIndex() != 0 or not self.iface:
            return ''
        try:
            canvas = self.iface.mapCanvas()
            e, crs = (canvas.extent(), canvas.mapSettings().destinationCrs())
            if e.isEmpty() or not crs.isValid():
                return ''
            return '%.10f,%.10f,%.10f,%.10f [%s]' % (e.xMinimum(), e.xMaximum(), e.yMinimum(), e.yMaximum(), crs.authid())
        except Exception:
            return ''

    def run(self):
        import processing
        layers = self._chosen_layers()
        folder = self.folder.text().strip() or self._default_folder()
        title = self.title.text().strip() or 'My web map'
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            self.estimate.setText("Can't use that folder: %s" % e)
            return
        self.publish_name = None
        if self.publish.isChecked() and hosting_ready():
            self.publish_name = self._confirm_address()
            if not self.publish_name:
                return
        self._save()
        self._start('Creating your web map…')
        ctx = QgsProcessingContext()
        ctx.setProject(self.project)
        params = {'LAYERS': layers, 'TITLE': title, 'DESCRIPTION': self.desc.text().strip(), 'MIN_ZOOM': 0, 'MAX_ZOOM': DETAIL[self.detail.currentIndex()][1], 'BASEMAP': self.basemap.currentIndex(), 'POINT_STYLE': MARKERS[self.markers.currentIndex()][1], 'SEARCH': self.search.isChecked(), 'PUBLISH': False, 'OUTPUT': folder}
        view = self._start_view()
        if view:
            params['START_VIEW'] = view
        t0 = time.monotonic()
        try:
            res = processing.run(PROVIDER_ID + ':exportwebmap', params, context=ctx, feedback=self.feedback)
        except Exception as e:
            return self._failed(e)
        if self.feedback.isCanceled():
            self.stack.setCurrentIndex(0)
            return
        self.result_dir, self.result_url = (folder, '')
        tiles = res.get('TILES', 0)
        if self.publish.isChecked() and hosting_ready():
            if not self._do_publish(title, ctx):
                return
        self._show_done(tiles, time.monotonic() - t0)

    def _do_publish(self, title, ctx=None):
        import processing
        h = load_hosting()
        self.step.setText('Publishing online…')
        self.bar.setRange(0, 0)
        if ctx is None:
            ctx = QgsProcessingContext()
            ctx.setProject(self.project)
        try:
            res = processing.run(PROVIDER_ID + ':publishwebmapr2', {'FOLDER': self.result_dir, 'MAP_NAME': getattr(self, 'publish_name', None) or self._map_name(), 'ACCOUNT': h['account'], 'BUCKET': h['bucket'], 'ACCESS_KEY': h['access_key'], 'SECRET': h['secret'], 'PUBLIC_URL': h['public_url'], 'REMEMBER': False, 'PROJECT_ID': project_map_id(self.project), 'PROJECT': os.path.basename(self.project.fileName() or '') or self.project.title()}, context=ctx, feedback=self.feedback)
        except Exception as e:
            self._failed(e, published=False)
            return False
        self.result_url = res.get('URL', '')
        self.addr_info = {'project_id': project_map_id(self.project, create=False), 'published': ''}
        self._set_addr('mine', '↻ <b>Updates your map.</b> Same link, new content.')
        return True

    def _publish_now(self):
        if not hosting_ready():
            self._hosting()
            if not hosting_ready():
                return
        self._publish_toggled(True)
        self.publish_name = self._confirm_address()
        if not self.publish_name:
            return
        self._start('Publishing your web map…')
        if self._do_publish(self.title.text().strip() or 'My web map'):
            self._show_done(None, None)

    def _show_done(self, tiles, secs):
        published = bool(self.result_url)
        self.done_title.setText('Your web map is live' if published else 'Your web map is ready')
        bits = []
        if tiles:
            bits.append('%s tiles' % format(tiles, ','))
        if secs:
            bits.append('made in %s' % ('%d s' % secs if secs < 90 else '%.1f min' % (secs / 60)))
        stats = ', '.join(bits).capitalize() + '. ' if bits else ''
        self.done_text.setText(stats + 'Files saved in %s' % self.result_dir)
        self.link.setCursorPosition(0)
        self.link_row.setVisible(published)
        self.link.setText(self.result_url)
        self.link.setCursorPosition(0)
        self.publish_now.setVisible(not published)
        self.publish_now.setText('Publish online…' if hosting_ready() else 'Set up publishing…')
        notes = []
        if published and '.r2.dev' in self.result_url:
            notes.append("Want a link on your own domain for client work? That's in <a href='%s'>Web Map Kit (full)</a>." % UPGRADE_URL)
        if self.warnings:
            notes.append('<b>Worth checking:</b><br>' + '<br>'.join(('• ' + re.sub('<[^>]+>', '', w) for w in self.warnings[:6])))
        self.notes.setText('<br><br>'.join(notes))
        self.stack.setCurrentIndex(2)

    def done(self, r):
        for w in list(self.workers):
            w.wait(3000)
        super().done(r)

    def _failed(self, err, published=None):
        msg = str(err).strip() or err.__class__.__name__
        self.run_title.setText("Publishing didn't finish" if published is False else "The web map couldn't be created")
        self.step.setText(msg)
        self.log.setVisible(True)
        self.cancel_btn.setText('Back')
        self.cancel_btn.setEnabled(True)
        try:
            self.cancel_btn.clicked.disconnect()
        except TypeError:
            pass
        self.cancel_btn.clicked.connect(self._back_to_form)

    def _back_to_form(self):
        self.cancel_btn.setText('Cancel')
        try:
            self.cancel_btn.clicked.disconnect()
        except TypeError:
            pass
        self.cancel_btn.clicked.connect(self._cancel)
        self.stack.setCurrentIndex(0)

class ReplaceDialog(QDialog):
    CANCEL, REPLACE, NEW = (0, 1, 2)

    def __init__(self, parent, info, address, when):
        super().__init__(parent)
        self.setWindowTitle('This address is in use')
        self.setStyleSheet(CSS)
        self.setMinimumWidth(480)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)
        v.addWidget(_label('Another map already lives at this address', 'h1'))
        src = ' from %s' % info['project'] if info.get('project') else ''
        when = ', published %s' % when if when else ''
        v.addWidget(_label("<b>%s</b>%s%s<br><span style='color:#667085'>%s</span>" % (info['title'], src, when, address), None))
        v.addWidget(_label('Replacing it puts this project at the same link, so anyone who has that link will see the new map. Publishing as a new map keeps both online.', 'hint'))
        row = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(lambda: self.done(self.CANCEL))
        rep = QPushButton('Replace it')
        rep.setObjectName('danger')
        rep.clicked.connect(lambda: self.done(self.REPLACE))
        new = QPushButton('Publish as a new map')
        new.setObjectName('primary')
        new.setDefault(True)
        new.clicked.connect(lambda: self.done(self.NEW))
        row.addWidget(cancel)
        row.addStretch(1)
        row.addWidget(rep)
        row.addWidget(new)
        v.addLayout(row)
