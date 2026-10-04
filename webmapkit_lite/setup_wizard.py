# -*- coding: utf-8 -*-
# Web Map Kit Lite · GPL-2.0-or-later · Byteloom (Pvt) Ltd · https://mapship.link/webmap-kit/
import time
from qgis.PyQt.QtCore import Qt, QTimer, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget
from qgis.core import QgsSettings
from . import cloudflare as cf
from .common import Worker
from .edition import UPGRADE_URL
from .publish_alg import SETTINGS, R2Client, slug
from .settings_dialog import HostingDialog as ManualDialog, hosting_ready, load_hosting
CSS = '\nQLabel#h1 { font-size: 18px; font-weight: 600; }\nQLabel#sub, QLabel#hint, QLabel#detail { color: #667085; }\nQLabel#stepNum { background: #0f766e; color: white; border-radius: 12px; font-weight: 700; }\nQLabel#stepTitle { font-weight: 600; font-size: 13px; }\nQLabel#pill { color: #98a2b3; padding: 2px 4px; }\nQLabel#pill[state="now"] { color: #0f766e; font-weight: 700; }\nQLabel#pill[state="done"] { color: #067647; }\nQFrame#card { border: 1px solid palette(mid); border-radius: 10px; background: palette(base); }\nQFrame#action { border: 1px solid #fedf89; border-radius: 8px; background: #fffaeb; }\nQLabel#actionText { color: #93370d; }\nQLabel#icon { font-size: 15px; font-weight: 700; }\nQLabel#error { color: #b42318; }\nQLabel#okBig { background: #dcfae6; color: #067647; border-radius: 26px; font-size: 26px; font-weight: 700; }\nQLabel#addrPreview { color: #175cd3; }\nQPushButton#primary { font-weight: 600; padding: 6px 16px; }\nQToolButton#link { border: none; color: #175cd3; padding: 0; }\nQToolButton#link:hover { text-decoration: underline; }\nQRadioButton { font-weight: 600; }\n'
ICONS = {'wait': ('○', '#98a2b3'), 'run': ('…', '#175cd3'), 'ok': ('✓', '#067647'), 'warn': ('!', '#b54708'), 'fail': ('✕', '#b42318')}

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
    b.clicked.connect(slot if slot else lambda: None)
    return b

def _open(url):
    QDesktopServices.openUrl(QUrl(url))

def save_hosting(**values):
    st = QgsSettings()
    for k, v in values.items():
        st.setValue(SETTINGS + k, v or '')

class CheckRow(QWidget):

    def __init__(self, text):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        h.setSpacing(10)
        self.icon = _label('', 'icon', wrap=False)
        self.icon.setFixedWidth(18)
        self.icon.setAlignment(Qt.AlignCenter)
        v = QVBoxLayout()
        v.setSpacing(0)
        self.text = _label(text)
        self.detail = _label('', 'detail')
        self.detail.setVisible(False)
        v.addWidget(self.text)
        v.addWidget(self.detail)
        h.addWidget(self.icon, 0, Qt.AlignTop)
        h.addLayout(v, 1)
        self.set('wait')

    def set(self, state, detail=None):
        sym, color = ICONS[state]
        self.icon.setText(sym)
        self.icon.setStyleSheet('color:%s' % color)
        self.text.setStyleSheet('color:#98a2b3' if state == 'wait' else '')
        if detail is not None:
            self.detail.setText(detail)
            self.detail.setVisible(bool(detail))

