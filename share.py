from flask import Flask, request, send_from_directory, send_file, render_template_string, jsonify
from flask_socketio import SocketIO, emit
import logging
import os
import socket
import ssl
import sys
import tempfile
import threading
import webbrowser
import zipfile

PORT = 8080
SHARE_DIR = "shared"
CERT_DIR = ".certs"

# HTTPS by default: browsers warn about / block downloads served over plain http://.
# Run `python3 share.py --http` to fall back to plain HTTP.
USE_HTTPS = "--http" not in sys.argv
# `--open` opens LocalShare in this computer's browser once the server is up.
OPEN_BROWSER = "--open" in sys.argv

os.makedirs(SHARE_DIR, exist_ok=True)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

SHARED_TEXT = ""


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def base_url():
    return f"{'https' if USE_HTTPS else 'http'}://{lan_ip()}:{PORT}"


def ensure_cert():
    """Create a self-signed certificate (once) for HTTPS on the local network."""
    cert = os.path.join(CERT_DIR, "cert.pem")
    key = os.path.join(CERT_DIR, "key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key

    # Generated in Python (not the openssl CLI) so it also works on Windows.
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    pkey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LocalShare")])
    alt_names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    if lan_ip() != "127.0.0.1":
        alt_names.append(x509.IPAddress(ipaddress.ip_address(lan_ip())))
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(pkey.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(pkey, hashes.SHA256())
    )

    os.makedirs(CERT_DIR, exist_ok=True)
    with open(key, "wb") as f:
        f.write(pkey.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    with open(cert, "wb") as f:
        f.write(certificate.public_bytes(serialization.Encoding.PEM))
    return cert, key


class LazyHandshakeContext(ssl.SSLContext):
    """Werkzeug does the TLS handshake inside accept(), on the main thread, so a
    single idle connection (browsers open spare ones in advance) freezes the whole
    server. Deferring the handshake moves it to the per-request worker thread."""

    def wrap_socket(self, sock, *args, **kwargs):
        kwargs["do_handshake_on_connect"] = False
        return super().wrap_socket(sock, *args, **kwargs)


class _HideCertRejections(logging.Filter):
    """Browsers refuse the self-signed cert on their spare preconnect sockets and
    log nothing useful; hide those harmless 'SSL error occurred' lines."""

    NOISE = ("TLS_ALERT_CERTIFICATE_UNKNOWN", "TLSV1_ALERT_UNKNOWN_CA",
             "SSLV3_ALERT_CERTIFICATE_UNKNOWN", "SSLV3_ALERT_BAD_CERTIFICATE")

    def filter(self, record):
        msg = record.getMessage()
        return not ("SSL error occurred" in msg and any(n in msg for n in self.NOISE))


def make_ssl_context():
    logging.getLogger("werkzeug").addFilter(_HideCertRejections())
    cert, key = ensure_cert()
    ctx = LazyHandshakeContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx


HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b1020">
<title>LocalShare</title>

<style>
:root {
  --bg: #f5f6fa;
  --surface: #ffffff;
  --surface-2: #f0f2f7;
  --border: #e3e6ee;
  --text: #141824;
  --muted: #6b7285;
  --accent: #4f46e5;
  --accent-soft: #eef0ff;
  --accent-text: #ffffff;
  --ok: #16a34a;
  --bad: #dc2626;
  --shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 4px 16px rgba(16, 24, 40, .06);
  --radius: 16px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0b1020;
    --surface: #121829;
    --surface-2: #192036;
    --border: #232b45;
    --text: #e8ebf4;
    --muted: #8b93ab;
    --accent: #7c75ff;
    --accent-soft: #1f2347;
    --accent-text: #ffffff;
    --ok: #22c55e;
    --bad: #f87171;
    --shadow: 0 1px 2px rgba(0, 0, 0, .3), 0 8px 24px rgba(0, 0, 0, .25);
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  -webkit-font-smoothing: antialiased;
  -webkit-tap-highlight-color: transparent;
}

.wrap {
  padding: 20px clamp(16px, 3vw, 40px) calc(32px + env(safe-area-inset-bottom));
}

/* Single column on phones; two columns on wide screens */
.grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0 20px; }
@media (min-width: 960px) {
  .grid { grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); align-items: start; }
  .grid .files-card { position: sticky; top: 20px; }
  .grid .files { max-height: calc(100vh - 140px); overflow: auto; }
  .grid textarea { min-height: 260px; }
}
.head-actions { display: flex; gap: 8px; flex-shrink: 0; }
.card-head h2 { white-space: nowrap; }
.btn[hidden] { display: none; }

