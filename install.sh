#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
shopt -s nullglob

usage() {
    echo "Usage: $0 [-r]"
    echo "  -r    remove installed files"
}

install_nvtray() {
    install -Dm755 "$ROOT_DIR/nvtray_eject_helper.py" /usr/lib/nvtray/nvtray-eject-helper
    install -Dm644 "$ROOT_DIR/i18n.py" /usr/lib/nvtray/i18n.py
    install -Dm644 "$ROOT_DIR/io.github.anlorsp.nvtray.policy" /usr/share/polkit-1/actions/io.github.anlorsp.nvtray.policy
    install -Dm755 "$ROOT_DIR/nvtray.py" /usr/lib/nvtray/nvtray
    ln -sf /usr/lib/nvtray/nvtray /usr/bin/nvtray
    install -Dm644 "$ROOT_DIR/nvtray.service" /usr/lib/systemd/user/nvtray.service

    # Install locale files
    for po_file in "$ROOT_DIR"/locales/*/LC_MESSAGES/nvtray.po; do
        lang=$(basename "$(dirname "$(dirname "$po_file")")")
        mo_dir="/usr/share/locale/$lang/LC_MESSAGES"
        mkdir -p "$mo_dir"
        msgfmt "$po_file" -o "$mo_dir/nvtray.mo"
    done

    echo "Installed:"
    echo "  /usr/bin/nvtray -> /usr/lib/nvtray/nvtray"
    echo "  /usr/lib/nvtray/nvtray-eject-helper"
    echo "  /usr/lib/nvtray/i18n.py"
    echo "  /usr/share/polkit-1/actions/io.github.anlorsp.nvtray.policy"
    echo "  /usr/lib/systemd/user/nvtray.service"
    echo "  locale files under /usr/share/locale/"
    echo ""
    echo "To enable autostart:"
    echo "  systemctl --user enable --now nvtray.service"
}

remove_nvtray() {
    if [[ -L /usr/bin/nvtray && "$(readlink /usr/bin/nvtray)" == "/usr/lib/nvtray/nvtray" ]]; then
        rm -f /usr/bin/nvtray
    fi
    rm -f /usr/lib/nvtray/nvtray
    rm -f /usr/lib/nvtray/nvtray-eject-helper
    rm -f /usr/lib/nvtray/i18n.py
    rm -f /usr/share/polkit-1/actions/io.github.anlorsp.nvtray.policy
    rm -f /usr/lib/systemd/user/nvtray.service

    for po_file in "$ROOT_DIR"/locales/*/LC_MESSAGES/nvtray.po; do
        lang=$(basename "$(dirname "$(dirname "$po_file")")")
        rm -f "/usr/share/locale/$lang/LC_MESSAGES/nvtray.mo"
        rmdir --ignore-fail-on-non-empty "/usr/share/locale/$lang/LC_MESSAGES" 2>/dev/null || true
        rmdir --ignore-fail-on-non-empty "/usr/share/locale/$lang" 2>/dev/null || true
    done

    rmdir --ignore-fail-on-non-empty /usr/lib/nvtray 2>/dev/null || true

    echo "Removed:"
    echo "  /usr/bin/nvtray"
    echo "  /usr/lib/nvtray/"
    echo "  /usr/share/polkit-1/actions/io.github.anlorsp.nvtray.policy"
    echo "  /usr/lib/systemd/user/nvtray.service"
    echo "  nvtray locale files under /usr/share/locale/"
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
