import json
import re
import sys
import os
import argparse

# FIX: was using script_dir (pipx site-packages) — now uses PATRONUS_BASE_DIR
PATRONUS_BASE_DIR = os.path.expanduser('~/.local/.patronus')


def extract_ansi_and_text(text):
    ansi_escape = re.compile(r'(\x1b\[[0-9;]*[mKDHCUJ])')
    parts = ansi_escape.split(text)
    ansi_positions = []
    clean_text = ""
    last_pos = 0

    for part in parts:
        if ansi_escape.match(part):
            ansi_positions.append((last_pos, part))
        else:
            clean_text += part
            last_pos += len(part)

    return clean_text, ansi_positions

def reinsert_ansi_codes(text, ansi_positions):
    for pos, code in reversed(ansi_positions):
        text = text[:pos] + code + text[pos:]
    return text

def redact_sensitive_info(text, redaction_word=None):
    clean_text, ansi_positions = extract_ansi_and_text(text)
    patterns = [
        (r'(?<=-H\s)["\']?\S+["\']?', lambda m: '*' * len(m.group())),
        (r'(?<=-p\s)["\']?\S+["\']?', lambda m: '*' * len(m.group())),
        (r'\b[a-fA-F0-9]{32}:[a-fA-F0-9]{32}\b', lambda m: '*' * len(m.group()))
    ]
    if redaction_word:
        patterns.append((re.escape(redaction_word), '*' * len(redaction_word)))
    for pattern, repl in patterns:
        clean_text = re.sub(pattern, repl, clean_text)
    return reinsert_ansi_codes(clean_text, ansi_positions)

def process_cast_file(input_file_path, output_file_path, redaction_word=None, force=False):
    if os.path.exists(output_file_path) and not force:
        print(f"Skipping already processed file: {output_file_path}")
        return
    temp_file_path = input_file_path + '.tmp'
    try:
        with open(input_file_path, 'r') as file, open(temp_file_path, 'w') as temp_out:
            for line in file:
                try:
                    record = json.loads(line)
                    if isinstance(record, list) and len(record) > 2 and record[1] == 'o':
                        record[2] = redact_sensitive_info(record[2], redaction_word)
                    json.dump(record, temp_out)
                    temp_out.write('\n')
                except json.JSONDecodeError as e:
                    print(f"Error processing line: {line.strip()} - {e}")
        os.replace(temp_file_path, output_file_path)
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

def main():
    parser = argparse.ArgumentParser(description="Redacts sensitive information from .cast files.")
    parser.add_argument('-f', '--file', help="Specify the full path to a single file to redact.")
    parser.add_argument('-w', '--word', help="Specify a custom word to redact.")
    args = parser.parse_args()

    # FIX: was script_dir = os.path.dirname(os.path.realpath(__file__))
    # which resolves to the pipx venv site-packages, not the data directory
    redacted_dir = os.path.join(PATRONUS_BASE_DIR, "static", "redacted_full")

    if args.file:
        input_file_path = args.file
        output_file_path = input_file_path if args.word else os.path.join(redacted_dir, os.path.basename(input_file_path))
        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
        process_cast_file(input_file_path, output_file_path, args.word, force=True)
    else:
        full_dir = os.path.join(PATRONUS_BASE_DIR, "static", "full")
        for root, dirs, files in os.walk(full_dir):
            for file in files:
                if file.endswith('.cast'):
                    input_file_path = os.path.join(root, file)
                    output_file_path = os.path.join(redacted_dir, os.path.relpath(input_file_path, full_dir))
                    if not os.path.exists(output_file_path):
                        os.makedirs(os.path.dirname(output_file_path), exist_ok=True)
                        process_cast_file(input_file_path, output_file_path, args.word)

if __name__ == "__main__":
    main()