/* Header */
header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 20px;
}
.brand { display: flex; align-items: center; gap: 10px; }
.logo {
  width: 36px; height: 36px; border-radius: 10px;
  background: linear-gradient(135deg, var(--accent), #06b6d4);
  display: grid; place-items: center; color: #fff;
}
.brand h1 { font-size: 19px; margin: 0; letter-spacing: -.01em; }
.brand p { margin: 1px 0 0; font-size: 12.5px; color: var(--muted); }

.pill {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12.5px; font-weight: 500;
  padding: 6px 10px; border-radius: 999px;
  background: var(--surface); border: 1px solid var(--border);
  color: var(--muted); white-space: nowrap;
}
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--bad); }
.pill.live .dot { background: var(--ok); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 25%, transparent); }

.address {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  background: var(--accent-soft);
  border-radius: 12px;
  padding: 10px 12px;
  margin-bottom: 16px;
  font-size: 13.5px;
}
.address code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13.5px; color: var(--accent); font-weight: 600;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.address span { color: var(--muted); }

/* Cards */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 18px;
  margin-bottom: 16px;
}
.card-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 8px; margin-bottom: 14px;
}
.card-head h2 {
  font-size: 15px; margin: 0; font-weight: 600;
  display: flex; align-items: center; gap: 8px;
}
.card-head h2 svg { color: var(--accent); }
.count {
  font-size: 12px; font-weight: 600; color: var(--muted);
  background: var(--surface-2); padding: 2px 8px; border-radius: 999px;
}
.hint { font-size: 12.5px; color: var(--muted); }

/* Buttons */
.btn {
  appearance: none; border: 1px solid var(--border);
  background: var(--surface); color: var(--text);
  font: inherit; font-size: 13px; font-weight: 500;
  padding: 7px 12px; border-radius: 10px;
  display: inline-flex; align-items: center; gap: 6px;
  cursor: pointer; transition: background .15s, border-color .15s;
  text-decoration: none; white-space: nowrap;
}
.btn:hover { background: var(--surface-2); }
.btn:active { transform: scale(.97); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); }
.btn.primary:hover { filter: brightness(1.08); }
.btn.danger { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 35%, var(--border)); }
.btn.danger:hover { background: color-mix(in srgb, var(--bad) 10%, var(--surface)); }
.btn.danger-ghost { color: var(--muted); }
.btn.danger-ghost:hover { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 35%, var(--border)); }
.file .btn + .btn { margin-left: -4px; }
.icon-btn {
  width: 38px; height: 38px; padding: 0; justify-content: center; flex-shrink: 0;
  border-radius: 10px; text-decoration: none; color: var(--text);
}

/* Drop zone */
.drop {
  border: 2px dashed var(--border);
  background: var(--surface-2);
  border-radius: 14px;
  padding: 28px 16px;
  text-align: center;
  cursor: pointer;
  transition: border-color .15s, background .15s;
}
.drop:hover, .drop.over { border-color: var(--accent); background: var(--accent-soft); }
.drop-icon {
  width: 48px; height: 48px; margin: 0 auto 10px;
  border-radius: 14px; background: var(--surface);
  display: grid; place-items: center; color: var(--accent);
  box-shadow: var(--shadow);
}
.drop strong { display: block; font-size: 15px; margin-bottom: 4px; }
.drop span { font-size: 13px; color: var(--muted); }

