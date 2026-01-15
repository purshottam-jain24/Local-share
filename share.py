from flask import Flask, request, send_from_directory, render_template_string, jsonify
from flask_socketio import SocketIO, emit
import os

PORT = 8080
SHARE_DIR = "shared"

os.makedirs(SHARE_DIR, exist_ok=True)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

SHARED_TEXT = ""

HTML = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LocalShare</title>

<style>
body {
  font-family: system-ui, sans-serif;
  background: #0f172a;
  color: #e5e7eb;
  margin: 0;
  padding: 16px;
}

.card {
  background: #020617;
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 16px;
  box-shadow: 0 0 0 1px #1e293b;
}

h2 { margin-top: 0; }

.drop {
  border: 2px dashed #334155;
  padding: 24px;
  text-align: center;
  border-radius: 12px;
  cursor: pointer;
}

.file {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  gap: 12px;
}

.filename {
  max-width: 70%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.progress-wrap {
  display: none;
  margin-top: 12px;
}

.progress-name {
  font-size: 14px;
  margin-bottom: 6px;
}

.progress-bar {
  height: 10px;
  background: #1e293b;
  border-radius: 6px;
  overflow: hidden;
}

.progress {
  height: 100%;
  width: 0%;
  background: #2563eb;
  transition: width 0.1s;
}

a {
  color: #38bdf8;
  text-decoration: none;
}
</style>
</head>

<body>

<div class="card">
  <h2>📤 Upload (Phone → PC)</h2>
  <div class="drop" onclick="fileInput.click()">
    Tap or Drop File Here
    <input id="fileInput" type="file" hidden>
  </div>

  <div class="progress-wrap" id="progressWrap">
    <div class="progress-name" id="progressName"></div>
    <div class="progress-bar">
      <div class="progress" id="progressBar"></div>
    </div>
    <div id="progressPercent">0%</div>
  </div>
</div>

<div class="card">
  <h2>📝 Shared Clipboard</h2>
  <textarea id="sharedText" style="width: 100%; height: 150px; background: #0f172a; color: #e5e7eb; border: 1px solid #334155; border-radius: 6px; padding: 12px; font-family: inherit; resize: vertical;" placeholder="Type here to share text in real-time..."></textarea>
</div>

<div class="card">
  <h2>📥 Files</h2>
  <div id="fileList">Loading...</div>
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.js"></script>
<script>
const socket = io();
const sharedText = document.getElementById('sharedText');

socket.on('connect', () => {
    console.log('Connected to server');
});

socket.on('text_update', (data) => {
    if (sharedText.value !== data.text) {
        sharedText.value = data.text;
    }
});

sharedText.addEventListener('input', () => {
    socket.emit('text_update', { text: sharedText.value });
});

const fileInput = document.getElementById('fileInput');
const progressWrap = document.getElementById('progressWrap');
const progressBar = document.getElementById('progressBar');
const progressName = document.getElementById('progressName');
const progressPercent = document.getElementById('progressPercent');

async function loadFiles() {
  const res = await fetch('/files');
  const files = await res.json();

  const list = document.getElementById('fileList');
  if (files.length === 0) {
    list.innerHTML = '<p>No files yet</p>';
    return;
  }

  list.innerHTML = files.map(f => `
    <div class="file">
      <div class="filename" title="${f}">${f}</div>
      <a href="/download/${encodeURIComponent(f)}">⬇️</a>
    </div>
  `).join('');
}

fileInput.addEventListener('change', () => {
  const file = fileInput.files[0];
  if (!file) return;

  const form = new FormData();
  form.append('file', file);

  progressWrap.style.display = 'block';
  progressName.textContent = file.name;
  progressBar.style.width = '0%';
  progressPercent.textContent = '0%';

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/upload');

  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) {
      const percent = Math.round((e.loaded / e.total) * 100);
      progressBar.style.width = percent + '%';
      progressPercent.textContent = percent + '%';
    }
  };

  xhr.onload = () => {
    progressWrap.style.display = 'none';
    fileInput.value = '';
    loadFiles(); // refresh list
  };

  xhr.send(form);
});

loadFiles();
</script>

</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/files")
def files():
    return jsonify(sorted(os.listdir(SHARE_DIR)))

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files["file"]
    f.save(os.path.join(SHARE_DIR, f.filename))
    return ("", 200)

@app.route("/download/<path:filename>")
def download(filename):
    return send_from_directory(SHARE_DIR, filename, as_attachment=True)

@socketio.on('connect')
def handle_connect():
    emit('text_update', {'text': SHARED_TEXT})

@socketio.on('text_update')
def handle_text_update(data):
    global SHARED_TEXT
    SHARED_TEXT = data['text']
    emit('text_update', {'text': SHARED_TEXT}, broadcast=True, include_self=False)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)
