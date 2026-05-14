#!/usr/bin/env python3
import glob
import os
import re
import subprocess
import sys
from typing import List, Tuple

from i18n import _

PCI_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")


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


def check_nvidia_processes() -> List[Tuple[int, str]]:
    """Check for processes using NVIDIA GPU with fuser. Returns list of (pid, process_name) tuples."""
    processes = []

    nvidia_devices = glob.glob("/dev/nvidia[0-9]*")
    if not nvidia_devices:
        return processes

    try:
        result = subprocess.run(
            ["fuser"] + nvidia_devices,
            capture_output=True,
            text=True,
            timeout=5,
        )
        # fuser returns PIDs on stdout
        if result.stdout.strip():
            pids = []
            for pid_str in result.stdout.strip().split():
                try:
                    pid = int(pid_str.rstrip(":").strip())
                    pids.append(pid)
                except ValueError:
                    pass

            for pid in pids:
                try:
                    with open(f"/proc/{pid}/comm", "r") as f:
                        name = f.read().strip()
                        processes.append((pid, name))
                except (FileNotFoundError, PermissionError):
                    processes.append((pid, f"PID {pid}"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return processes


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


def parse_args() -> Tuple[str, bool]:
    unload_modules = False
    args = sys.argv[1:]

    if args and args[0] == "--unload-modules":
        unload_modules = True
        args = args[1:]

    if len(args) != 1:
        fail("Usage: nvtray_eject_helper.py [--unload-modules] <PCI_ID>")

    return validate_pci_id(args[0]), unload_modules


def main() -> None:
    if os.geteuid() != 0:
        fail("This helper must run as root (use pkexec).")

    pci_id, should_unload_modules = parse_args()
    ensure_nvidia_device(pci_id)
    
    # Check for running processes using the GPU
    processes = check_nvidia_processes()
    if processes:
        process_list = ", ".join([f"{name} (PID {pid})" for pid, name in processes[:5]])
        if len(processes) > 5:
            process_list += ", " + _("and %d more process(es)") % (len(processes) - 5)
        fail(_("Cannot eject GPU: the following processes are using the NVIDIA card: %s") % process_list)

    # Remove PCI device directly
    remove_pci_device(pci_id)

    if not should_unload_modules:
        print(_("NVIDIA GPU (%s) removed successfully") % pci_id)
        return

    # Attempt to unload NVIDIA kernel modules and check results
    failed_modules = unload_nvidia_modules()

    if failed_modules:
        failed_list = ", ".join(failed_modules)
        print(_("Warning: the following modules failed to unload (may be in use): %s") % failed_list)
        print(_("NVIDIA GPU (%s) removed, but some kernel modules failed to unload. Reboot to fully unload.") % pci_id)
    else:
        print(_("NVIDIA GPU (%s) ejected and all kernel modules unloaded successfully") % pci_id)


if __name__ == "__main__":
    main()
