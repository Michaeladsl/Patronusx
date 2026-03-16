from flask import Flask, render_template_string, request, jsonify
import subprocess
import os
import shutil
import psutil
import json
import re
import pyte
from tqdm import tqdm
import sys

home_dir = os.path.expanduser("~")
patronus_base_dir = os.path.join(home_dir, ".local", ".patronus")
patronus_static_dir = os.path.join(patronus_base_dir, "static")
splits_dir = os.path.join(patronus_static_dir, 'splits')
full_dir = os.path.join(patronus_base_dir, 'full')
redacted_full_dir = os.path.join(patronus_base_dir, 'redacted_full')
text_dir = os.path.join(patronus_static_dir, 'text')

app = Flask(__name__, static_folder=patronus_static_dir)

status_file_path = os.path.join(patronus_static_dir, 'status_file.txt')

@app.route('/status')
def status():
    try:
        with open(status_file_path, 'r') as file:
            processing_status = file.read().strip()
    except IOError:
        processing_status = "Status unavailable"

    return jsonify({"status": processing_status})

def get_cast_files():
    # FIX: Guard against missing splits_dir (e.g. first run before split.py completes)
    if not os.path.exists(splits_dir):
        return [], {}

    files = [f for f in os.listdir(splits_dir) if f.endswith('.cast')]

    mappings_file = os.path.join(splits_dir, 'file_timestamp_mapping.json')
    timestamps = {}
    # FIX: Guard against missing mapping file (crashes on fresh installs)
    if os.path.exists(mappings_file):
        with open(mappings_file, 'r') as f:
            mappings = json.load(f)
            timestamps = {file_path: timestamp for file_path, timestamp in mappings.items() if timestamp is not None}

    if request.path.startswith('/command/'):
        sorted_files = sorted(files, key=lambda x: timestamps.get(os.path.join(splits_dir, x), ''))
    else:
        sorted_files = sorted(files)

    tools = sorted(set(f.split('_')[0] for f in sorted_files))
    files_dict = {tool: [f for f in sorted_files if f.split('_')[0] == tool] for tool in tools}
    return tools, files_dict


def strip_ansi_sequences(file_path):
    with open(file_path, 'r') as f:
        content = f.read()
        return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', content)

def search_index(query):
    results = []
    if os.path.exists(text_dir):
        for text_file in os.listdir(text_dir):
            if text_file.endswith('.txt'):
                with open(os.path.join(text_dir, text_file), 'r') as f:
                    content = f.read()
                    if query.lower() in content.lower():
                        results.append(text_file.replace('.txt', ''))
    return results

def get_disk_usage():
    try:
        disk_usage = psutil.disk_usage('/')
        free_gb = disk_usage.free / (1024 ** 3)
    except OSError as e:
        print(f"Error retrieving disk usage for root partition: {e}")
        free_gb = 0

    def get_directory_size(path):
        if not os.path.exists(path):
            return 0
        total_size = sum(os.path.getsize(os.path.join(dirpath, filename))
                         for dirpath, _, filenames in os.walk(path)
                         for filename in filenames)
        return total_size / (1024 ** 3)

    recordings_size = (
        get_directory_size(splits_dir) +
        get_directory_size(redacted_full_dir) +
        get_directory_size(full_dir) +
        get_directory_size(text_dir)
    )
    return free_gb, recordings_size


def load_favorites():
    favorites_file = os.path.join(patronus_static_dir, 'favorites.txt')
    favorites = {}
    if os.path.exists(favorites_file):
        with open(favorites_file, 'r') as f:
            for line in f:
                filename = line.strip()
                if filename:
                    favorites[filename] = True
    return favorites

def save_favorites(favorites):
    favorites_file = os.path.join(patronus_static_dir, 'favorites.txt')
    with open(favorites_file, 'w') as f:
        for filename in favorites:
            f.write(filename + '\n')

favorites = load_favorites()


