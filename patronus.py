import argparse
import subprocess
import os
import sys
import shutil

PATRONUS_BASE_DIR = os.path.expanduser('~/.local/.patronus')

def make_script_executable(script_path):
    if not os.access(script_path, os.X_OK):
        os.chmod(script_path, os.stat(script_path).st_mode | 0o111)

def find_script_path(script_name):
    """Finds the path of the script within the pipx environment."""
    venv_root = sys.prefix
    script_path_main = os.path.join(venv_root, '..', script_name)
    script_path_venv_root = os.path.join(venv_root, script_name)
    # FIX: hardcoded python3.12 path will break on other Python versions
    # Try to find the correct site-packages dir dynamically
    import glob
    site_packages_glob = os.path.join(venv_root, 'lib', 'python3.*', 'site-packages', script_name)
    site_packages_matches = glob.glob(site_packages_glob)
    script_path_site = site_packages_matches[0] if site_packages_matches else ''

    if os.path.exists(script_path_main):
        return script_path_main
    elif os.path.exists(script_path_venv_root):
        return script_path_venv_root
    elif script_path_site and os.path.exists(script_path_site):
        return script_path_site
    else:
        raise FileNotFoundError(
            f"Script '{script_name}' not found. Searched:\n"
            f"  {script_path_main}\n"
            f"  {script_path_venv_root}\n"
            f"  {site_packages_glob}"
        )

def start_flask_server_in_tmux():
    check_session_command = "tmux has-session -t flask_server 2>/dev/null"
    result = subprocess.run(check_session_command, shell=True)
    if result.returncode == 0:
        print("flask_server tmux session already active")
        return

    flask_script_path = find_script_path('server.py')
    tmux_command = f"tmux new-session -d -s flask_server 'python3 {flask_script_path}'"
    subprocess.run(tmux_command, shell=True, check=True)

def run_script(script_name, args):
    """Runs a script from its original location within the pipx environment."""
    full_script_path = find_script_path(script_name)
    make_script_executable(full_script_path)
    command = [full_script_path] + args if script_name.endswith('.sh') else ['python3', full_script_path] + args
    subprocess.run(command, check=True)

def setup_directories():
    """Sets up the main directory structure in the user's home directory."""
    if not os.path.exists(PATRONUS_BASE_DIR):
        os.makedirs(PATRONUS_BASE_DIR)
        print(f"Created directory: {PATRONUS_BASE_DIR}")

    for subdir in ['full', 'redacted_full', 'splits']:
        subdir_path = os.path.join(PATRONUS_BASE_DIR, 'static', subdir)
        if not os.path.exists(subdir_path):
            os.makedirs(subdir_path)
            print(f"Created directory: {subdir_path}")

    static_src_dir = None
    # FIX: dynamically find site-packages instead of hardcoding python3.12
    import glob
    pattern = os.path.join(sys.prefix, 'lib', 'python3.*', 'site-packages', 'static')
    matches = glob.glob(pattern)
    if matches:
        static_src_dir = matches[0]

    static_dest_dir = os.path.join(PATRONUS_BASE_DIR, 'static')
    if static_src_dir and os.path.exists(static_src_dir) and not os.path.exists(static_dest_dir):
        shutil.copytree(static_src_dir, static_dest_dir)
        print(f"Copied static files from {static_src_dir} to {static_dest_dir}")

def remove_gitkeep_files():
    for subdir in ['redacted_full', 'full', 'splits']:
        gitkeep_path = os.path.join(PATRONUS_BASE_DIR, 'static', subdir, '.gitkeep')
        if os.path.exists(gitkeep_path):
            os.remove(gitkeep_path)
            print(f"Removed .gitkeep from {gitkeep_path}")

def nuke_directories():
    for subdir in ['full', 'redacted_full', 'splits']:
        full_path = os.path.join(PATRONUS_BASE_DIR, 'static', subdir)
        for item in os.listdir(full_path):
            item_path = os.path.join(full_path, item)
            if os.path.isfile(item_path) or os.path.islink(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)
        print(f"Nuked all contents from {full_path}")

# FIX: README claims `patronus redact,split,server,config` works, but the old
# argparse setup only accepted 'on' or 'off' as positional args — any other
# value raised an error. This is now implemented as a --run flag that accepts
# a comma-separated list of components to run selectively.
VALID_COMPONENTS = {
    'redact': 'redact.py',
    'split':  'split.py',
    'server': None,          # handled specially (tmux)
    'edit':   'edit.py',
    'config': 'configure.sh',
}

def run_components(components_str):
    """Parse and run a comma-separated list of components."""
    requested = [c.strip().lower() for c in components_str.split(',')]
    unknown = [c for c in requested if c not in VALID_COMPONENTS]
    if unknown:
        print(f"[patronus] Unknown component(s): {', '.join(unknown)}")
        print(f"[patronus] Valid options: {', '.join(VALID_COMPONENTS.keys())}")
        sys.exit(1)

    for component in requested:
        script = VALID_COMPONENTS[component]
        if component == 'server':
            print("[patronus] Starting Flask server in tmux…")
            start_flask_server_in_tmux()
            print("Server started: http://127.0.0.1:8005")
        elif script:
            print(f"[patronus] Running {component}…")
            run_script(script, [])


def main():
    parser = argparse.ArgumentParser(
        description="Patronus: capture, redact, and review pentest terminal recordings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  patronus on                         # Start recording (configure zsh hook)
  patronus off                        # Stop recording
  patronus                            # Process recordings and launch web UI
  patronus --run redact,split,server  # Run specific components only
  patronus --run server               # Just (re)start the Flask server
  patronus --nuke                     # Wipe all recording data
        """
    )
    parser.add_argument(
        'mode',
        nargs='?',
        choices=['on', 'off'],
        help='Turn recording on or off (configures zsh hook via configure.sh)'
    )
    parser.add_argument(
        '--run',
        metavar='COMPONENTS',
        help=(
            'Comma-separated list of components to run. '
            'Valid values: redact, split, server, edit, config. '
            'Example: --run redact,split,server'
        )
    )
    parser.add_argument(
        '--nuke',
        action='store_true',
        help='Erase all contents from the recording directories'
    )
    args = parser.parse_args()

    setup_directories()

    if args.mode:
        if args.mode == 'on':
            run_script('configure.sh', [])
        elif args.mode == 'off':
            run_script('configure.sh', ['--undo'])
        return

    if args.nuke:
        confirm = input("This will delete ALL recordings. Type 'yes' to confirm: ")
        if confirm.strip().lower() == 'yes':
            nuke_directories()
        else:
            print("Aborted.")
        return

    # FIX: --run flag implements the selective component feature described in README
    if args.run:
        remove_gitkeep_files()
        run_components(args.run)
        return

    # Default: run everything
    remove_gitkeep_files()
    start_flask_server_in_tmux()
    print("Server started: http://127.0.0.1:8005")
    for script in ['redact.py', 'split.py', 'edit.py']:
        run_script(script, [])


if __name__ == "__main__":
    main()
