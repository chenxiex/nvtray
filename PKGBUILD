# Maintainer: anlorsp <anlor[at]anlor[dot]top>
pkgname=nvtray-git
pkgver=0.r2.868ca11
pkgrel=1
pkgdesc="Linux tray application for ejecting NVIDIA GPU from PCI bus"
arch=('x86_64' 'aarch64')
url="https://github.com/anlorsp/nvtray"
license=('GPL3')
depends=(
  'python'
  'python-pyudev'
  'python-gobject'
  'libappindicator'
  'polkit'
  'python-notify2'
)
source=("${pkgname}::git+https://github.com/anlorsp/nvtray.git")
sha256sums=('SKIP')

pkgver() {
  cd "${pkgname}"
  echo "0.r$(git rev-list --count HEAD).$(git rev-parse --short HEAD)"
}

package() {
  cd "${pkgname}"
  install -Dm755 nvtray.py "${pkgdir}/usr/lib/nvtray/nvtray"

  install -Dm755 nvtray_eject_helper.py "${pkgdir}/usr/lib/nvtray/nvtray-eject-helper"
  install -Dm644 i18n.py "${pkgdir}/usr/lib/nvtray/i18n.py"
  install -Dm644 io.github.anlorsp.nvtray.policy "${pkgdir}/usr/share/polkit-1/actions/io.github.anlorsp.nvtray.policy"
  install -Dm644 nvtray.service "${pkgdir}/usr/lib/systemd/user/nvtray.service"

  # Compile and install locale files
  for po_file in locales/*/LC_MESSAGES/nvtray.po; do
    lang=$(basename "$(dirname "$(dirname "$po_file")")")
    mo_dir="${pkgdir}/usr/share/locale/$lang/LC_MESSAGES"
    mkdir -p "$mo_dir"
    msgfmt "$po_file" -o "$mo_dir/nvtray.mo"
  done

  mkdir -p "${pkgdir}/usr/bin"
  ln -s /usr/lib/nvtray/nvtray "${pkgdir}/usr/bin/nvtray"
}