def combine_cast_files(input_files, output_file, debug=False):
    combined_events = []
    start_time_offset = 0.0
    header = None

    for file in tqdm(input_files, desc="Combining CAST Files"):
        file_path = os.path.join(splits_dir, file)
        with open(file_path, 'r') as f:
            lines = f.readlines()
            if not header:
                header = json.loads(lines[0].strip())
                combined_events.append(json.dumps(header))
            events = [json.loads(line.strip()) for line in lines[1:]]

            if not events:
                continue

            if len(combined_events) > 1:
                last_event = json.loads(combined_events[-1])
                if debug:
                    print(f"Last event: {last_event}")
                if not isinstance(last_event, list) or len(last_event) < 1:
                    raise ValueError("Last event is not in the expected format.")

                last_event_time = float(last_event[0])
            else:
                last_event_time = 0.0

            first_event_time = float(events[0][0])
            if last_event_time > 0.0:
                start_time_offset += last_event_time - first_event_time

            for event in events:
                event_time = float(event[0]) + start_time_offset
                combined_events.append(json.dumps([event_time, event[1], event[2]]))

    output_path = os.path.join(splits_dir, output_file)
    with open(output_path, 'w') as f:
        f.write('\n'.join(combined_events) + '\n')

    if debug:
        print(f"Combined {len(input_files)} files into {output_file}")

@app.route('/combine_files', methods=['POST'])
def combine_files():
    data = request.json
    files = data['files']
    new_file_name = data['new_file_name']

    if not new_file_name.endswith('.cast'):
        new_file_name += '.cast'

    combine_cast_files(files, new_file_name, debug=True)

    favorites[new_file_name] = True
    save_favorites(favorites)

    return jsonify(success=True)


@app.route('/')
def index():
    tools, files_dict = get_cast_files()
    free_gb, recordings_size = get_disk_usage()
    return render_template_string(HTML_TEMPLATE, tools=tools, files_dict=files_dict, free_gb=free_gb, recordings_size=recordings_size, favorites=favorites)

@app.route('/command/<command>')
def command_page(command):
    _, files_dict = get_cast_files()
    command_files = files_dict.get(command, [])

    current_date = None
    files_with_dates = []

    for file in command_files:
        file_path = os.path.join(splits_dir, file)
        timestamp = get_timestamp(file_path)
        date = timestamp.split()[0] if timestamp else None

        if date and date != current_date:
            current_date = date
            files_with_dates.append(date)

        files_with_dates.append(file)

    return render_template_string(COMMAND_TEMPLATE, command=command, command_files=files_with_dates, favorites=favorites)

def get_timestamp(file_path):
    mappings_file = os.path.join(splits_dir, 'file_timestamp_mapping.json')
    # FIX: Guard against missing mapping file
    if not os.path.exists(mappings_file):
        return None
    with open(mappings_file, 'r') as f:
        mappings = json.load(f)
        timestamp = mappings.get(file_path)
        return timestamp


@app.route('/favorites')
def favorites_page():
    _, files_dict = get_cast_files()
    favorites_files = [file for file in favorites if file in sum(files_dict.values(), [])]
    return render_template_string(COMMAND_TEMPLATE, command="Favorites", command_files=favorites_files, favorites=favorites_files)

@app.route('/redact', methods=['POST'])
def redact_text():
    data = request.json
    word = data['word']
    file_to_redact = os.path.join(splits_dir, data['file'])

    redact_script_path = os.path.expanduser('~/.local/share/pipx/venvs/patronus/lib/python3.12/site-packages/redact.py')

    subprocess.run(['python3', redact_script_path, '-w', word, '-f', file_to_redact], check=True)

    return jsonify(success=True)


@app.route('/delete', methods=['POST'])
def delete_file():
    data = request.json
    file_path = os.path.join(splits_dir, data['file'])
    try:
        os.remove(file_path)
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/edit', methods=['POST'])
def edit_file_name():
    data = request.get_json()
    old_file_name = data['old_file']
    new_file_name = data['new_file']
    old_file_path = os.path.join(splits_dir, old_file_name)
    new_file_path = os.path.join(splits_dir, new_file_name)
    try:
        os.rename(old_file_path, new_file_path)
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

