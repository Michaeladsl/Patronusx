import re
import sys
import json
import pyte
import os
import argparse
from tqdm import tqdm
from wcwidth import wcwidth

parser = argparse.ArgumentParser(description='Split CAST files')
parser.add_argument('--debug', action='store_true', help='Enable debug mode')

args = parser.parse_args()

# FIX: all data paths now use PATRONUS_BASE_DIR instead of script_dir,
# which pointed to the pipx venv's site-packages — not the data directory.
PATRONUS_BASE_DIR = os.path.expanduser('~/.local/.patronus')
STATIC_DIR = os.path.join(PATRONUS_BASE_DIR, 'static')


class PatchedScreen(pyte.Screen):
    def select_graphic_rendition(self, *attrs, private=False):
        super().select_graphic_rendition(*attrs)

def generate_filename(command, part_index, timestamp=None):
    cleaned_command_name = clean_filename(command)
    timestamp_part = timestamp.replace(' ', '_') if timestamp else ""
    base_name = f"{cleaned_command_name}_{timestamp_part}.cast"
    if len(base_name) > 255:
        base_name = base_name[:250] + ".cast"
    return f"{base_name}_{part_index}.cast"

def process_with_terminal_emulator(input_file, output_file):
    screen = PatchedScreen(236, 49)
    stream = pyte.Stream(screen)
    screen.reset()

    with open(input_file, 'r') as file:
        lines = file.readlines()

    lines = lines[1:]

    for line in lines:
        try:
            data = json.loads(line.strip())
            if isinstance(data, list) and len(data) == 3 and isinstance(data[2], str):
                text_with_escapes = data[2]
            else:
                text_with_escapes = line.strip()
        except json.JSONDecodeError:
            text_with_escapes = line.strip()

        stream.feed(text_with_escapes)

    output_lines = "\n".join(screen.display)

    try:
        with open(output_file, 'w') as file:
            file.write(output_lines)
    except Exception as e:
        print(f"Error writing to text file: {e}")

    return output_lines


def create_text_versions(static_dir):
    text_dir = os.path.join(static_dir, 'text')
    os.makedirs(text_dir, exist_ok=True)

    splits_dir = os.path.join(static_dir, 'splits')
    for root, _, files in os.walk(splits_dir):
        for file in files:
            if file.endswith('.cast'):
                input_file = os.path.join(root, file)
                relative_path = os.path.relpath(input_file, splits_dir)
                output_file = os.path.join(text_dir, os.path.splitext(relative_path)[0] + '.txt')
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                process_with_terminal_emulator(input_file, output_file)


# FIX: write_status used a bare 'status_file.txt' which breaks if CWD isn't
# the project directory. Now writes to the proper absolute path.
def write_status(status, static_dir=None):
    if static_dir is None:
        static_dir = STATIC_DIR
    status_file = os.path.join(static_dir, 'status_file.txt')
    with open(status_file, 'w') as file:
        file.write(status)


def split_file(input_dir, output_dir, debug=False):
    mapping_file = os.path.join(output_dir, 'file_timestamp_mapping.json')

    if os.path.exists(mapping_file):
        with open(mapping_file, 'r') as f:
            mapping = json.load(f)
    else:
        mapping = {}

    processed_files = set()
    files_to_process = [file for file in os.listdir(input_dir) if file.endswith('.cast')]
    total_files = len(files_to_process)

    write_status("Processing")

    try:
        for file in tqdm(files_to_process, desc="Splitting Redacted Files"):
            input_file_path = os.path.join(input_dir, file)
            output_file_path = generate_output_filename(file, output_dir)

            if file in mapping and os.path.getmtime(input_file_path) == mapping[file]:
                if debug:
                    print(f"Skipping file {file} as it hasn't changed since the last run.")
                continue

            if file not in processed_files:
                try:
                    process_cast_file(input_file_path, output_dir, mapping_file)
                    mapping[file] = os.path.getmtime(input_file_path)
                    with open(mapping_file, 'w') as f:
                        json.dump(mapping, f, indent=4)
                except Exception as e:
                    print(f"Error processing section of {input_file_path}: {e}")
                processed_files.add(file)
                if debug:
                    print(f"Processed file: {file}")

            if total_files > 0:
                current_progress = round((len(processed_files) / total_files) * 100)
                write_status(f"Processing {current_progress}% complete")

        write_status("Complete")

    except Exception as e:
        write_status("Failed")
        print(f"An error occurred during file splitting: {e}")


