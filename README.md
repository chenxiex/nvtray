# nvtray

Linux tray application that detects NVIDIA PCI devices and provides an "Eject NVIDIA GPU" menu item.

[中文说明](README.zh-CN.md)

## Features

- Automatically detects NVIDIA PCI devices (vendor ID: `0x10de`)
- Only shows display controllers (PCI class `0x03`), filters out audio devices
- Tray icon is only shown when an NVIDIA device is present
- Tray icon is automatically hidden after the NVIDIA device is removed
- Menu item to eject (unbind + remove) individual PCI devices
- **Checks for processes using the GPU before ejecting** — refuses to eject and lists offending processes if any are found
- Authorizes privileged operations via `pkexec` + `polkit`

## Dependencies

- Python 3
- `python3-gi`
- `python3-pyudev`
- `gir1.2-ayatanaappindicator3-0.1` or `gir1.2-appindicator3-0.1`
- `policykit-1`
- `python3-notify2`
- `gettext` (for locale file compilation, build-time only)

Arch Linux: use the provided PKGBUILD.

## Installation

```bash
cd /path/to/nvtray
sudo ./install.sh
```

Arch Linux: use the provided PKGBUILD.

## Usage

Run manually:

```bash
nvtray
```

Enable autostart (recommended):

```bash
systemctl --user enable --now nvtray.service
```

Disable autostart:

```bash
systemctl --user disable --now nvtray.service
```

## Hook Commands

You can run custom bash commands for these events:

- `gpu_added`: after an NVIDIA display controller is detected by udev
- `before_eject`: before `pkexec nvtray-eject-helper <pci_id>` is executed
- `after_eject`: after eject command finishes (success or failure)

Configuration file path follows the XDG Base Directory spec:

- `$XDG_CONFIG_HOME/nvtray/config.ini`
- If `XDG_CONFIG_HOME` is unset: `~/.config/nvtray/config.ini`

Example config:

```ini
[hooks]
gpu_added = /home/user/.local/bin/nvidia-gpu-added.sh
before_eject = logger -t nvtray "about to eject $NVTRAY_PCI_ID" && /home/user/.local/bin/check-safe.sh
after_eject = [ "$NVTRAY_EJECT_SUCCESS" = "1" ] && notify-send "GPU ejected" "$NVTRAY_PCI_ID"

[eject]
unload_modules = false
```

Each hook receives these environment variables:

- `NVTRAY_EVENT`: `gpu_added`, `before_eject`, or `after_eject`
- `NVTRAY_PCI_ID`: PCI ID such as `0000:01:00.0`
- `NVTRAY_EJECT_SUCCESS`: only for `after_eject`, value is `1` or `0`

Notes:

- Hook values are executed as `bash -lc "<your command>"`.
- Script paths are still supported because they are valid bash commands.
- `before_eject` is blocking. If it exits non-zero, GPU eject is aborted.
- `gpu_added` and `after_eject` run asynchronously.

Eject options:

- `unload_modules`: when set to `true`, the helper attempts to unload NVIDIA kernel modules after removing the PCI device. This is disabled by default.

## Notes

- The helper only accepts well-formed PCI IDs and verifies that the device vendor is NVIDIA.
- **GPU usage is checked before ejecting**:
  - Uses `fuser` to detect processes with open `/dev/nvidia*` device files
  - If any processes are found, ejection is refused and their names and PIDs are shown
- **Eject procedure**:
  - Writes to the PCI device's `remove` sysfs interface to remove the device from the bus
  - Optionally unloads NVIDIA kernel modules (`nvidia_uvm`, `nvidia_drm`, `nvidia_modeset`, `nvidia`) when `eject.unload_modules = true`
- The default polkit policy requires administrator authentication (cached for active sessions).

## Localization

Translations are stored as gettext `.po` files under `locales/`. Compiled `.mo` files are included in the repository.

To add a new language:
1. Create `locales/<lang>/LC_MESSAGES/nvtray.po` based on the existing `zh_CN` file
2. Compile: `msgfmt locales/<lang>/LC_MESSAGES/nvtray.po -o locales/<lang>/LC_MESSAGES/nvtray.mo`
3. Update `install.sh` and `PKGBUILD` to install the new locale file