@app.route('/toggle_favorite', methods=['POST'])
def toggle_favorite():
    data = request.json
    file = data['file']
    if file in favorites:
        favorites.pop(file)
    else:
        favorites[file] = True
    save_favorites(favorites)
    return jsonify(success=True)

@app.route('/search')
def search_files():
    query = request.args.get('q', '')
    results = search_index(query)
    return jsonify(results)


# ─────────────────────────────────────────────────────────────────────────────
#  HTML TEMPLATES
# ─────────────────────────────────────────────────────────────────────────────

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Patronus</title>
    <style>
        *, *::before, *::after { box-sizing: border-box; }

        body {
            background-color: #000000;
            color: white;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 0 8px 24px;
        }

        /* ── Top bar ── */
        .top-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            width: 95%;
            margin: 12px auto 8px;
            gap: 10px;
            flex-wrap: wrap;
        }
        .disk-meter {
            background-color: rgba(22, 33, 62, 0.8);
            color: #aac4b8;
            padding: 8px 14px;
            border-radius: 5px;
            font-size: 13px;
            white-space: nowrap;
        }

        /* ── Status bar ── */
        /* FIX: hidden by default; shown only while processing */
        #statusMessage {
            display: none;
            background-color: rgba(22, 33, 62, 0.9);
            border: 1px solid #4ecca3;
            color: #4ecca3;
            padding: 8px 14px;
            text-align: center;
            font-size: 13px;
            border-radius: 5px;
            margin: 0 auto 10px;
            width: 95%;
            transition: opacity 0.4s;
        }
        #statusMessage.visible { display: block; }
        #statusMessage.done {
            border-color: #3ba888;
            color: #3ba888;
        }
        #statusMessage.failed {
            border-color: #E94560;
            color: #E94560;
        }

        /* ── Search ── */
        .search-wrapper {
            position: relative;
            flex: 1;
            min-width: 200px;
            max-width: 500px;
        }
        .search-icon {
            position: absolute;
            left: 10px;
            top: 50%;
            transform: translateY(-50%);
            color: #4ecca3;
            font-size: 15px;
            pointer-events: none;
        }
        input[type="text"]#searchInput {
            padding: 8px 10px 8px 34px;
            width: 100%;
            background-color: #16213e;
            border: 1px solid #4ecca3;
            color: white;
            font-size: 15px;
            border-radius: 5px;
            outline: none;
        }
        input[type="text"]#searchInput:focus {
            border-color: #6edfc0;
            box-shadow: 0 0 0 2px rgba(78, 204, 163, 0.2);
        }
        .search-results {
            background-color: #1a2540;
            border: 1px solid #4ecca3;
            color: white;
            position: absolute;
            width: 100%;
            display: none;
            z-index: 1000;
            border-radius: 0 0 5px 5px;
            max-height: 260px;
            overflow-y: auto;
        }
        .search-result {
            padding: 10px 14px;
            border-bottom: 1px solid rgba(78, 204, 163, 0.25);
            cursor: pointer;
            font-size: 14px;
        }
        .search-result:last-child { border-bottom: none; }
        .search-result:hover { background-color: #4ecca3; color: #000; }

        /* ── Buttons ── */
        .button {
            background-color: #16213e;
            border: 1px solid #4ecca3;
            color: white;
            padding: 15px;
            text-align: center;
            display: block;
            font-size: 17px;
            margin: 8px auto;
            cursor: pointer;
            border-radius: 5px;
            width: 95%;
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
            transition: box-shadow 0.2s, background-color 0.2s;
        }
        .button:hover {
            background-color: #1c2a52;
            box-shadow: 0 8px 16px rgba(0,0,0,0.3);
        }
        .favorites-button {
            background-color: #4ecca3;
            color: white;
            padding: 12px;
            text-align: center;
            text-decoration: none;
            display: block;
            font-size: 16px;
            margin: 8px auto;
            cursor: pointer;
            border-radius: 5px;
            width: 95%;
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
            transition: background-color 0.2s;
            font-weight: 600;
        }
        .favorites-button:hover { background-color: #3ba888; }

        /* ── Empty state ── */
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #4a6e62;
        }
        .empty-state .icon { font-size: 48px; margin-bottom: 16px; }
        .empty-state h2 { color: #4ecca3; margin: 0 0 10px; font-size: 20px; }
        .empty-state p { font-size: 14px; line-height: 1.6; max-width: 380px; margin: 0 auto; }
        .empty-state code {
            background: #16213e;
            border: 1px solid #4ecca3;
            border-radius: 4px;
            padding: 2px 7px;
            font-family: monospace;
            color: #4ecca3;
        }
    </style>
</head>
<body>
    <div class="top-bar">
        <div class="disk-meter">
            💾 Free: {{ free_gb|round(2) }} GB &nbsp;|&nbsp; Recordings: {{ recordings_size|round(2) }} GB
        </div>
        <div class="search-wrapper">
            <span class="search-icon">🔍</span>
            <input type="text" id="searchInput" placeholder="Search recordings…" oninput="performSearch()" autocomplete="off">
            <div id="searchResults" class="search-results"></div>
        </div>
    </div>

    <div id="statusMessage"></div>

    <a class="favorites-button" href="/favorites">⭐ Favorites</a>

    {% if files_dict %}
        {% for tool, count in files_dict.items() %}
        <div class="button" onclick="window.location.href='/command/{{ tool }}'">{{ tool }} <span style="color:#4ecca3;font-size:14px;">({{ count|length }})</span></div>
        {% endfor %}
    {% else %}
        <div class="empty-state">
            <div class="icon">📂</div>
            <h2>No recordings yet</h2>
            <p>Start a recording session with <code>patronus on</code>, run some commands, then stop with <code>patronus off</code>.<br><br>When you're done, run <code>patronus</code> to process and view your recordings here.</p>
        </div>
    {% endif %}

    <script>
        function performSearch() {
            let input = document.getElementById('searchInput');
            let dropdown = document.getElementById('searchResults');
            if (input.value.length < 1) {
                dropdown.style.display = 'none';
                return;
            }
            fetch(`/search?q=${encodeURIComponent(input.value)}`)
            .then(r => r.json())
            .then(data => {
                if (data.length) {
                    dropdown.innerHTML = data.map(item =>
                        `<div class="search-result" onclick="location.href='/command/${item.split('_')[0]}?open=${item}'">${item}</div>`
                    ).join('');
                    dropdown.style.display = 'block';
                } else {
                    dropdown.innerHTML = '<div class="search-result" style="color:#888;">No results found</div>';
                    dropdown.style.display = 'block';
                }
            })
            .catch(err => console.error('Search error:', err));
        }

        document.addEventListener('click', function(e) {
            const searchInput = document.getElementById('searchInput');
            const searchResults = document.getElementById('searchResults');
            if (e.target !== searchInput && !searchResults.contains(e.target)) {
                searchResults.style.display = 'none';
            }
        });

        // FIX: Status bar is hidden until processing is actually happening.
        // It auto-hides once Complete or Failed, and is only shown when relevant.
        function checkStatus() {
            fetch('/status')
            .then(r => r.json())
            .then(data => {
                const el = document.getElementById('statusMessage');
                const s = data.status;
                if (s && s !== 'Status unavailable') {
                    el.textContent = s;
                    el.classList.add('visible');
                    el.classList.toggle('done', s === 'Complete');
                    el.classList.toggle('failed', s === 'Failed');
                    if (s === 'Complete' || s === 'Failed') {
                        clearInterval(statusInterval);
                        // Auto-hide after 4 seconds once done
                        setTimeout(() => {
                            el.classList.remove('visible');
                        }, 4000);
                    }
                }
            })
            .catch(err => console.error('Status error:', err));
        }

        var statusInterval = setInterval(checkStatus, 5000);
        checkStatus();
    </script>
</body>
</html>
'''


COMMAND_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ command }} — Patronus</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='asciinema-player.css') }}">
<style>
    *, *::before, *::after { box-sizing: border-box; }

    body {
        background-color: #000000;
        color: white;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        margin: 0;
        padding: 0 8px 32px;
    }

    /* ── Back link ── */
    .back-link {
        display: inline-block;
        color: #4ecca3;
        text-decoration: none;
        font-size: 14px;
        margin: 14px 0 4px 2.5%;
        opacity: 0.8;
        transition: opacity 0.2s;
    }
    .back-link:hover { opacity: 1; }

    /* ── Page title ── */
    .page-title {
        font-size: 22px;
        font-weight: 600;
        color: #4ecca3;
        width: 95%;
        margin: 6px auto 14px;
        letter-spacing: 0.02em;
    }

    /* ── File rows ── */
    .file-name {
        background-color: #16213e;
        border: 1px solid #4ecca3;
        color: white;
        padding: 13px 15px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 16px;
        margin: 8px auto;
        cursor: pointer;
        border-radius: 5px;
        width: 95%;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        transition: background-color 0.2s, box-shadow 0.2s;
    }
    .file-name:hover { background-color: #1c2a52; box-shadow: 0 8px 16px rgba(0,0,0,0.3); }

    .file-text {
        flex-grow: 1;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        margin-right: 10px;
    }
    .edit-icon, .save-btn, .exit-btn {
        margin-left: 5px;
        cursor: pointer;
        visibility: hidden;
        flex-shrink: 0;
    }
    .file-name:hover .edit-icon { visibility: visible; }
    .save-btn { color: #4ecca3; font-size: 22px; }
    .exit-btn { color: #E94560; }
    .favorite {
        margin-left: 20px;
        cursor: pointer;
        font-size: 22px;
        flex-shrink: 0;
    }

    /* ── Dropdown content ── */
    .dropdown-content {
        display: none;
        width: 95%;
        margin: -4px auto 8px;
        background-color: #16213e;
        color: white;
        padding: 15px;
        border: 1px solid #4ecca3;
        border-top: none;
        border-radius: 0 0 5px 5px;
        box-shadow: 0 6px 12px rgba(0,0,0,0.25);
    }
    .redact-controls, .delete-controls {
        padding: 6px 0;
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .redact-controls input[type="text"] {
        flex: 1;
        padding: 7px 10px;
        background-color: #0d1526;
        border: 1px solid #4ecca3;
        color: white;
        font-size: 14px;
        border-radius: 4px;
        outline: none;
    }
    .redact-controls input[type="text"]:focus {
        border-color: #6edfc0;
        box-shadow: 0 0 0 2px rgba(78,204,163,0.2);
    }
    button {
        background-color: #4ecca3;
        border: none;
        color: #000;
        padding: 7px 14px;
        cursor: pointer;
        border-radius: 4px;
        font-size: 14px;
        font-weight: 600;
        transition: background-color 0.2s;
    }
    button:hover { background-color: #3ba888; }
    .delete-button {
        background-color: #E94560;
        color: white;
        padding: 8px 16px;
    }
    .delete-button:hover { background-color: #c73350; }

    /* ── Date separators ── */
    .file-date {
        color: #4ecca3;
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin: 20px auto 4px;
        width: 95%;
        opacity: 0.7;
        border-bottom: 1px solid rgba(78,204,163,0.2);
        padding-bottom: 4px;
    }

    /* ── Empty state ── */
    .empty-state {
        text-align: center;
        padding: 60px 20px;
        color: #4a6e62;
    }
    .empty-state .icon { font-size: 48px; margin-bottom: 16px; }
    .empty-state h2 { color: #4ecca3; margin: 0 0 10px; font-size: 20px; }
    .empty-state p { font-size: 14px; line-height: 1.6; max-width: 380px; margin: 0 auto; }

    /* ── Combine button ── */
    .combine-button {
        background-color: #4ecca3;
        color: #000;
        padding: 11px;
        text-align: center;
        cursor: pointer;
        border-radius: 5px;
        width: 95%;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        margin: 12px auto;
        display: block;
        font-size: 15px;
        font-weight: 600;
        border: none;
        transition: background-color 0.2s;
    }
    .combine-button:hover { background-color: #3ba888; }

    /* ── Combine modal overlay ── */
    /* FIX: Added proper modal backdrop so it dims the background */
    .modal-overlay {
        display: none;
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.75);
        z-index: 999;
        backdrop-filter: blur(2px);
    }
    .modal-overlay.active { display: block; }

    .combine-popups-container {
        display: none;
        position: fixed;
        left: 50%;
        top: 50%;
        transform: translate(-50%, -50%);
        width: 80%;
        max-width: 1100px;
        z-index: 1000;
        gap: 16px;
    }
    .combine-popups-container.active {
        display: flex;
    }
    .combine-popup {
        background-color: #16213e;
        border: 1px solid #4ecca3;
        max-height: 450px;
        padding: 20px;
        color: white;
        flex: 1;
        min-width: 280px;
        border-radius: 10px;
        overflow-y: auto;
    }
    .combine-popup h3 { margin: 0 0 12px; color: #4ecca3; font-size: 16px; }
    .combine-popup .item {
        padding: 10px 40px 10px 10px;
        background-color: #1a1a1a;
        margin: 5px 0;
        border: 1px solid #4ecca3;
        cursor: grab;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        position: relative;
        border-radius: 4px;
        font-size: 13px;
    }
    .combine-popup .item .remove-btn {
        color: #FF0000;
        cursor: pointer;
        position: absolute;
        right: 10px;
        top: 50%;
        transform: translateY(-50%);
        font-weight: bold;
    }
    .combine-popup .item .clone-btn {
        right: 10px;
        color: #00FF00;
        position: absolute;
        font-weight: bold;
        cursor: pointer;
    }
    .combine-popup .generate-button {
        background-color: #4ecca3;
        border: none;
        color: #000;
        padding: 10px;
        cursor: pointer;
        border-radius: 5px;
        width: 100%;
        margin-top: 10px;
        font-weight: 600;
    }
    .combine-popup .generate-button:hover { background-color: #3ba888; }
    .combine-popup input[type="text"] {
        width: 88%;
        padding: 7px 10px;
        background: #0d1526;
        border: 1px solid #4ecca3;
        color: white;
        border-radius: 4px;
        font-size: 14px;
    }
</style>
</head>
<body>
    <a class="back-link" href="/">← Back to commands</a>
    <div class="page-title">{{ command }}</div>

    {% set has_files = namespace(value=false) %}
    {% for item in command_files %}
        {% if item.endswith('.cast') %}{% set has_files.value = true %}{% endif %}
    {% endfor %}

    {% if not has_files.value %}
        <div class="empty-state">
            <div class="icon">🎬</div>
            <h2>No recordings here</h2>
            <p>No recordings found for <strong>{{ command }}</strong>. Run some commands while Patronus is active and they'll appear here.</p>
        </div>
    {% else %}
        {% set current_date = None %}
        {% for item in command_files %}
            {% if item.endswith('.cast') %}
                <div class="file-name" id="file-{{ loop.index }}" onclick="toggleDisplay('{{ item }}', event)">
                    <span id="name-{{ item }}" class="file-text">{{ item.split('.cast')[0] }}</span>
                    <i class="edit-icon" onclick="enableEdit('{{ item }}', event)">🖊️</i>
                    <i class="save-btn" onclick="confirmSave('{{ item }}', event)" style="visibility: hidden;">&#10004;</i>
                    <i class="exit-btn" onclick="exitEditMode('{{ item }}', event)" style="visibility: hidden;">&#10006;</i>
                    {% if item in favorites %}
                        <span class="favorite" onclick="toggleFavorite('{{ item }}', event)">&#9733;</span>
                    {% else %}
                        <span class="favorite" onclick="toggleFavorite('{{ item }}', event)">&#9734;</span>
                    {% endif %}
                </div>
                <div id="demo-{{ item }}" class="dropdown-content">
                    <div class="redact-controls">
                        <input type="text" id="redact-word-{{ item }}" placeholder="Word to redact…">
                        <button onclick="redactAndReload('{{ item }}')">Redact &amp; Reload</button>
                    </div>
                    <div class="delete-controls">
                        <button class="delete-button" onclick="deleteFile('{{ item }}')">🗑 Delete</button>
                    </div>
                </div>
            {% else %}
                <div class="file-date">{{ item }}</div>
            {% endif %}
        {% endfor %}
    {% endif %}

    {% if command == "Favorites" %}
        <button class="combine-button" onclick="openCombinePopup()">⚡ Combine Favorites</button>
        <!-- FIX: Proper dimming overlay -->
        <div class="modal-overlay" id="modalOverlay" onclick="closeCombinePopup()"></div>
        <div class="combine-popups-container" id="combinePopupsContainer">
            <div class="combine-popup" id="combinePopupLeft">
                <h3>Favorites (drag to right to include)</h3>
                <div id="draggableContainerLeft"></div>
            </div>
            <div class="combine-popup" id="combinePopupRight">
                <h3>Combine order</h3>
                <div id="draggableContainerRight"></div>
                <div style="display:flex;align-items:center;margin-top:10px;">
                    <input type="text" id="newFileNameRight" placeholder="New file name">
                    <span style="margin-left:5px;color:#aaa;">.cast</span>
                </div>
                <button class="generate-button" onclick="generateCombinedFile()">Generate Combined File</button>
            </div>
        </div>
    {% endif %}

    <script src="{{ url_for('static', filename='asciinema-player.min.js') }}"></script>
    <script>
        function toggleDisplay(filename, event) {
            if (event && event.target.closest('.edit-icon, .save-btn, .exit-btn, .favorite')) return;
            var player = document.getElementById('demo-' + filename);
            if (player.style.display === 'block') {
                player.style.display = 'none';
            } else {
                document.querySelectorAll('.dropdown-content').forEach(p => p.style.display = 'none');
                player.style.display = 'block';
                var ts = new Date().getTime();
                AsciinemaPlayer.create('/.patronus/static/splits/' + filename + '?_=' + ts, player);
            }
        }

        window.onload = function() {
            const params = new URLSearchParams(window.location.search);
            const openFile = params.get('open');
            if (openFile) {
                const player = document.getElementById('demo-' + openFile);
                if (player) {
                    player.style.display = 'block';
                    AsciinemaPlayer.create('/.patronus/static/splits/' + openFile + '?_=' + new Date().getTime(), player);
                }
            }
        };

        function openCombinePopup() {
            document.getElementById('modalOverlay').classList.add('active');
            document.getElementById('combinePopupsContainer').classList.add('active');
            initializePopups();
        }

        function closeCombinePopup() {
            document.getElementById('modalOverlay').classList.remove('active');
            document.getElementById('combinePopupsContainer').classList.remove('active');
        }

        function initializePopups() {
            const left = document.getElementById('draggableContainerLeft');
            const right = document.getElementById('draggableContainerRight');
            left.innerHTML = '';
            right.innerHTML = '';
            const favorites = {{ favorites | tojson }};
            Object.keys(favorites).forEach(file => {
                const item = document.createElement('div');
                item.className = 'item';
                item.draggable = true;
                item.textContent = file.replace('.cast', '');
                item.dataset.file = file;
                item.innerHTML += '<span class="clone-btn" onclick="cloneItem(this)">+</span>';
                addDragListeners(item);
                left.appendChild(item);
            });
            [left, right].forEach(container => {
                container.addEventListener('dragover', e => e.preventDefault());
                container.addEventListener('drop', e => {
                    e.preventDefault();
                    const dragged = document.querySelector('.dragging');
                    if (dragged) container.appendChild(dragged);
                });
            });
        }

        function addDragListeners(item) {
            item.addEventListener('dragstart', () => item.classList.add('dragging'));
            item.addEventListener('dragend', () => item.classList.remove('dragging'));
        }

        function cloneItem(btn) {
            const original = btn.parentElement;
            const clone = original.cloneNode(true);
            clone.querySelector('.clone-btn').onclick = function() { cloneItem(this); };
            clone.querySelector('.remove-btn') && (clone.querySelector('.remove-btn').onclick = function() { removeItem(this); });
            addDragListeners(clone);
            original.parentElement.appendChild(clone);
        }

        function removeItem(btn) {
            btn.parentElement.remove();
        }

        function generateCombinedFile() {
            const right = document.getElementById('draggableContainerRight');
            const items = right.querySelectorAll('.item');
            const files = Array.from(items).map(i => i.dataset.file);
            const newName = document.getElementById('newFileNameRight').value.trim();
            if (!newName) { alert('Please enter a file name.'); return; }
            if (!files.length) { alert('Add at least one recording to combine.'); return; }
            fetch('/combine_files', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files, new_file_name: newName })
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    closeCombinePopup();
                    window.location.reload();
                } else {
                    alert('Failed to combine files.');
                }
            }).catch(err => { console.error(err); alert('Error combining files.'); });
        }

        function enableEdit(file, event) {
            event.stopPropagation();
            var nameSpan = document.getElementById('name-' + file);
            var parentDiv = nameSpan.closest('.file-name');
            nameSpan.contentEditable = true;
            nameSpan.focus();
            parentDiv.querySelector('.edit-icon').style.visibility = 'hidden';
            parentDiv.querySelector('.save-btn').style.visibility = 'visible';
            parentDiv.querySelector('.exit-btn').style.visibility = 'visible';
        }

        function confirmSave(file, event) {
            event.stopPropagation();
            var nameSpan = document.getElementById('name-' + file);
            var newName = nameSpan.textContent.trim() + '.cast';
            fetch('/edit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ old_file: file, new_file: newName })
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    window.location.reload();
                } else {
                    alert('Failed to rename.');
                }
            }).catch(err => { console.error(err); alert('Error: ' + err.message); });
        }

        function exitEditMode(file, event) {
            event.stopPropagation();
            var nameSpan = document.getElementById('name-' + file);
            nameSpan.contentEditable = false;
            var parentDiv = nameSpan.closest('.file-name');
            parentDiv.querySelector('.edit-icon').style.visibility = 'visible';
            parentDiv.querySelector('.save-btn').style.visibility = 'hidden';
            parentDiv.querySelector('.exit-btn').style.visibility = 'hidden';
            nameSpan.textContent = file.split('.cast')[0];
        }

        function toggleFavorite(file, event) {
            event.stopPropagation();
            fetch('/toggle_favorite', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file })
            }).then(r => r.json()).then(data => {
                if (data.success) window.location.reload();
                else console.error('Failed to toggle favorite');
            }).catch(err => console.error(err));
        }

        function redactAndReload(filename) {
            var word = document.getElementById('redact-word-' + filename).value;
            if (!word) { alert('Enter a word to redact first.'); return; }
            fetch('/redact', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ word, file: filename })
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    var ts = new Date().getTime();
                    var playerContainer = document.getElementById('demo-' + filename);
                    playerContainer.innerHTML = '';
                    document.getElementById('redact-word-' + filename).value = '';
                    setTimeout(() => {
                        playerContainer.style.display = 'block';
                        AsciinemaPlayer.create('/.patronus/static/splits/' + filename + '?_=' + ts, playerContainer);
                    }, 1000);
                }
            }).catch(err => console.error(err));
        }

        function deleteFile(filename) {
            if (confirm('Delete ' + filename.split('.cast')[0] + '? This cannot be undone.')) {
                fetch('/delete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file: filename })
                }).then(r => r.json()).then(data => {
                    if (data.success) window.location.reload();
                    else alert('Failed to delete.');
                }).catch(err => { console.error(err); alert('Error deleting.'); });
            }
        }
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8005, debug=False)
