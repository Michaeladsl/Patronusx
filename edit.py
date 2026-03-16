import os
import re
import json
import argparse
from typing import List, Tuple, Optional

# FIX: was using script_dir (pipx site-packages) — now uses PATRONUS_BASE_DIR
PATRONUS_BASE_DIR = os.path.expanduser('~/.local/.patronus')
STATIC_DIR = os.path.join(PATRONUS_BASE_DIR, 'static')


class ValidationError(Exception):
    pass

class Cast:
    @staticmethod
    def decode(file, debug=False):
        lines = file.readlines()
        if not lines:
            return None
        try:
            header = json.loads(lines[0].strip())
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid header: {e}")
        events = []
        for line in lines[1:]:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    if debug:
                        print(f"Skipping malformed event line: {line} - {e}")
        return {'header': header, 'events': events}

    @staticmethod
    def validate(cast, debug=False):
        if not isinstance(cast.get('header'), dict):
            raise ValidationError("Cast header must be a dict")
        if not isinstance(cast.get('events'), list):
            raise ValidationError("Cast events must be a list")

    @staticmethod
    def encode(file, cast, debug=False):
        file.write(json.dumps(cast['header']) + '\n')
        for event in cast['events']:
            file.write(json.dumps(event) + '\n')


def parse_quantize_ranges(ranges: List[str], debug=False) -> List[Tuple[float, float]]:
    parsed = []
    for r in ranges:
        try:
            val = float(r)
            parsed.append((0.0, val))
        except ValueError:
            parts = r.split('-')
            if len(parts) == 2:
                parsed.append((float(parts[0]), float(parts[1])))
    return parsed


class QuantizeTransformation:
    def __init__(self, ranges: List[Tuple[float, float]]):
        self.ranges = ranges

    def transform(self, cast, debug=False):
        events = cast['events']
        for i in range(1, len(events)):
            prev_time = float(events[i - 1][0])
            curr_time = float(events[i][0])
            gap = curr_time - prev_time
            for lo, hi in self.ranges:
                if gap > hi:
                    events[i][0] = round(prev_time + hi, 6)
                    break
        cast['events'] = events


class Transformer:
    def __init__(self, transformation, input_file, output_file, debug=False):
        self.transformation = transformation
        self.input_file = input_file
        self.output_file = output_file
        self.debug = debug

    def transform(self):
        try:
            if self.debug:
                print(f"Reading file: {self.input_file}")
            with open(self.input_file, 'r') as infile:
                cast = Cast.decode(infile, self.debug)
                if not cast:
                    return
                Cast.validate(cast, self.debug)
                self.transformation.transform(cast, self.debug)

            if self.debug:
                print(f"Writing file: {self.output_file}")
            with open(self.output_file, 'w') as outfile:
                Cast.encode(outfile, cast, self.debug)
        except Exception as e:
            if self.debug:
                raise ValidationError(f"Error processing file {self.input_file}: {e}")


def quantize_action(splits_dir: str, debug: bool):
    # FIX: was using script_dir (site-packages) to build input_dir
    if debug:
        print(f"Input directory: {splits_dir}")

    default_range = ["2"]
    quantize_ranges = parse_quantize_ranges(default_range, debug)
    transformation = QuantizeTransformation(quantize_ranges)

    for filename in os.listdir(splits_dir):
        if filename.endswith(".cast"):
            input_path = os.path.join(splits_dir, filename)
            output_path = input_path

            if debug:
                print(f"Processing file: {input_path}")
            try:
                transformer = Transformer(transformation, input_path, output_path, debug)
                transformer.transform()
            except ValidationError as e:
                print(f"ValidationError processing file {input_path}: {e}")
            except Exception as e:
                print(f"Unexpected error processing file {input_path}: {e}")


def main():
    parser = argparse.ArgumentParser(description='Quantize Asciinema Casts in a directory.')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    args = parser.parse_args()

    splits_dir = os.path.join(STATIC_DIR, 'splits')
    try:
        quantize_action(splits_dir, args.debug)
    except ValidationError as e:
        print(f"Error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main()