def process_cast_file(input_file_path, output_dir, mapping_file):
    trivial_commands = {'cd', 'ls', 'ls -la', 'nano', 'vi'}
    try:
        with open(input_file_path, 'r') as file:
            lines = file.readlines()
    except IOError as e:
        print(f"Error: Could not read file '{input_file_path}'. {e}")
        return

    header = None
    segments = []
    current_segment = []
    current_command = None
    current_timestamp = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if isinstance(data, dict):
            header = data
            continue

        if isinstance(data, list) and len(data) == 3:
            event_time, event_type, event_data = data
            if event_type == 'o':
                display = extract_command(event_data)
                if display and display != "initial":
                    command_name = clean_filename(display)
                    if not is_trivial_command(command_name, trivial_commands):
                        if current_segment:
                            segments.append((current_command, current_timestamp, current_segment))
                        current_command = command_name
                        current_timestamp = event_time
                        current_segment = []
                current_segment.append(line)

    if current_segment:
        segments.append((current_command, current_timestamp, current_segment))

    for command, timestamp, segment in segments:
        if command:
            output_filename = generate_output_filename(command, output_dir)
            content = [json.dumps(header)] + segment if header else segment
            write_segment(output_filename, content, timestamp)


def is_mapping_file_changed(file_path, mapping_file):
    try:
        with open(mapping_file, 'r') as f:
            mapping = json.load(f)
    except FileNotFoundError:
        mapping = {}
    current_timestamp = os.path.getmtime(file_path)
    stored_timestamp = mapping.get(file_path)
    return stored_timestamp is None or stored_timestamp != current_timestamp


def update_mapping_file(file_path, mapping_file):
    try:
        with open(mapping_file, 'r') as f:
            mapping = json.load(f)
    except FileNotFoundError:
        mapping = {}
    mapping[file_path] = os.path.getmtime(file_path)
    with open(mapping_file, 'w') as f:
        json.dump(mapping, f, indent=4)


def extract_plain_text(display):
    return "\n".join(line.rstrip() for line in display)


def adjust_time(line, start_time):
    parts = json.loads(line.strip())
    original_time = float(parts[0])
    return original_time, parts[1], parts[2]


def write_segment(filename, content, timestamp):
    with open(filename, 'w') as new_file:
        new_file.write('\n'.join(content))
    if args.debug:
        print(f"Created file: {filename}")

    mapping_file = os.path.join(os.path.dirname(filename), 'file_timestamp_mapping.json')
    try:
        with open(mapping_file, 'r') as f:
            mapping = json.load(f)
    except FileNotFoundError:
        mapping = {}
    mapping[filename] = timestamp
    with open(mapping_file, 'w') as f:
        json.dump(mapping, f, indent=4)


def write_plain_text(filename, content):
    with open(filename, 'w') as text_file:
        text_file.write('\n'.join(content))
    print(f"Created plain text file: {filename}")


def extract_command(display):
    lines = display.split('\n')
    for line in reversed(lines):
        if '➜' in line:
            command = line.split('➜')[-1].strip()
            parts = command.split()
            if parts:
                if parts[0].startswith(('python3', 'sudo')):
                    full_command = " ".join(parts[1:])
                    return full_command.replace(' ', '_')
            return command.replace(' ', '_')
        elif '└─$' in line:
            command = line.split('└─$')[-1].strip()
            parts = command.split()
            if parts:
                if parts[0].startswith(('python3', 'sudo')):
                    full_command = " ".join(parts[1:])
                    return full_command.replace(' ', '_')
            return command.replace(' ', '_')
    return "initial"


def clean_filename(command_name):
    command_name = re.sub(r'(-p\s+\S+)', '-p', command_name)
    command_name = re.sub(r'(-H\s+\S+)', '-H', command_name)
    command_name = re.sub(r'[^a-zA-Z0-9_]', '_', command_name)
    command_name = command_name.lstrip('_')
    if not command_name:
        command_name = "command"
    return command_name


def generate_output_filename(command, output_dir):
    cleaned_command_name = clean_filename(command)
    output_filename = os.path.join(output_dir, f"{cleaned_command_name}.cast")
    if os.path.exists(output_filename):
        index = 1
        while True:
            new_output_filename = os.path.join(output_dir, f"{cleaned_command_name}_{index}.cast")
            if not os.path.exists(new_output_filename):
                return new_output_filename
            index += 1
    return output_filename


def is_trivial_command(command, trivial_commands):
    return command.split('_')[0] in trivial_commands


if __name__ == "__main__":
    # FIX: was using script_dir (pipx site-packages) — now uses PATRONUS_BASE_DIR
    input_dir = os.path.join(STATIC_DIR, 'redacted_full')
    output_dir = os.path.join(STATIC_DIR, 'splits')

    # Ensure directories exist before trying to list them
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    split_file(input_dir, output_dir, args.debug)
    create_text_versions(STATIC_DIR)
