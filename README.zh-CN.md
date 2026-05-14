# nvtray

Linux 托盘程序：检测 NVIDIA PCI 设备并提供“弹出 NVIDIA GPU”菜单项。
[English](README.md)
## 功能

- 自动检测 NVIDIA PCI 设备（厂商 ID: `0x10de`）
- 仅显示显示控制器设备（PCI class 0x03），过滤音频设备
- 仅在检测到 NVIDIA 设备时显示托盘图标
- NVIDIA 设备移除后自动隐藏托盘图标
- 菜单可对单个 PCI 设备执行弹出（`unbind` + `remove`）
- **弹出前自动检测占用 GPU 的进程**，如有进程使用则拒绝弹出并显示进程列表
- 通过 `pkexec` + `polkit` 获取授权

## 依赖

- Python 3
- `python3-gi`
- `python3-pyudev`
- `gir1.2-ayatanaappindicator3-0.1` 或 `gir1.2-appindicator3-0.1`
- `policykit-1`
- `python3-notify2`
- `gettext`

Arch Linux 可直接使用 PKGBUILD。

## 安装

```bash
cd /path/to/nvtray
sudo ./install.sh
```

Arch Linux 可直接使用 PKGBUILD。

## 运行

手动运行：

```bash
nvtray
```

启用开机自启动（推荐）：

```bash
systemctl --user enable --now nvtray.service
```

停止并禁用自启动：

```bash
systemctl --user disable --now nvtray.service
```

## Hook 命令

可在以下事件执行自定义 bash 命令：

- `gpu_added`：udev 检测到 NVIDIA 显示控制器后
- `before_eject`：执行 `pkexec nvtray-eject-helper <pci_id>` 前
- `after_eject`：弹出命令结束后（无论成功或失败）

配置文件路径遵循 XDG Base Directory 规范：

- `$XDG_CONFIG_HOME/nvtray/config.ini`
- 若未设置 `XDG_CONFIG_HOME`：`~/.config/nvtray/config.ini`

示例配置：

```ini
[hooks]
gpu_added = /home/user/.local/bin/nvidia-gpu-added.sh
before_eject = logger -t nvtray "about to eject $NVTRAY_PCI_ID" && /home/user/.local/bin/check-safe.sh
after_eject = [ "$NVTRAY_EJECT_SUCCESS" = "1" ] && notify-send "GPU ejected" "$NVTRAY_PCI_ID"
```

每个 Hook 会收到以下环境变量：

- `NVTRAY_EVENT`：`gpu_added`、`before_eject` 或 `after_eject`
- `NVTRAY_PCI_ID`：例如 `0000:01:00.0`
- `NVTRAY_EJECT_SUCCESS`：仅 `after_eject` 有，值为 `1` 或 `0`

说明：

- Hook 配置内容会按 `bash -lc "<你的命令>"` 执行。
- 直接写脚本路径仍然可用，因为脚本路径本身就是合法 bash 命令。
- `before_eject` 为阻塞执行，若返回非 0 则中止弹出。
- `gpu_added` 和 `after_eject` 为异步执行。

## 说明

- helper 只允许处理格式正确的 PCI ID，并校验设备厂商必须是 NVIDIA。
- **弹出前会检查是否有进程正在使用 GPU**：
  - 使用 `fuser` 检测打开 `/dev/nvidia*` 设备的进程
  - 如检测到进程占用，将拒绝弹出并显示进程名称和 PID
- **弹出流程**：
  - 直接写入 PCI 设备的 `remove` 接口移除设备
  - 尝试卸载 NVIDIA 内核模块（nvidia_uvm, nvidia_drm, nvidia_modeset, nvidia）
- 默认 polkit 策略为管理员认证（活跃会话可缓存认证）。
