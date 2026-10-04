# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
from qgis.core import QgsSettings
from .publish_alg import SETTINGS, R2Client
KEYS = ('account', 'bucket', 'access_key', 'secret', 'public_url', 'api_token', 'account_name', 'jurisdiction')

def load_hosting():
    st = QgsSettings()
    return {k: (st.value(SETTINGS + k, '') or '').strip() for k in KEYS}

def hosting_ready(h=None):
    h = h or load_hosting()
    return all((h[k] for k in ('account', 'bucket', 'access_key', 'secret')))
STEPS = '\n<p style="margin:0 0 6px"><b>One-time set-up, about 3 minutes.</b> Your maps are hosted in your own free Cloudflare account:\nno monthly bill, no bandwidth fees, and only you control them.</p>\n<ol style="margin-top:0; padding-left:18px">\n<li>Create a free account at <a href="https://dash.cloudflare.com/sign-up">dash.cloudflare.com</a>, open <b>R2 Object Storage</b> and create a bucket (for example <i>maps</i>).</li>\n<li>In the bucket: <b>Settings → Public access</b>. Turn on the <b>r2.dev URL</b> and copy the address.</li>\n<li>Back in R2: <b>Manage API tokens → Create API token</b>, permission <b>Object Read &amp; Write</b> for that bucket. Copy the keys shown.</li>\n<li>Paste everything below and click <b>Test connection</b>.</li>\n</ol>\n'

class HostingDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Web Map Kit: hosting')
        self.setMinimumWidth(600)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(12)
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        steps.setTextFormat(Qt.RichText)
        steps.setOpenExternalLinks(True)
        v.addWidget(steps)
        h = load_hosting()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.account = QLineEdit(h['account'])
        self.account.setPlaceholderText('32 characters, shown on the R2 overview page')
        self.bucket = QLineEdit(h['bucket'])
        self.bucket.setPlaceholderText('e.g. maps')
        self.public = QLineEdit(h['public_url'])
        self.public.setPlaceholderText('https://pub-….r2.dev  or  https://maps.yourdomain.com')
        self.ak = QLineEdit(h['access_key'])
        self.ak.setPlaceholderText('Access Key ID')
        self.sk = QLineEdit(h['secret'])
        self.sk.setPlaceholderText('Secret Access Key')
        self.sk.setEchoMode(QLineEdit.Password)
        show = QCheckBox('Show')
        show.toggled.connect(lambda on: self.sk.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        srow = QHBoxLayout()
        srow.addWidget(self.sk, 1)
        srow.addWidget(show)
        form.addRow('Account ID', self.account)
        form.addRow('Bucket', self.bucket)
        form.addRow('Public address', self.public)
        form.addRow('Access Key ID', self.ak)
        form.addRow('Secret Access Key', srow)
        v.addLayout(form)
        trow = QHBoxLayout()
        self.test_btn = QPushButton('Test connection')
        self.test_btn.clicked.connect(self.test)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        trow.addWidget(self.test_btn)
        trow.addWidget(self.status, 1)
        v.addLayout(trow)
        note = QLabel('<span style="color:#667085">Saved in your QGIS profile on this computer. </span>')
        note.setWordWrap(True)
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        help_btn = bb.addButton('Open Cloudflare', QDialogButtonBox.HelpRole)
        help_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://dash.cloudflare.com/?to=/:account/r2/overview')))
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.layout().activate()
        self.setMinimumHeight(self.layout().heightForWidth(600) if self.layout().hasHeightForWidth() else self.sizeHint().height())

    def values(self):
        pub = self.public.text().strip().rstrip('/')
        if pub and (not pub.startswith('http')):
            pub = 'https://' + pub
        return {'account': self.account.text().strip(), 'bucket': self.bucket.text().strip(), 'access_key': self.ak.text().strip(), 'secret': self.sk.text().strip(), 'public_url': pub}

    def _say(self, text, ok=None):
        color = '#067647' if ok else '#b42318' if ok is False else '#475467'
        self.status.setText('<span style="color:%s">%s</span>' % (color, text))

    def test(self):
        h = self.values()
        missing = [n for n, k in [('Account ID', 'account'), ('Bucket', 'bucket'), ('Access Key ID', 'access_key'), ('Secret', 'secret')] if not h[k]]
        if missing:
            self._say('Missing: ' + ', '.join(missing), False)
            return
        self._say('Checking…')
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            import os
            status = R2Client(h['account'], h['access_key'], h['secret'], endpoint=os.environ.get('WEBMAPKIT_R2_ENDPOINT')).bucket_exists(h['bucket'])
        except Exception as e:
            status = str(e)
        finally:
            QApplication.restoreOverrideCursor()
        if status == 200:
            msg = 'Connected to bucket “%s”.' % h['bucket']
            if not h['public_url']:
                self._say(msg + ' Add the public address so maps get a link.', None)
                return
            self._say(msg + ' Ready to publish.', True)
        elif status == 404:
            self._say("Bucket “%s” not found. Check the name (it's case-sensitive)." % h['bucket'], False)
        elif status in (401, 403):
            self._say('Cloudflare refused the keys. Check the Account ID and both keys, and that the token can write to this bucket.', False)
        else:
            self._say("Couldn't reach Cloudflare (%s). Check your internet connection." % status, False)

    def save(self):
        st = QgsSettings()
        st.setValue(SETTINGS + 'api_token', '')
        for k, v in self.values().items():
            st.setValue(SETTINGS + k, v)
        self.accept()
