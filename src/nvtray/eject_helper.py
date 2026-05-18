#!/usr/bin/env python3
import argparse
import glob
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Set, Tuple

try:
    from .i18n import _
except ImportError:
    from i18n import _

PCI_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
NVIDIA_MINOR_PATTERN = re.compile(r"^\s*Minor Number\s*:\s*(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class DeviceHandle:
    pid: int
    name: str
    path: str


@dataclass(frozen=True)
class EjectOptions:
    pci_id: str
    unload_modules: bool
    wait_seconds: float
    remove_related_functions: bool


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as file:
        return file.read().strip()


def write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)


def validate_pci_id(pci_id: str) -> str:
    if not PCI_ID_PATTERN.match(pci_id):
        fail(f"Invalid PCI ID format: {pci_id}")
    return pci_id.lower()


def ensure_nvidia_device(pci_id: str) -> None:
    device_dir = f"/sys/bus/pci/devices/{pci_id}"
    vendor_file = os.path.join(device_dir, "vendor")
    if not os.path.exists(device_dir):
        fail(f"PCI device not found: {pci_id}")
    if not os.path.exists(vendor_file):
        fail(f"Vendor file missing for device: {pci_id}")
    vendor = read_file(vendor_file)
    if vendor.lower() != "0x10de":
        fail(f"Device is not NVIDIA (vendor={vendor}): {pci_id}")


def device_dir(pci_id: str) -> str:
    return f"/sys/bus/pci/devices/{pci_id}"


def pci_slot_prefix(pci_id: str) -> str:
    return pci_id.rsplit(".", 1)[0]


def is_nvidia_pci_function(pci_id: str) -> bool:
    vendor_path = os.path.join(device_dir(pci_id), "vendor")
    try:
        return read_file(vendor_path).lower() == "0x10de"
    except OSError:
        return False


def is_display_pci_function(pci_id: str) -> bool:
    class_path = os.path.join(device_dir(pci_id), "class")
    try:
        return read_file(class_path).lower().startswith("0x03")
    except OSError:
        return False


def related_nvidia_functions(pci_id: str, include_related: bool) -> List[str]:
    if not include_related:
        return [pci_id]

    base = "/sys/bus/pci/devices"
    prefix = pci_slot_prefix(pci_id)
    functions = []
    for function_id in sorted(glob.glob(os.path.join(base, f"{prefix}.*"))):
        candidate = os.path.basename(function_id).lower()
        if PCI_ID_PATTERN.match(candidate) and is_nvidia_pci_function(candidate):
            functions.append(candidate)
    return functions or [pci_id]


def drm_device_names_for_pci(pci_id: str) -> List[str]:
    drm_dir = os.path.join(device_dir(pci_id), "drm")
    if not os.path.isdir(drm_dir):
        return []
    return sorted(
        name
        for name in os.listdir(drm_dir)
        if name.startswith("card") or name.startswith("renderD")
    )


def drm_paths_for_pci(pci_id: str) -> List[str]:
    return [os.path.join("/dev/dri", name) for name in drm_device_names_for_pci(pci_id)]


def drm_sysfs_paths_for_pci(pci_id: str) -> List[str]:
    return [
        os.path.realpath(os.path.join(device_dir(pci_id), "drm", name))
        for name in drm_device_names_for_pci(pci_id)
    ]


def nvidia_device_paths_for_pci(pci_id: str) -> List[str]:
    paths = []
    info_path = f"/proc/driver/nvidia/gpus/{pci_id}/information"
    try:
        info = read_file(info_path)
    except OSError:
        info = ""

    match = NVIDIA_MINOR_PATTERN.search(info)
    if match:
        paths.append(f"/dev/nvidia{match.group(1)}")

    # These global entry points cannot be attributed to one GPU on multi-GPU
    # systems. Include them only when they are the best available signal.
    known_gpus = glob.glob("/proc/driver/nvidia/gpus/*")
    if len(known_gpus) <= 1 or not match:
        paths.extend(["/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools"])
    return paths


