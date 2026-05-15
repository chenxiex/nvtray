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
    echo "  /usr/share/polkit-1/actions/io.github.chenxiex.nvtray.policy"
    echo "  /usr/lib/systemd/user/nvtray.service"
    echo ""
    echo "To enable autostart:"
    echo "  systemctl --user enable --now nvtray.service"
}

remove_nvtray() {
    python - <<'PY'
import importlib.metadata
import shutil
import sysconfig
from pathlib import Path

try:
    dist = importlib.metadata.distribution("nvtray")
except importlib.metadata.PackageNotFoundError:
    dist = None

paths = set()
remove_trees = set()

if dist is not None:
    remove_trees.add(Path(dist.locate_file("nvtray")))
    dist_info_path = getattr(dist, "_path", None)
    if dist_info_path is not None:
        remove_trees.add(Path(dist_info_path))

    for entry in dist.files or []:
        path = Path(dist.locate_file(entry))
        if path.exists() or path.is_symlink():
            paths.add(path)

# Also handle stale files left after an interrupted or older script install.
for scheme_name in ("purelib", "platlib"):
    scheme_path = sysconfig.get_paths().get(scheme_name)
    if scheme_path is None:
        continue
    site_path = Path(scheme_path)
    if not site_path.exists():
        continue
    remove_trees.add(site_path / "nvtray")
    remove_trees.update(site_path.glob("nvtray-*.dist-info"))

for site_path in Path("/usr/lib").glob("python*/site-packages"):
    if not site_path.exists():
        continue
    remove_trees.add(site_path / "nvtray")
    remove_trees.update(site_path.glob("nvtray-*.dist-info"))

for path in sorted(paths, key=lambda item: len(item.parts), reverse=True):
    if path.is_dir() and not path.is_symlink():
        continue
    path.unlink(missing_ok=True)

for path in sorted(remove_trees, key=lambda item: len(item.parts), reverse=True):
    if path.exists() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.is_symlink():
        path.unlink()

for path in sorted({p.parent for p in paths} | {p.parent for p in remove_trees}, key=lambda item: len(item.parts), reverse=True):
    try:
        path.rmdir()
    except OSError:
        pass

for script in ("/usr/bin/nvtray", "/usr/bin/nvtray-eject-helper"):
    try:
        Path(script).unlink()
    except FileNotFoundError:
        pass

print("Removed nvtray Python distribution files.")
PY

    remove_legacy_script_install

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