class SetupWizard(QDialog):
    CONNECT, SETUP, ADDRESS, DONE = range(4)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Web Map Kit: hosting')
        self.setStyleSheet(CSS)
        self.setMinimumSize(620, 600)
        self.workers, self.changed = (set(), False)
        self.api, self.token_id, self.account, self.accounts, self.bucket = (None, None, None, [], None)
        self.r2dev = ''
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 16)
        outer.setSpacing(12)
        pills = QHBoxLayout()
        pills.setSpacing(2)
        self.pills = []
        for i, name in enumerate(('1  Connect', '2  Set up', '3  Address', '4  Ready')):
            p = _label(name, 'pill', wrap=False)
            self.pills.append(p)
            pills.addWidget(p)
            if i < 3:
                pills.addWidget(_label('›', 'pill', wrap=False))
        pills.addStretch(1)
        outer.addLayout(pills)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet('color: palette(mid)')
        outer.addWidget(line)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        for build in (self._connect_page, self._setup_page, self._address_page, self._done_page):
            self.stack.addWidget(build())
        h = load_hosting()
        if hosting_ready(h):
            self._show_done()
        else:
            self._go(self.CONNECT)

    def _go(self, page):
        self.stack.setCurrentIndex(page)
        for i, p in enumerate(self.pills):
            p.setProperty('state', 'now' if i == page else 'done' if i < page else '')
            p.style().unpolish(p)
            p.style().polish(p)

    def _run(self, fn, *args, done=None, failed=None, step=None):
        w = Worker(fn, *args, parent=self)
        self.workers.add(w)
        if step:
            w.step.connect(step)
        if done:
            w.done.connect(done)
        if failed:
            w.failed.connect(lambda m, w=w: failed(getattr(w, 'error', None) or m))
        w.finished.connect(lambda: self.workers.discard(w))
        w.start()
        return w

    def done(self, r):
        for w in list(self.workers):
            w.wait(5000)
        if hasattr(self, 'poll'):
            self.poll.stop()
        super().done(1 if (r or self.changed) and hosting_ready() else r)

    def _step(self, num, title, body, widget=None):
        f = QFrame()
        f.setObjectName('card')
        h = QHBoxLayout(f)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(12)
        n = _label(str(num), 'stepNum', wrap=False)
        n.setFixedSize(24, 24)
        n.setAlignment(Qt.AlignCenter)
        h.addWidget(n, 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(4)
        v.addWidget(_label(title, 'stepTitle'))
        if body:
            b = _label(body, 'hint')
            b.setTextFormat(Qt.RichText)
            v.addWidget(b)
        if widget is not None:
            v.addWidget(widget)
        h.addLayout(v, 1)
        return f

    def _connect_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        v.addWidget(_label('Connect your Cloudflare account', 'h1'))
        v.addWidget(_label('Your maps are hosted in your own free Cloudflare account: no monthly bill, no bandwidth fees, and only you control them. It takes about 2 minutes, once.', 'sub'))
        b0 = QPushButton('Sign in ↗')
        b0.clicked.connect(lambda: _open(cf.DASH + '/login'))
        b1 = QPushButton('Create a free account ↗')
        b1.clicked.connect(lambda: _open(cf.SIGNUP_URL))
        row1 = QHBoxLayout()
        row1.addWidget(b0)
        row1.addWidget(b1)
        row1.addStretch(1)
        c1 = QWidget()
        c1.setLayout(row1)
        row1.setContentsMargins(0, 2, 0, 0)
        v.addWidget(self._step(1, 'Sign in to Cloudflare', 'Sign in first (or create a free account), so the link in step 2 can fill everything in.', c1))
        b2 = QPushButton('Create my token ↗')
        b2.setObjectName('primary')
        b2.clicked.connect(lambda: _open(cf.token_url()))
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 2, 0, 0)
        row2.addWidget(b2)
        copy2 = _link('Copy link', None)
        copy2.setToolTip('For a different browser or profile: copy the link and paste it there')
        copy2.clicked.disconnect()

        def _copy_token_link():
            from qgis.PyQt.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(cf.token_url())
            copy2.setText('Copied ✓')
            QTimer.singleShot(2000, lambda: copy2.setText('Copy link'))
        copy2.clicked.connect(_copy_token_link)
        row2.addSpacing(8)
        row2.addWidget(copy2)
        row2.addStretch(1)
        c2 = QWidget()
        c2v = QVBoxLayout(c2)
        c2v.setContentsMargins(0, 0, 0, 0)
        c2v.setSpacing(6)
        c2v.addLayout(row2)
        manual = _label("If you only see the list of tokens: make sure you're signed in, then click the link again. Or click <b>Create Token</b> → <b>Create Custom Token</b> → <b>Get started</b>, name it <i>Web Map Kit</i>, and add this permission:<br>&nbsp;&nbsp;• <b>Account</b> · <b>Workers R2 Storage</b> · <b>Edit</b><br>Then <b>Continue to summary</b> → <b>Create Token</b> → <b>Copy</b>.", 'hint')
        manual.setTextFormat(Qt.RichText)
        manual.setVisible(False)

        def _toggle_manual():
            manual.setVisible(not manual.isVisible())
            QTimer.singleShot(0, lambda: self.resize(self.width(), max(self.height(), self.sizeHint().height())))
        toggle = _link("Page didn't fill in?", _toggle_manual)
        c2v.addWidget(toggle, 0, Qt.AlignLeft)
        c2v.addWidget(manual)
        v.addWidget(self._step(2, 'Create a Web Map Kit token', 'Cloudflare opens in your browser with everything filled in (use <b>Copy link</b> for a different browser). Scroll to the bottom and click <b>Continue to summary</b>, then <b>Create Token</b>, then <b>Copy</b>.', c2))
        c3 = QWidget()
        r3 = QHBoxLayout(c3)
        r3.setContentsMargins(0, 2, 0, 0)
        self.token = QLineEdit()
        self.token.setPlaceholderText('Paste the token here')
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setMinimumHeight(30)
        self.token.textChanged.connect(self._token_changed)
        self.token.returnPressed.connect(self._connect)
        show = QCheckBox('Show')
        show.toggled.connect(lambda on: self.token.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.connect_btn = QPushButton('Connect')
        self.connect_btn.setObjectName('primary')
        self.connect_btn.setEnabled(False)
        self.connect_btn.clicked.connect(self._connect)
        r3.addWidget(self.token, 1)
        r3.addWidget(show)
        r3.addWidget(self.connect_btn)
        v.addWidget(self._step(3, 'Paste it here', '', c3))
        self.connect_err = _label('', 'error')
        self.connect_err.setVisible(False)
        v.addWidget(self.connect_err)
        v.addStretch(1)
        foot = QHBoxLayout()
        foot.addWidget(_link('I already have R2 keys (enter them manually)', self._manual))
        foot.addStretch(1)
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        v.addLayout(foot)
        return w

    def _token_changed(self, text):
        t = text.strip()
        self.connect_btn.setEnabled(len(t) >= 30)
        self.connect_err.setVisible(False)
        if len(t) >= 40 and len(t) - len(getattr(self, '_prev_token', '')) >= 30:
            QTimer.singleShot(250, self._connect)
        self._prev_token = t

    def _manual(self):
        if ManualDialog(self).exec_():
            self.changed = True
            self._show_done()

    def _setup_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        v.addWidget(_label('Setting up your map hosting', 'h1'))
        v.addWidget(_label('Web Map Kit is preparing everything in your Cloudflare account.', 'sub'))
        card = QFrame()
        card.setObjectName('card')
        cv = QVBoxLayout(card)
        cv.setContentsMargins(16, 14, 16, 14)
        cv.setSpacing(8)
        self.rows = {}
        for key, text in (('token', 'Checking your token'), ('account', 'Finding your Cloudflare account'), ('r2', 'Checking R2 storage'), ('bucket', 'Preparing storage for your maps'), ('public', 'Switching on the public address'), ('test', 'Test upload')):
            self.rows[key] = CheckRow(text)
            cv.addWidget(self.rows[key])
            if key == 'account':
                self.account_box = QWidget()
                ah = QHBoxLayout(self.account_box)
                ah.setContentsMargins(28, 0, 0, 4)
                self.account_combo = QComboBox()
                use = QPushButton('Use this account')
                use.setObjectName('primary')
                use.clicked.connect(self._account_chosen)
                ah.addWidget(self.account_combo, 1)
                ah.addWidget(use)
                self.account_box.setVisible(False)
                cv.addWidget(self.account_box)
            if key == 'r2':
                self.r2_box = QFrame()
                self.r2_box.setObjectName('action')
                rv = QVBoxLayout(self.r2_box)
                rv.setContentsMargins(12, 10, 12, 10)
                rv.addWidget(_label('R2 needs to be switched on once in your Cloudflare account. Cloudflare asks for a card to do this, but the free tier (10 GB of maps, unlimited visitors) costs nothing.', 'actionText'))
                rh = QHBoxLayout()
                on = QPushButton('Switch on R2 ↗')
                on.setObjectName('primary')
                on.clicked.connect(lambda: _open(cf.r2_url(self.account and self.account['id'])))
                again = QPushButton("I've done it, check again")
                again.clicked.connect(self._start_setup)
                rh.addWidget(on)
                rh.addWidget(again)
                rh.addStretch(1)
                rv.addLayout(rh)
                self.r2_box.setVisible(False)
                cv.addWidget(self.r2_box)
        v.addWidget(card)
        self.setup_err = _label('', 'error')
        self.setup_err.setTextFormat(Qt.RichText)
        self.setup_err.setVisible(False)
        v.addWidget(self.setup_err)
        v.addStretch(1)
        foot = QHBoxLayout()
        back = QPushButton('Back')
        back.clicked.connect(lambda: self._go(self.CONNECT))
        self.retry_btn = QPushButton('Try again')
        self.retry_btn.clicked.connect(self._connect)
        self.retry_btn.setVisible(False)
        self.setup_next = QPushButton('Continue')
        self.setup_next.setObjectName('primary')
        self.setup_next.setEnabled(False)
        self.setup_next.clicked.connect(self._show_address)
        foot.addWidget(back)
        foot.addStretch(1)
        foot.addWidget(self.retry_btn)
        foot.addWidget(self.setup_next)
        v.addLayout(foot)
        return w

    def _fail(self, row, err):
        import html
        self.rows[row].set('fail')
        msg = cf.friendly(err)
        tech = cf.detail(err) if not isinstance(err, str) else err
        extra = "<br><span style='color:#667085; font-size:11px'>Details: %s</span>" % html.escape(tech) if tech and tech != msg else ''
        self.setup_err.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setup_err.setText(html.escape(msg) + extra)
        self.setup_err.setVisible(True)
        self.retry_btn.setVisible(True)

    def _connect(self):
        token = self.token.text().strip()
        if len(token) < 30 or (self.workers and self.stack.currentIndex() == self.SETUP):
            return
        self.api = cf.Cloudflare(token)
        for r in self.rows.values():
            r.set('wait', '')
        self.account_box.setVisible(False)
        self.r2_box.setVisible(False)
        self.setup_err.setVisible(False)
        self.retry_btn.setVisible(False)
        self.setup_next.setEnabled(False)
        self._go(self.SETUP)
        self.rows['token'].set('run')
        api = self.api

        def phase1():
            return (api.verify(), api.accounts())
        self._run(phase1, done=self._phase1_done, failed=lambda e: self._fail('token', e))

    def _phase1_done(self, res):
        self.token_id, self.accounts = res
        self.rows['token'].set('ok', 'Token accepted')
        if not self.accounts:
            return self._fail('account', "This token can't see any Cloudflare account. Create it again with the “Create my token” button (it sets the right permissions).")
        if len(self.accounts) == 1:
            self.account = self.accounts[0]
            return self._start_setup()
        saved = load_hosting()['account']
        self.account_combo.clear()
        for a in self.accounts:
            self.account_combo.addItem(a['name'], a)
            if a['id'] == saved:
                self.account_combo.setCurrentIndex(self.account_combo.count() - 1)
        self.rows['account'].set('run', 'You have %d accounts. Which one should hold your maps?' % len(self.accounts))
        self.account_box.setVisible(True)

    def _account_chosen(self):
        self.account = self.account_combo.currentData()
        self.account_box.setVisible(False)
        self._start_setup()

    def _start_setup(self):
        self.r2_box.setVisible(False)
        self.setup_err.setVisible(False)
        self.retry_btn.setVisible(False)
        self.rows['account'].set('ok', self.account['name'])
        for k in ('r2', 'bucket', 'public', 'test'):
            self.rows[k].set('wait', '')
        self.rows['r2'].set('run')
        api, acc, token, token_id = (self.api, self.account['id'], self.token.text().strip(), self.token_id)
        preferred = load_hosting()['bucket'] if load_hosting()['account'] == acc else ''

        def phase2(emit):
            try:
                names = api.buckets(acc)
            except cf.CFError as e:
                if e.code in cf.R2_NOT_ENABLED or 'enable r2' in str(e).lower():
                    return {'r2_off': True}
                raise
            emit(('r2', 'ok', ''))
            emit(('bucket', 'run', ''))
            bucket = preferred if preferred in names else 'maps' if 'maps' in names else None
            created = False
            if not bucket:
                bucket = 'maps'
                api.create_bucket(acc, bucket)
                created = True
            emit(('bucket', 'ok', '%s bucket “%s”' % ('Created' if created else 'Using your', bucket)))
            emit(('public', 'run', ''))
            r2dev = api.public_address(acc, bucket)
            emit(('public', 'ok', r2dev.split('://', 1)[-1]))
            emit(('test', 'run', ''))
            ak, sk = cf.s3_keys(token_id, token)
            import os
            jur = api.bucket_jurisdiction(acc, bucket)
            s3 = R2Client(acc, ak, sk, endpoint=os.environ.get('WEBMAPKIT_R2_ENDPOINT'), jurisdiction=jur)
            key = '_webmapkit-check.txt'
            for attempt in range(4):
                try:
                    s3.put_bytes(bucket, key, b'ok', 'text/plain')
                    s3.delete(bucket, key)
                    break
                except Exception as e:
                    if attempt == 3:
                        raise RuntimeError('Upload test to %s failed. %s' % (s3.endpoint.split('://')[-1], cf.detail(e)))
                    emit(('test', 'run', 'Retrying… (%d)' % (attempt + 1)))
                    time.sleep(3 + attempt * 3)
            emit(('test', 'ok', 'Uploads work'))
            return {'bucket': bucket, 'r2dev': r2dev, 'ak': ak, 'sk': sk, 'jur': jur}
        w = Worker(None, parent=self)
        w.fn = lambda: phase2(lambda t: w.step.emit(t))
        w.args = ()
        self.workers.add(w)
        w.step.connect(lambda t: self.rows[t[0]].set(t[1], t[2]))
        w.done.connect(self._phase2_done)
        w.failed.connect(lambda m, w=w: self._phase2_failed(getattr(w, 'error', None) or m))
        w.finished.connect(lambda: self.workers.discard(w))
        w.start()

    def _phase2_failed(self, msg):
        row = next((k for k in ('test', 'public', 'bucket', 'r2') if self.rows[k].icon.text() == '…'), 'r2')
        self._fail(row, msg)

    def _phase2_done(self, res):
        if res.get('r2_off'):
            self.rows['r2'].set('warn', 'Not switched on yet')
            self.r2_box.setVisible(True)
            return
        self.bucket, self.r2dev = (res['bucket'], res['r2dev'])
        public = self.r2dev
        save_hosting(account=self.account['id'], account_name=self.account['name'], bucket=self.bucket, access_key=res['ak'], secret=res['sk'], public_url=public, api_token=self.token.text().strip(), jurisdiction=res.get('jur', ''))
        self.changed = True
        self.setup_next.setEnabled(True)
        self.setup_next.setFocus()

    def _address_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        v.addWidget(_label('Choose your map address', 'h1'))
        v.addWidget(_label('Every map you publish gets a link at this address. You can change it later.', 'sub'))
        self.addr_group = QButtonGroup(self)
        self.addr_card = QFrame()
        self.addr_card.setObjectName('card')
        self.addr_layout = QVBoxLayout(self.addr_card)
        self.addr_layout.setContentsMargins(16, 14, 16, 14)
        self.addr_layout.setSpacing(6)
        v.addWidget(self.addr_card)
        self.addr_msg = _label('', 'hint')
        self.addr_msg.setTextFormat(Qt.RichText)
        v.addWidget(self.addr_msg)
        v.addStretch(1)
        foot = QHBoxLayout()
        back = QPushButton('Back')
        back.clicked.connect(lambda: self._go(self.SETUP) if self.api else self._show_done())
        self.addr_btn = QPushButton('Use this address')
        self.addr_btn.setObjectName('primary')
        self.addr_btn.clicked.connect(self._use_address)
        foot.addWidget(back)
        foot.addStretch(1)
        foot.addWidget(self.addr_btn)
        v.addLayout(foot)
        return w

    def _show_address(self):
        while self.addr_layout.count():
            it = self.addr_layout.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        for b in self.addr_group.buttons():
            self.addr_group.removeButton(b)
        r2 = self.r2dev.split('://', 1)[-1]
        rb = QRadioButton('Free Cloudflare address')
        rb.setChecked(True)
        self.addr_group.addButton(rb)
        self.addr_layout.addWidget(rb)
        hint = _label("<span style='color:#175cd3'>%s/your-map/</span><br>Works straight away." % r2, 'hint')
        hint.setTextFormat(Qt.RichText)
        hint.setContentsMargins(24, 0, 0, 8)
        self.addr_layout.addWidget(hint)
        note = _label("Want your own domain, like <b>maps.yourcompany.com</b>? That's in <a href='%s'>Web Map Kit (full)</a>." % UPGRADE_URL, 'hint')
        note.setTextFormat(Qt.RichText)
        note.setOpenExternalLinks(True)
        self.addr_layout.addWidget(note)
        self.addr_msg.setText('')
        self.addr_btn.setEnabled(bool(self.r2dev))
        self._go(self.ADDRESS)

    def _use_address(self):
        save_hosting(public_url='https://' + self.r2dev.split('://', 1)[-1])
        self.changed = True
        self._show_done()

    def _done_page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(14)
        ok = _label('✓', 'okBig', wrap=False)
        ok.setFixedSize(52, 52)
        ok.setAlignment(Qt.AlignCenter)
        head.addWidget(ok, 0, Qt.AlignTop)
        hv = QVBoxLayout()
        hv.addWidget(_label("You're ready to publish", 'h1'))
        hv.addWidget(_label('Maps you publish go straight to your Cloudflare account.', 'sub'))
        head.addLayout(hv, 1)
        v.addLayout(head)
        card = QFrame()
        card.setObjectName('card')
        self.summary = QVBoxLayout(card)
        self.summary.setContentsMargins(16, 14, 16, 14)
        self.summary.setSpacing(8)
        v.addWidget(card)
        v.addWidget(_label("Free tier: 10 GB of maps and unlimited visitors. Cloudflare doesn't charge for bandwidth.", 'hint'))
        v.addStretch(1)
        foot = QHBoxLayout()
        self.change_addr = QPushButton('Change address…')
        self.change_addr.clicked.connect(self._change_address)
        reconnect = QPushButton('Reconnect…')
        reconnect.setToolTip('Connect again with a new token')
        reconnect.clicked.connect(lambda: (self.token.clear(), self._go(self.CONNECT)))
        foot.addWidget(self.change_addr)
        foot.addWidget(reconnect)
        foot.addWidget(_link('Keys…', self._manual))
        foot.addStretch(1)
        close = QPushButton('Done')
        close.setObjectName('primary')
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        v.addLayout(foot)
        return w

    def _show_done(self):
        while self.summary.count():
            it = self.summary.takeAt(0)
            if it.layout():
                while it.layout().count():
                    x = it.layout().takeAt(0)
                    if x.widget():
                        x.widget().setParent(None)
            elif it.widget():
                it.widget().setParent(None)
        h = load_hosting()
        for label, value in (('Account', h.get('account_name') or h['account']), ('Storage', 'bucket “%s”' % h['bucket']), ('Map address', (h['public_url'] or 'not set').split('://', 1)[-1] + '/')):
            row = QHBoxLayout()
            l = _label(label, 'hint', wrap=False)
            l.setFixedWidth(110)
            row.addWidget(l)
            row.addWidget(_label(value), 1)
            self.summary.addLayout(row)
        self.change_addr.setEnabled(bool(h.get('api_token')))
        self.change_addr.setToolTip('' if h.get('api_token') else 'Reconnect with a token to change the address here')
        self._go(self.DONE)

    def _change_address(self):
        h = load_hosting()
        self.token.setText(h['api_token'])
        self.api = cf.Cloudflare(h['api_token'])
        self.account = {'id': h['account'], 'name': h.get('account_name') or h['account']}
        self.bucket = h['bucket']
        self.addr_msg.setText('Loading your address…')
        api, acc, bucket = (self.api, self.account['id'], self.bucket)

        def got(r2dev):
            self.r2dev = r2dev
            self._show_address()
        self._run(api.public_address, acc, bucket, done=got, failed=lambda m: self.addr_msg.setText("<span style='color:#b42318'>%s</span>" % cf.friendly(m)))
        self._go(self.ADDRESS)