/* Upload queue */
.queue { margin-top: 14px; display: none; }
.queue.show { display: block; }
.queue-top {
  display: flex; justify-content: space-between; font-size: 13px;
  margin-bottom: 8px; color: var(--muted);
}
.queue-top b { color: var(--text); font-weight: 600; }
.bar { height: 8px; background: var(--surface-2); border-radius: 99px; overflow: hidden; }
.bar > div {
  height: 100%; width: 0%;
  background: linear-gradient(90deg, var(--accent), #06b6d4);
  border-radius: 99px; transition: width .15s;
}
.qitems { margin-top: 10px; max-height: 180px; overflow: auto; }
.qitem {
  display: flex; align-items: center; gap: 10px;
  font-size: 13px; padding: 6px 0;
}
.qitem .n { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.qitem .s { color: var(--muted); font-variant-numeric: tabular-nums; }
.qitem .st { width: 18px; text-align: center; color: var(--muted); }
.qitem.done .st { color: var(--ok); }

/* Clipboard */
textarea {
  width: 100%; min-height: 140px; resize: vertical;
  background: var(--surface-2); color: var(--text);
  border: 1px solid var(--border); border-radius: 12px;
  padding: 12px 14px; font: inherit; font-size: 15px; line-height: 1.5;
  outline: none; transition: border-color .15s, box-shadow .15s;
}
textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent);
}
.row { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 10px; }
.row .actions { display: flex; gap: 8px; }