def existing_char_devices(paths: Iterable[str]) -> Dict[int, str]:
    devices = {}
    for path in paths:
        try:
            st = os.stat(path)
        except OSError:
            continue
        if stat.S_ISCHR(st.st_mode):
            devices[st.st_rdev] = path
    return devices


def process_name(pid: int) -> str:
    try:
        return read_file(f"/proc/{pid}/comm")
    except OSError:
        return f"PID {pid}"


def check_open_device_handles(paths: Iterable[str]) -> List[DeviceHandle]:
    watched_devices = existing_char_devices(paths)
    if not watched_devices:
        return []

    handles: Set[DeviceHandle] = set()
    for proc_entry in os.listdir("/proc"):
        if not proc_entry.isdigit():
            continue
        pid = int(proc_entry)
        fd_dir = f"/proc/{pid}/fd"
        try:
            fd_names = os.listdir(fd_dir)
        except (FileNotFoundError, PermissionError):
            continue
        name = process_name(pid)
        for fd_name in fd_names:
            fd_path = os.path.join(fd_dir, fd_name)
            try:
                st = os.stat(fd_path)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if stat.S_ISCHR(st.st_mode) and st.st_rdev in watched_devices:
                handles.add(
                    DeviceHandle(pid=pid, name=name, path=watched_devices[st.st_rdev])
                )

    return sorted(handles, key=lambda item: (item.path, item.pid, item.name))


def check_open_regular_paths(paths: Iterable[str]) -> List[DeviceHandle]:
    watched_paths = {os.path.realpath(path): path for path in paths if os.path.exists(path)}
    if not watched_paths:
        return []

    handles: Set[DeviceHandle] = set()
    for proc_entry in os.listdir("/proc"):
        if not proc_entry.isdigit():
            continue
        pid = int(proc_entry)
        fd_dir = f"/proc/{pid}/fd"
        try:
            fd_names = os.listdir(fd_dir)
        except (FileNotFoundError, PermissionError):
            continue
        name = process_name(pid)
        for fd_name in fd_names:
            fd_path = os.path.join(fd_dir, fd_name)
            try:
                target = os.readlink(fd_path)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            if target.endswith(" (deleted)"):
                target = target[: -len(" (deleted)")]
            real_target = os.path.realpath(target)
            for watched_real, watched_label in watched_paths.items():
                if real_target == watched_real or real_target.startswith(
                    watched_real + os.sep
                ):
                    handles.add(DeviceHandle(pid=pid, name=name, path=watched_label))
                    break

    return sorted(handles, key=lambda item: (item.path, item.pid, item.name))


def check_nvidia_processes(pci_id: str) -> List[DeviceHandle]:
    """Check for processes that keep the target GPU device nodes open."""
    watched_paths = drm_paths_for_pci(pci_id) + nvidia_device_paths_for_pci(pci_id)
    handles = check_open_device_handles(watched_paths)
    handles.extend(check_open_regular_paths(drm_sysfs_paths_for_pci(pci_id)))
    return sorted(set(handles), key=lambda item: (item.path, item.pid, item.name))


def format_device_handles(handles: List[DeviceHandle]) -> str:
    entries = [f"{handle.name} (PID {handle.pid}, {handle.path})" for handle in handles[:8]]
    if len(handles) > 8:
        entries.append(_("and %d more process(es)") % (len(handles) - 8))
    return ", ".join(entries)


def set_power_control_on(pci_id: str) -> None:
    power_control_path = os.path.join(device_dir(pci_id), "power", "control")
    if os.path.exists(power_control_path):
        try:
            write_file(power_control_path, "on\n")
        except OSError as exc:
            fail(f"Failed to set runtime power control to on for {pci_id}: {exc}")


def wait_for_removal(
    pci_ids: List[str],
    drm_names: List[str],
    wait_seconds: float,
) -> Tuple[List[str], List[str]]:
    deadline = time.monotonic() + wait_seconds
    missing_pci: List[str] = []
    missing_drm: List[str] = []

    while True:
        remaining_pci = [pci_id for pci_id in pci_ids if os.path.exists(device_dir(pci_id))]
        remaining_drm = [
            name
            for name in drm_names
            if os.path.exists(os.path.join("/sys/class/drm", name))
            or os.path.exists(os.path.join("/dev/dri", name))
        ]
        if not remaining_pci and not remaining_drm:
            return [], []
        if time.monotonic() >= deadline:
            missing_pci = remaining_pci
            missing_drm = remaining_drm
            break
        time.sleep(0.2)

    return missing_pci, missing_drm


