#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
    echo "Usage: $0 [-r]"
    echo "  -r    remove installed files"
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Error: required command not found: $1" >&2
        exit 1
    fi
}

require_python_module() {
    if ! python -c "import $1" >/dev/null 2>&1; then
        echo "Error: required Python module not found: $1" >&2
        exit 1
    fi
}

compile_locales() {
    shopt -s nullglob
    for po_file in "$ROOT_DIR"/src/nvtray/locales/*/LC_MESSAGES/nvtray.po; do
        msgfmt "$po_file" -o "${po_file%.po}.mo"
    done
}

build_wheel() {
    rm -rf "$ROOT_DIR/dist"
    (cd "$ROOT_DIR" && python -m build --wheel --no-isolation)
}

remove_legacy_script_install() {
    if [[ -L /usr/bin/nvtray && "$(readlink /usr/bin/nvtray)" == "/usr/lib/nvtray/nvtray" ]]; then
        rm -f /usr/bin/nvtray
    fi

    rm -f /usr/lib/nvtray/nvtray
    rm -f /usr/lib/nvtray/nvtray-eject-helper
    rm -f /usr/lib/nvtray/i18n.py
    rmdir --ignore-fail-on-non-empty /usr/lib/nvtray 2>/dev/null || true
}

install_nvtray() {
    require_command msgfmt
    require_command python
    require_python_module build
    require_python_module installer
    require_python_module setuptools
    require_python_module wheel

    compile_locales
    build_wheel
    remove_legacy_script_install
    python -m installer --destdir / --prefix /usr --overwrite-existing "$ROOT_DIR"/dist/nvtray-*.whl

    echo "Installed nvtray from wheel."
    echo "Installed commands:"
    echo "  /usr/bin/nvtray"
    echo "  /usr/bin/nvtray-eject-helper"
    echo "Installed integration files:"
    echo "  /usr/share/polkit-1/actions/io.github.anlorsp.nvtray.policy"
    echo "  /usr/lib/systemd/user/nvtray.service"
    echo ""
    echo "To enable autostart:"
    echo "  systemctl --user enable --now nvtray.service"
}

remove_nvtray() {
    python - <<'PY'
import importlib.metadata
import os
from pathlib import Path

try:
    dist = importlib.metadata.distribution("nvtray")
except importlib.metadata.PackageNotFoundError:
    raise SystemExit("nvtray is not installed as a Python distribution.")

paths = []
for entry in dist.files or []:
    path = Path(dist.locate_file(entry))
    if path.exists() or path.is_symlink():
        paths.append(path)

for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
    if path.is_dir() and not path.is_symlink():
        continue
    path.unlink(missing_ok=True)

for path in sorted({p.parent for p in paths}, key=lambda item: len(item.parts), reverse=True):
    try:
        path.rmdir()
    except OSError:
        pass

for script in ("/usr/bin/nvtray", "/usr/bin/nvtray-eject-helper"):
    try:
        os.unlink(script)
    except FileNotFoundError:
        pass

print("Removed nvtray Python distribution files.")
PY

    echo ""
    echo "If autostart was enabled for the current user, you can disable it with:"
    echo "  systemctl --user disable --now nvtray.service"
}

remove=false

while getopts ":rh" opt; do
    case "$opt" in
        r)
            remove=true
            ;;
        h)
            usage
            exit 0
            ;;
        \?)
            usage >&2
            exit 1
            ;;
    esac
done

shift $((OPTIND - 1))

if (( $# > 0 )); then
    usage >&2
    exit 1
fi

if [[ "$remove" == true ]]; then
    remove_nvtray
else
    install_nvtray
fi