/* Files */
.files { display: flex; flex-direction: column; }
.file {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 0;
  border-top: 1px solid var(--border);
}
.file:first-child { border-top: 0; padding-top: 0; }
.ext {
  width: 40px; height: 40px; border-radius: 10px; flex-shrink: 0;
  display: grid; place-items: center;
  font-size: 10.5px; font-weight: 700; letter-spacing: .02em; text-transform: uppercase;
  background: var(--accent-soft); color: var(--accent);
}
.ext.img { background: color-mix(in srgb, #ec4899 14%, transparent); color: #db2777; }
.ext.vid { background: color-mix(in srgb, #f59e0b 16%, transparent); color: #d97706; }
.ext.aud { background: color-mix(in srgb, #10b981 16%, transparent); color: #059669; }
.ext.doc { background: color-mix(in srgb, #3b82f6 14%, transparent); color: #2563eb; }
.ext.zip { background: color-mix(in srgb, #8b5cf6 14%, transparent); color: #7c3aed; }
.meta { flex: 1; min-width: 0; }
.meta .name {
  font-size: 14.5px; font-weight: 500;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.meta .sub { font-size: 12.5px; color: var(--muted); margin-top: 2px; }
.empty { text-align: center; padding: 24px 8px; color: var(--muted); font-size: 14px; }
.empty svg { opacity: .5; margin-bottom: 6px; }

/* Modal */
.modal-backdrop {
  position: fixed; inset: 0; z-index: 50;
  display: grid; place-items: center; padding: 16px;
  background: rgba(5, 8, 18, .55);
  backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px);
  opacity: 0; visibility: hidden; transition: opacity .18s, visibility .18s;
}
.modal-backdrop.open { opacity: 1; visibility: visible; }
.modal {
  width: 100%; max-width: 400px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 18px; box-shadow: 0 20px 60px rgba(0, 0, 0, .35);
  padding: 22px;
  transform: translateY(8px) scale(.98); transition: transform .18s;
}
.modal-backdrop.open .modal { transform: none; }
.modal-icon {
  width: 44px; height: 44px; border-radius: 12px; margin-bottom: 14px;
  display: grid; place-items: center;
  background: color-mix(in srgb, var(--bad) 14%, transparent); color: var(--bad);
}
.modal h3 { margin: 0 0 6px; font-size: 17px; }
.modal p {
  margin: 0; font-size: 14px; line-height: 1.5; color: var(--muted);
  overflow-wrap: anywhere;
}
.modal p b { color: var(--text); font-weight: 600; }
.modal-actions { display: flex; gap: 10px; margin-top: 20px; }
.modal-actions .btn { flex: 1; justify-content: center; padding: 10px 14px; font-size: 14px; }
.btn.solid-danger { background: var(--bad); border-color: var(--bad); color: #fff; }
.btn.solid-danger:hover { filter: brightness(1.08); background: var(--bad); }

/* Toast */
.toast {
  position: fixed; left: 50%; bottom: calc(20px + env(safe-area-inset-bottom));
  transform: translate(-50%, 20px); opacity: 0;
  background: var(--text); color: var(--bg);
  padding: 10px 16px; border-radius: 12px; font-size: 14px; font-weight: 500;
  transition: opacity .2s, transform .2s; pointer-events: none;
  max-width: calc(100% - 32px);
}
.toast.show { opacity: 1; transform: translate(-50%, 0); }

@media (max-width: 420px) {
  .card { padding: 16px; }
  .brand p { display: none; }
  .refresh-btn { display: none; } /* list updates live; save the room */
  #deleteAll .lbl { display: none; } /* icon only on narrow phones */
  #deleteAll { padding: 7px 10px; }
  .files-card .count { display: none; }
}
</style>
</head>

<body>
<div class="wrap">

  <header>
    <div class="brand">
      <div class="logo">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17 17 7M9 7h8v8"/></svg>
      </div>
      <div>
        <h1>LocalShare</h1>
        <p>Files &amp; text across your devices</p>
      </div>
    </div>
    <div class="pill" id="status"><span class="dot"></span><span id="statusText">Connecting…</span></div>
  </header>

  <div class="address">
    <div style="min-width:0;display:flex;flex-direction:column;gap:2px">
      <span>Open on another device</span>
      <code id="addr">{{ address }}</code>
    </div>
    <button class="btn" onclick="copyText(document.getElementById('addr').textContent, 'Address copied')">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
      Copy
    </button>
  </div>

  <div class="grid">
  <div class="col">

  <!-- Upload -->
  <section class="card">
    <div class="card-head">
      <h2>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M6 10l6-6 6 6"/><path d="M4 20h16"/></svg>
        Send files
      </h2>
    </div>

    <div class="drop" id="dropZone">
      <div class="drop-icon">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
      </div>
      <strong>Tap to choose files</strong>
      <span>or drag &amp; drop — select as many as you like</span>
      <input id="fileInput" type="file" multiple hidden>
    </div>

    <div class="queue" id="queue">
      <div class="queue-top">
        <span id="queueLabel">Uploading…</span>
        <b id="queuePct">0%</b>
      </div>
      <div class="bar"><div id="bar"></div></div>
      <div class="qitems" id="qitems"></div>
    </div>
  </section>

  <!-- Clipboard -->
  <section class="card">
    <div class="card-head">
      <h2>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/></svg>
        Shared clipboard
      </h2>
      <span class="hint">Syncs live</span>
    </div>
    <textarea id="sharedText" placeholder="Type or paste here — it appears instantly on every connected device."></textarea>
    <div class="row">
      <span class="hint" id="charCount">0 characters</span>
      <div class="actions">
        <button class="btn" onclick="clearText()">Clear</button>
        <button class="btn primary" onclick="copyText(sharedText.value, 'Copied to clipboard')">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
          Copy
        </button>
      </div>
    </div>
  </section>

  </div>
  <div class="col">

  <!-- Files -->
  <section class="card files-card">
    <div class="card-head">
      <h2>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
        Shared files
        <span class="count" id="fileCount">0</span>
      </h2>
      <div class="head-actions">
        <button class="btn danger" id="deleteAll" onclick="deleteAll()" hidden>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
          <span class="lbl">Delete all</span>
        </button>
        <a class="btn primary" id="downloadAll" href="/download-all" hidden>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v12M6 10l6 6 6-6"/><path d="M4 20h16"/></svg>
          Download all
        </a>
        <button class="btn icon-btn refresh-btn" onclick="loadFiles()" title="Refresh" aria-label="Refresh">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>
        </button>
      </div>
    </div>
    <div class="files" id="fileList"><div class="empty">Loading…</div></div>
  </section>

  </div>
  </div>

</div>

<div class="toast" id="toast"></div>

<div class="modal-backdrop" id="modal" aria-hidden="true">
  <div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="modalTitle" aria-describedby="modalMsg">
    <div class="modal-icon">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
    </div>
    <h3 id="modalTitle"></h3>
    <p id="modalMsg"></p>
    <div class="modal-actions">
      <button class="btn" id="modalCancel">Cancel</button>
      <button class="btn solid-danger" id="modalOk">Delete</button>
    </div>
  </div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.js"></script>
<script>
const $ = id => document.getElementById(id);
const sharedText = $('sharedText');
const fileInput = $('fileInput');
const dropZone = $('dropZone');

/* ---------- helpers ---------- */
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function fmtSize(b) {
  if (b < 1024) return b + ' B';
  const u = ['KB', 'MB', 'GB', 'TB'];
  let i = -1;
  do { b /= 1024; i++; } while (b >= 1024 && i < u.length - 1);
  return b.toFixed(b < 10 ? 1 : 0) + ' ' + u[i];
}
function timeAgo(sec) {
  const d = Math.max(0, Date.now() / 1000 - sec);
  if (d < 60) return 'just now';
  if (d < 3600) return Math.floor(d / 60) + ' min ago';
  if (d < 86400) return Math.floor(d / 3600) + ' h ago';
  return new Date(sec * 1000).toLocaleDateString();
}
function extInfo(name) {
  const m = name.match(/\\.([a-z0-9]{1,5})$/i);
  const ext = m ? m[1].toLowerCase() : '';
  const groups = {
    img: 'jpg jpeg png gif webp heic heif svg bmp avif',
    vid: 'mp4 mov mkv webm avi m4v',
    aud: 'mp3 wav m4a aac flac ogg opus',
    doc: 'pdf doc docx txt md rtf xls xlsx csv ppt pptx key pages numbers',
    zip: 'zip rar 7z tar gz tgz bz2 xz dmg apk ipa',
  };
  let cls = '';
  for (const k in groups) if (groups[k].split(' ').includes(ext)) cls = k;
  return { label: ext ? ext.slice(0, 4) : 'file', cls };
}
let toastTimer;
function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 1800);
}
async function copyText(text, msg) {
  if (!text) return toast('Nothing to copy');
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // navigator.clipboard needs HTTPS; fall back for plain-http LAN access
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  toast(msg);
}

/* ---------- realtime ---------- */
const socket = io();
const setStatus = live => {
  $('status').classList.toggle('live', live);
  $('statusText').textContent = live ? 'Live' : 'Offline';
};
socket.on('connect', () => { setStatus(true); loadFiles(); });
socket.on('disconnect', () => setStatus(false));
socket.on('files_changed', loadFiles);

socket.on('text_update', data => {
  if (sharedText.value !== data.text) {
    sharedText.value = data.text;
    updateCount();
  }
});
function updateCount() {
  const n = sharedText.value.length;
  $('charCount').textContent = n + (n === 1 ? ' character' : ' characters');
}
sharedText.addEventListener('input', () => {
  updateCount();
  socket.emit('text_update', { text: sharedText.value });
});
function clearText() {
  sharedText.value = '';
  updateCount();
  socket.emit('text_update', { text: '' });
}

/* ---------- files ---------- */
async function loadFiles() {
  let files = [];
  try {
    files = await (await fetch('/files')).json();
  } catch {
    return;
  }
  $('fileCount').textContent = files.length;
  $('downloadAll').hidden = files.length < 2;
  $('deleteAll').hidden = files.length === 0;
  const list = $('fileList');
  if (files.length === 0) {
    list.innerHTML = `
      <div class="empty">
        <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
        <div>No files yet — send one from any device.</div>
      </div>`;
    return;
  }
  list.innerHTML = files.map(f => {
    const e = extInfo(f.name);
    return `
      <div class="file">
        <div class="ext ${e.cls}">${esc(e.label)}</div>
        <div class="meta">
          <div class="name" title="${esc(f.name)}">${esc(f.name)}</div>
          <div class="sub">${fmtSize(f.size)} · ${timeAgo(f.mtime)}</div>
        </div>
        <button class="btn icon-btn danger-ghost" data-name="${esc(f.name)}" onclick="deleteFile(this.dataset.name)" title="Delete" aria-label="Delete ${esc(f.name)}">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
        </button>
        <a class="btn icon-btn" href="/download/${encodeURIComponent(f.name)}" title="Download" aria-label="Download ${esc(f.name)}">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v12M6 10l6 6 6-6"/><path d="M4 20h16"/></svg>
        </a>
      </div>`;
  }).join('');
}

/* Promise-based confirm modal. Resolves true on confirm, false on cancel/Esc/backdrop. */
function confirmModal({ title, html, okText = 'Delete' }) {
  const modal = $('modal'), ok = $('modalOk'), cancel = $('modalCancel');
  $('modalTitle').textContent = title;
  $('modalMsg').innerHTML = html;
  ok.textContent = okText;
  const prevFocus = document.activeElement;
  modal.classList.add('open');
  modal.setAttribute('aria-hidden', 'false');
  setTimeout(() => cancel.focus(), 50);  // safe default: Enter won't delete by accident

  return new Promise(resolve => {
    const close = result => {
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      ok.onclick = cancel.onclick = modal.onclick = null;
      document.removeEventListener('keydown', onKey);
      if (prevFocus) prevFocus.focus();
      resolve(result);
    };
    const onKey = e => {
      if (e.key === 'Escape') close(false);
      if (e.key === 'Tab') {  // keep focus inside the dialog
        e.preventDefault();
        (document.activeElement === ok ? cancel : ok).focus();
      }
    };
    ok.onclick = () => close(true);
    cancel.onclick = () => close(false);
    modal.onclick = e => { if (e.target === modal) close(false); };
    document.addEventListener('keydown', onKey);
  });
}

async function deleteFile(name) {
  const yes = await confirmModal({
    title: 'Delete file?',
    html: `<b>${esc(name)}</b> will be removed from every device. This can't be undone.`,
  });
  if (!yes) return;
  const res = await fetch('/delete/' + encodeURIComponent(name), { method: 'POST' });
  toast(res.ok ? 'Deleted' : 'Delete failed');
  loadFiles();
}

async function deleteAll() {
  const n = Number($('fileCount').textContent) || 0;
  const yes = await confirmModal({
    title: `Delete all ${n} file${n === 1 ? '' : 's'}?`,
    html: `Everything in the shared folder will be removed from every device. This can't be undone.`,
    okText: 'Delete all',
  });
  if (!yes) return;
  const res = await fetch('/delete-all', { method: 'POST' });
  toast(res.ok ? 'All files deleted' : 'Delete failed');
  loadFiles();
}

/* ---------- upload ---------- */
function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (files.length === 0) return;

  const form = new FormData();
  files.forEach(f => form.append('file', f));

  const total = files.reduce((s, f) => s + f.size, 0);
  const queue = $('queue');
  const bar = $('bar');
  queue.classList.add('show');
  bar.style.width = '0%';
  $('queuePct').textContent = '0%';
  $('queueLabel').textContent = `Uploading ${files.length} file${files.length > 1 ? 's' : ''} · ${fmtSize(total)}`;
  $('qitems').innerHTML = files.map((f, i) => `
    <div class="qitem" id="q${i}">
      <span class="st">○</span>
      <span class="n">${esc(f.name)}</span>
      <span class="s">${fmtSize(f.size)}</span>
    </div>`).join('');

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/upload');

  xhr.upload.onprogress = e => {
    if (!e.lengthComputable) return;
    const pct = Math.round((e.loaded / e.total) * 100);
    bar.style.width = pct + '%';
    $('queuePct').textContent = pct + '%';
    // Files are sent in order, so mark each one done once its bytes have passed.
    const sent = e.loaded / e.total * total;
    let acc = 0;
    files.forEach((f, i) => {
      acc += f.size;
      const el = $('q' + i);
      if (sent >= acc && !el.classList.contains('done')) {
        el.classList.add('done');
        el.querySelector('.st').textContent = '✓';
      }
    });
  };

  const finish = ok => {
    fileInput.value = '';
    if (ok) {
      bar.style.width = '100%';
      $('queuePct').textContent = '100%';
      document.querySelectorAll('.qitem').forEach(el => {
        el.classList.add('done');
        el.querySelector('.st').textContent = '✓';
      });
      toast(`${files.length} file${files.length > 1 ? 's' : ''} sent`);
      loadFiles();
      setTimeout(() => queue.classList.remove('show'), 1500);
    } else {
      queue.classList.remove('show');
      toast('Upload failed');
    }
  };
  xhr.onload = () => finish(xhr.status >= 200 && xhr.status < 300);
  xhr.onerror = () => finish(false);

  xhr.send(form);
}

dropZone.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => uploadFiles(fileInput.files));

['dragenter', 'dragover'].forEach(ev => dropZone.addEventListener(ev, e => {
  e.preventDefault();
  dropZone.classList.add('over');
}));
['dragleave', 'drop'].forEach(ev => dropZone.addEventListener(ev, e => {
  e.preventDefault();
  dropZone.classList.remove('over');
}));
dropZone.addEventListener('drop', e => uploadFiles(e.dataTransfer.files));

// Allow dropping anywhere on the page without the browser opening the file.
['dragover', 'drop'].forEach(ev => window.addEventListener(ev, e => e.preventDefault()));

loadFiles();
</script>

</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML, address=base_url())


@app.route("/files")
def files():
    items = []
    for name in os.listdir(SHARE_DIR):
        path = os.path.join(SHARE_DIR, name)
        if os.path.isfile(path) and not name.startswith("."):
            st = os.stat(path)
            items.append({"name": name, "size": st.st_size, "mtime": st.st_mtime})
    items.sort(key=lambda f: f["mtime"], reverse=True)
    return jsonify(items)


@app.route("/upload", methods=["POST"])
def upload():
    saved = 0
    for f in request.files.getlist("file"):
        name = os.path.basename(f.filename or "")
        if not name:
            continue
        f.save(os.path.join(SHARE_DIR, name))
        saved += 1
    if saved:
        socketio.emit("files_changed")
    return ("", 200)


@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(SHARE_DIR, filename, as_attachment=True)


def _shared_path(name):
    """Resolve a name inside SHARE_DIR, refusing anything that escapes it."""
    name = os.path.basename(name)
    path = os.path.join(SHARE_DIR, name)
    if not name or name.startswith(".") or not os.path.isfile(path):
        return None
    return path


@app.route("/delete/<path:filename>", methods=["POST"])
def delete(filename):
    path = _shared_path(filename)
    if not path:
        return ("Not found", 404)
    os.remove(path)
    socketio.emit("files_changed")
    return ("", 204)


@app.route("/delete-all", methods=["POST"])
def delete_all():
    for n in os.listdir(SHARE_DIR):
        path = _shared_path(n)
        if path:
            os.remove(path)
    socketio.emit("files_changed")
    return ("", 204)


@app.route("/download-all")
def download_all():
    names = sorted(
        n for n in os.listdir(SHARE_DIR)
        if os.path.isfile(os.path.join(SHARE_DIR, n)) and not n.startswith(".")
    )
    if not names:
        return ("No files to download", 404)
    # Spill to disk so large shares don't sit in memory. Files are stored, not
    # compressed — most shared files (photos, videos, PDFs) are already compressed.
    tmp = tempfile.TemporaryFile()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for n in names:
            zf.write(os.path.join(SHARE_DIR, n), arcname=n)
    tmp.seek(0)
    return send_file(tmp, mimetype="application/zip", as_attachment=True,
                     download_name="LocalShare.zip")


@socketio.on('connect')
def handle_connect():
    emit('text_update', {'text': SHARED_TEXT})


@socketio.on('text_update')
def handle_text_update(data):
    global SHARED_TEXT
    SHARED_TEXT = data['text']
    emit('text_update', {'text': SHARED_TEXT}, broadcast=True, include_self=False)


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    local_url = f"{'https' if USE_HTTPS else 'http'}://localhost:{PORT}"
    if port_in_use(PORT):
        print(f" * Port {PORT} is already in use — LocalShare may already be running.")
        if OPEN_BROWSER:
            webbrowser.open(local_url)
        sys.exit(1)

    ssl_context = make_ssl_context() if USE_HTTPS else None
    if OPEN_BROWSER:
        threading.Timer(1.5, webbrowser.open, args=[local_url]).start()
    print(f" * LocalShare: open {base_url()} on your other devices")
    if USE_HTTPS:
        print(" * First visit on each device shows a certificate warning (self-signed);"
              " choose Advanced -> Proceed once.")
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True,
                 ssl_context=ssl_context)