def remove_pci_device(pci_id: str) -> None:
    """Remove PCI device from the bus."""
    remove_path = f"/sys/bus/pci/devices/{pci_id}/remove"
    if os.path.exists(remove_path):
        write_file(remove_path, "1\n")
    else:
        fail(f"Remove interface not found: {remove_path}")


def unload_nvidia_modules() -> List[str]:
    """Attempt to unload NVIDIA kernel modules. Returns list of failed modules (empty if all succeeded)."""
    # Modules to unload in order (dependent modules first)
    modules = [
        "nvidia_uvm",
        "nvidia_drm",
        "nvidia_modeset",
        "nvidia",
    ]

    failed_modules = []
    for module in modules:
        try:
            result = subprocess.run(
                ["modprobe", "-r", module],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                failed_modules.append(module)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            failed_modules.append(module)

    return failed_modules


def parse_args() -> EjectOptions:
    parser = argparse.ArgumentParser(
        prog="nvtray-eject-helper",
        description="Eject an NVIDIA GPU from the PCI bus.",
    )
    parser.add_argument("--unload-modules", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=5.0)
    parser.add_argument(
        "--keep-related-functions",
        action="store_true",
        help="Only remove the requested PCI function instead of the whole NVIDIA slot.",
    )
    parser.add_argument("pci_id")
    args = parser.parse_args()

    if args.wait_seconds < 0:
        fail("--wait-seconds must be greater than or equal to 0")

    return EjectOptions(
        pci_id=validate_pci_id(args.pci_id),
        unload_modules=args.unload_modules,
        wait_seconds=args.wait_seconds,
        remove_related_functions=not args.keep_related_functions,
    )


def main() -> None:
    options = parse_args()

    if os.geteuid() != 0:
        fail("This helper must run as root (use pkexec).")

    ensure_nvidia_device(options.pci_id)

    pci_ids = related_nvidia_functions(options.pci_id, options.remove_related_functions)
    drm_names = drm_device_names_for_pci(options.pci_id)

    # Check for running processes using the target GPU before removing it.
    processes = check_nvidia_processes(options.pci_id)
    if processes:
        fail(
            _("Cannot eject GPU: the following processes are using the NVIDIA card: %s")
            % format_device_handles(processes)
        )

    set_power_control_on(options.pci_id)

    display_functions = [pci_id for pci_id in pci_ids if is_display_pci_function(pci_id)]
    other_functions = [pci_id for pci_id in pci_ids if pci_id not in display_functions]
    for function_id in other_functions + display_functions:
        if os.path.exists(device_dir(function_id)):
            remove_pci_device(function_id)

    remaining_pci, remaining_drm = wait_for_removal(pci_ids, drm_names, options.wait_seconds)
    if remaining_pci or remaining_drm:
        parts = []
        if remaining_pci:
            parts.append("PCI functions still present: " + ", ".join(remaining_pci))
        if remaining_drm:
            parts.append("DRM nodes still present: " + ", ".join(remaining_drm))
        fail(_("NVIDIA GPU (%s) removal incomplete: %s") % (options.pci_id, "; ".join(parts)))

    if not options.unload_modules:
        print(_("NVIDIA GPU (%s) removed successfully") % options.pci_id)
        return

    # Attempt to unload NVIDIA kernel modules and check results
    failed_modules = unload_nvidia_modules()

    if failed_modules:
        failed_list = ", ".join(failed_modules)
        print(_("Warning: the following modules failed to unload (may be in use): %s") % failed_list)
        print(
            _("NVIDIA GPU (%s) removed, but some kernel modules failed to unload. Reboot to fully unload.")
            % options.pci_id
        )
    else:
        print(_("NVIDIA GPU (%s) ejected and all kernel modules unloaded successfully") % options.pci_id)


if __name__ == "__main__":
    main()
