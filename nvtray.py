#!/usr/bin/env python3
import configparser
import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

import gi
import notify2
import pyudev

from i18n import _

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402


logger = logging.getLogger("nvtray")


@dataclass
class AppConfig:
    gpu_added: Optional[str]
    before_eject: Optional[str]
    after_eject: Optional[str]
    unload_modules: bool = False


def _get_config_path() -> str:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if not xdg_config_home:
        xdg_config_home = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(xdg_config_home, "nvtray", "config.ini")


def _load_config() -> AppConfig:
    config_path = _get_config_path()
    parser = configparser.ConfigParser()
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            parser.read_file(file)
    except FileNotFoundError:
        logger.info("Config not found, using defaults: %s", config_path)
        return AppConfig(gpu_added=None, before_eject=None, after_eject=None)
    except (OSError, configparser.Error) as exc:
        logger.warning("Failed to read config file %s: %s", config_path, exc)
        return AppConfig(gpu_added=None, before_eject=None, after_eject=None)

    hooks = parser["hooks"] if parser.has_section("hooks") else {}
    try:
        unload_modules = parser.getboolean("eject", "unload_modules", fallback=False)
    except ValueError as exc:
        logger.warning("Invalid eject.unload_modules value in %s: %s", config_path, exc)
        unload_modules = False

    loaded = AppConfig(
        gpu_added=hooks.get("gpu_added") or None,
        before_eject=hooks.get("before_eject") or None,
        after_eject=hooks.get("after_eject") or None,
        unload_modules=unload_modules,
    )
    logger.info(
        "Loaded config from %s (gpu_added=%s, before_eject=%s, after_eject=%s, unload_modules=%s)",
        config_path,
        bool(loaded.gpu_added),
        bool(loaded.before_eject),
        bool(loaded.after_eject),
        loaded.unload_modules,
    )
    return loaded


def list_nvidia_pci_ids() -> List[str]:
    base = "/sys/bus/pci/devices"
    result: List[str] = []
    if not os.path.isdir(base):
        return result

    for device_id in sorted(os.listdir(base)):
        vendor_path = os.path.join(base, device_id, "vendor")
        class_path = os.path.join(base, device_id, "class")
        if not os.path.exists(vendor_path):
            continue
        try:
            with open(vendor_path, "r", encoding="utf-8") as file:
                vendor = file.read().strip().lower()
        except OSError:
            continue
        if vendor != "0x10de":
            continue

        # Only include display controllers (class 0x03xxxx), not audio (0x04xxxx)
        try:
            with open(class_path, "r", encoding="utf-8") as file:
                device_class = file.read().strip().lower()
        except OSError:
            continue
        if device_class.startswith("0x03"):
            result.append(device_id)
    return result


class NvTrayApp:
    def __init__(self) -> None:
        notify2.init("nvtray")
        self.config = _load_config()
        
        self.indicator = self._create_indicator()
        self.context = pyudev.Context()
        self.monitor = pyudev.Monitor.from_netlink(self.context)
        self.monitor.filter_by(subsystem="pci")
        self.monitor.start()

        self.monitor_channel = GLib.IOChannel.unix_new(self.monitor.fileno())
        GLib.io_add_watch(
            self.monitor_channel,
            GLib.IO_IN,
            self._on_udev_event,
        )

        self.refresh_ui()

    def _run_hook(self, hook_command: str, env_extra: Dict[str, str], timeout: int = 60) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update(env_extra)
        logger.info(
            "Executing hook command event=%s command=%r timeout=%ss",
            env_extra.get("NVTRAY_EVENT", "unknown"),
            hook_command,
            timeout,
        )
        return subprocess.run(
            ["bash", "-lc", hook_command],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )

    def _run_hook_in_thread(self, hook_name: str, hook_command: Optional[str], env_extra: Dict[str, str]) -> None:
        if not hook_command:
            return

        def _worker() -> None:
            try:
                completed = self._run_hook(hook_command, env_extra)
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                logger.error(
                    "Async hook failed name=%s pci_id=%s error=%s",
                    hook_name,
                    env_extra.get("NVTRAY_PCI_ID", ""),
                    exc,
                )
                GLib.idle_add(
                    self._send_notification,
                    _("Hook execution failed"),
                    _("%s hook failed: %s") % (hook_name, str(exc)),
                    notify2.URGENCY_CRITICAL,
                )
                return

            if completed.returncode != 0:
                error = completed.stderr.strip() or completed.stdout.strip() or _("Unknown error")
                logger.error(
                    "Async hook exited non-zero name=%s pci_id=%s rc=%s stderr=%s",
                    hook_name,
                    env_extra.get("NVTRAY_PCI_ID", ""),
                    completed.returncode,
                    completed.stderr.strip(),
                )
                GLib.idle_add(
                    self._send_notification,
                    _("Hook execution failed"),
                    _("%s hook exited with error: %s") % (hook_name, error),
                    notify2.URGENCY_CRITICAL,
                )
                return

            logger.info(
                "Async hook completed name=%s pci_id=%s rc=0",
                hook_name,
                env_extra.get("NVTRAY_PCI_ID", ""),
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _run_before_eject_hook(self, pci_id: str) -> bool:
        if not self.config.before_eject:
            return True
        env_extra = {
            "NVTRAY_EVENT": "before_eject",
            "NVTRAY_PCI_ID": pci_id,
        }
        try:
            completed = self._run_hook(self.config.before_eject, env_extra)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            logger.error("before_eject hook failed pci_id=%s error=%s", pci_id, exc)
            self._send_notification(
                _("NVIDIA GPU operation failed"),
                _("before_eject hook failed: %s") % str(exc),
                notify2.URGENCY_CRITICAL,
            )
            return False

        if completed.returncode != 0:
            error = completed.stderr.strip() or completed.stdout.strip() or _("Unknown error")
            logger.error(
                "before_eject hook exited non-zero pci_id=%s rc=%s stderr=%s",
                pci_id,
                completed.returncode,
                completed.stderr.strip(),
            )
            self._send_notification(
                _("NVIDIA GPU operation failed"),
                _("before_eject hook exited with error: %s") % error,
                notify2.URGENCY_CRITICAL,
            )
            return False
        logger.info("before_eject hook completed pci_id=%s rc=0", pci_id)
        return True

    def _create_indicator(self):
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3

            indicator = AyatanaAppIndicator3.Indicator.new(
                "nvtray",
                "video-display",
                AyatanaAppIndicator3.IndicatorCategory.HARDWARE,
            )
            indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)
            self._indicator_mod = AyatanaAppIndicator3
            return indicator
        except (ImportError, ValueError):
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3

            indicator = AppIndicator3.Indicator.new(
                "nvtray",
                "video-display",
                AppIndicator3.IndicatorCategory.HARDWARE,
            )
            indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
            self._indicator_mod = AppIndicator3
            return indicator

    def _indicator_set_visible(self, visible: bool) -> None:
        status_enum = self._indicator_mod.IndicatorStatus
        self.indicator.set_status(status_enum.ACTIVE if visible else status_enum.PASSIVE)

    def _build_menu(self, pci_ids: List[str]) -> Gtk.Menu:
        menu = Gtk.Menu()

        if pci_ids:
            for pci_id in pci_ids:
                item = Gtk.MenuItem(label=_("Eject NVIDIA GPU (%s)") % pci_id)
                item.connect("activate", self._on_eject_clicked, pci_id)
                menu.append(item)
        else:
            item = Gtk.MenuItem(label=_("No NVIDIA GPU detected"))
            item.set_sensitive(False)
            menu.append(item)

        separator = Gtk.SeparatorMenuItem()
        menu.append(separator)

        quit_item = Gtk.MenuItem(label=_("Quit"))
        quit_item.connect("activate", self._on_quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _on_eject_clicked(self, _menu_item: Gtk.MenuItem, pci_id: str) -> None:
        threading.Thread(target=self._run_eject, args=(pci_id,), daemon=True).start()

    def _find_helper(self) -> Optional[str]:
        # 1. Search in PATH
        helper = shutil.which("nvtray-eject-helper")
        if helper:
            return helper

        # 2. Check common install locations
        common_paths = [
            "/usr/lib/nvtray/nvtray-eject-helper",
            "/usr/local/lib/nvtray/nvtray-eject-helper",
            "/usr/libexec/nvtray-eject-helper",
            "/usr/local/libexec/nvtray-eject-helper",
        ]
        for path in common_paths:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path

        # 3. Fall back to development version in script directory
        local_helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nvtray_eject_helper.py")
        if os.path.isfile(local_helper):
            return local_helper

        return None

    def _run_eject(self, pci_id: str) -> None:
        if not self._run_before_eject_hook(pci_id):
            return

        helper_path = self._find_helper()
        if not helper_path:
            self._send_notification(
                _("NVIDIA GPU operation failed"),
                _("Error: nvtray-eject-helper not found"),
                notify2.URGENCY_CRITICAL,
            )
            return

        cmd = ["pkexec", helper_path]
        if self.config.unload_modules:
            cmd.append("--unload-modules")
        cmd.append(pci_id)
        completed = subprocess.run(cmd, capture_output=True, text=True)
        eject_success = completed.returncode == 0
        if completed.returncode != 0:
            error = completed.stderr.strip() or completed.stdout.strip()
            self._send_notification(
                _("NVIDIA GPU operation failed"),
                error,
                notify2.URGENCY_CRITICAL,
            )
        else:
            # Show output messages from helper (warnings, success info, etc.)
            message = completed.stdout.strip()
            if message:
                self._send_notification(
                    _("NVIDIA GPU operation completed"),
                    message,
                    notify2.URGENCY_NORMAL,
                )

        self._run_hook_in_thread(
            "after_eject",
            self.config.after_eject,
            {
                "NVTRAY_EVENT": "after_eject",
                "NVTRAY_PCI_ID": pci_id,
                "NVTRAY_EJECT_SUCCESS": "1" if eject_success else "0",
            },
        )
        GLib.idle_add(self.refresh_ui)

    def _send_notification(self, title: str, body: str, urgency: int = notify2.URGENCY_NORMAL) -> None:
        """Send a system desktop notification."""
        try:
            notification = notify2.Notification(title, body, icon="video-display")
            notification.set_urgency(urgency)
            notification.timeout = 5000 if urgency == notify2.URGENCY_NORMAL else 10000
            notification.show()
        except Exception as e:
            logger.warning("Failed to send notification: %s", e)

    def _on_quit(self, _menu_item: Gtk.MenuItem) -> None:
        Gtk.main_quit()

    def _is_nvidia_display_device(self, device: pyudev.Device) -> bool:
        vendor_raw = device.attributes.get("vendor")
        class_raw = device.attributes.get("class")
        if vendor_raw is None or class_raw is None:
            return False

        vendor = vendor_raw.decode("utf-8", errors="ignore").strip().lower()
        device_class = class_raw.decode("utf-8", errors="ignore").strip().lower()
        return vendor == "0x10de" and device_class.startswith("0x03")

    def _on_udev_event(self, _source, condition) -> bool:
        if condition & GLib.IO_IN:
            while True:
                device = self.monitor.poll(timeout=0)
                if device is None:
                    break
                action = device.action
                if action == "add" and self._is_nvidia_display_device(device):
                    self._run_hook_in_thread(
                        "gpu_added",
                        self.config.gpu_added,
                        {
                            "NVTRAY_EVENT": "gpu_added",
                            "NVTRAY_PCI_ID": device.sys_name,
                        },
                    )
                if action in {"add", "remove", "change", "bind", "unbind"}:
                    self.refresh_ui()
        return True

    def refresh_ui(self) -> bool:
        pci_ids = list_nvidia_pci_ids()
        menu = self._build_menu(pci_ids)
        self.indicator.set_menu(menu)
        self._indicator_set_visible(bool(pci_ids))
        return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    NvTrayApp()
    Gtk.main()


if __name__ == "__main__":
    main()
