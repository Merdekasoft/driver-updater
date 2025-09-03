#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Driver Updater - Version 2.0
A GUI utility to check and install driver updates on Debian/Ubuntu-based systems.

Major Improvements:
- Uses QStackedWidget for better page management (Scan <-> Results).
- Uses detect.py/kerneldetection.py (ubuntu-drivers-common API) for driver detection.
- Update process (apt install) runs in a separate thread to keep the UI responsive.
- Added notification to reboot after important driver updates.
- Implements scan cancellation functionality.
- Clearer results display with version information.
- Improved error handling and user feedback.

Dependencies:
- PySide6: pip install pyside6
- Command line utilities: apt, pkexec, lshw (optional)
"""


import sys
import subprocess
import re
import os
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread, Slot
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen, QBrush, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QProgressBar,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QStyle,
    QMessageBox,
    QStackedWidget,
    QScrollArea,
)


class ScanWorker(QObject):
    """
    Runs driver scanning in a separate thread to keep the UI responsive.
    """
    progress = Signal(int, str)
    log = Signal(str)
    finished = Signal(list)
    error = Signal(str)
    _is_running = True

    def run_scan(self) -> None:
        """Performs the complete scanning process."""
        self._is_running = True
        try:
            # 1) Try to use detect.py module (ubuntu-drivers-common) if available
            try:
                import importlib
                detect_module = importlib.import_module('detect')
                # Ensure apt_pkg is available for detect.py
                import apt_pkg  # type: ignore
                apt_pkg.init()
                self.log.emit("Using detect.py module for driver detection.")
                self.run_detect_scan(detect_module)
                return
            except Exception as de:
                self.log.emit(f"detect.py could not be used: {de}\nContinuing with fallback method...")

            # 2) Fallback to apt scanning if detect.py is not available
            self.log.emit("Using 'apt' method as fallback.")
            self.run_apt_scan()
        except Exception as e:
            self.error.emit(f"Unexpected error occurred: {str(e)}")

    def run_detect_scan(self, detect_module) -> None:
        """Scanning based on detect.py module for Ubuntu systems."""
        if not self._is_running:
            return
        self.progress.emit(10, "Analyzing devices and driver candidates...")

        updates_found = []
        try:
            # Get package -> device info map from detect.py
            packages = detect_module.system_driver_packages()
        except Exception as e:
            self.log.emit(f"Failed to run system_driver_packages(): {e}")
            packages = {}

        if not self._is_running:
            return
        self.progress.emit(40, "Checking installed vs candidate package versions...")

        def _policy(pkg: str):
            proc = self._run_command(["apt-cache", "policy", pkg])
            installed = None
            candidate = None
            if proc.stdout:
                m_inst = re.search(r"Installed:\s*([^\n]+)", proc.stdout)
                m_cand = re.search(r"Candidate:\s*([^\n]+)", proc.stdout)
                installed = m_inst.group(1).strip() if m_inst else None
                candidate = m_cand.group(1).strip() if m_cand else None
            return installed, candidate

        # Build upgrade list for packages that are installed but have a newer candidate version
        for pkg, meta in packages.items():
            if not self._is_running:
                return
            self.log.emit(f"Checking package: {pkg}")
            installed, candidate = _policy(pkg)
            if installed and candidate and installed != candidate and installed != "(none)":
                updates_found.append({
                    "package": pkg,
                    "vendor": meta.get("vendor", ""),
                    "model": meta.get("model", ""),
                    "current_version": installed,
                    "new_version": candidate,
                    "type": "detect"
                })
                self.log.emit(f"-> Update found: {pkg} {installed} -> {candidate}")

        # If nothing to upgrade, suggest recommended packages for installation
        if not updates_found:
            self.progress.emit(70, "Looking for recommended packages to install...")
            try:
                import apt_pkg as _apt_pkg  # type: ignore
                _apt_pkg.init()
                cache = _apt_pkg.Cache(None)
                to_install = detect_module.get_desktop_package_list(cache)
            except Exception:
                to_install = []

            for pkg in to_install:
                installed, candidate = _policy(pkg)
                meta = packages.get(pkg, {})
                if installed == "(none)" or installed is None:
                    updates_found.append({
                        "package": pkg,
                        "vendor": meta.get("vendor", ""),
                        "model": meta.get("model", ""),
                        "current_version": installed or "(none)",
                        "new_version": candidate or "", 
                        "type": "detect-recommended"
                    })
                    self.log.emit(f"-> Recommended for installation: {pkg} (candidate {candidate or 'unknown'})")

        if not updates_found:
            self.log.emit("No updates or recommended installations found by detect.py.")

        self.progress.emit(100, "Scan complete")
        self.finished.emit(updates_found)

    # The ubuntu-drivers method is removed; detection now relies entirely on detect.py

    def run_apt_scan(self):
        """Fallback scanning method using `apt list --upgradable`."""
        if not self._is_running: return
        self.log.emit("Running 'pkexec apt update' to refresh package list...")
        self.progress.emit(10, "Refreshing package list...")

        update_proc = self._run_command(["pkexec", "apt", "update"], capture_output=True)
        if update_proc.returncode != 0:
            err_msg = update_proc.stderr.strip() if update_proc.stderr else "Permission denied or error occurred during 'apt update'."
            self.error.emit(f"Failed to update package list.\n{err_msg}")
            return

        if not self._is_running: return
        self.log.emit("Package list updated successfully.")
        self.progress.emit(40, "Checking upgradable packages...")
        upg_proc = self._run_command(["apt", "list", "--upgradable"], check=True)

        driver_regex = r'driver|firmware|linux-(?:modules|image|headers)|nvidia|amd|intel|vulkan|mesa'
        upgradable_lines = upg_proc.stdout.splitlines()[1:]  # Skip header
        updates_found = []

        for line in upgradable_lines:
            if not self._is_running: return
            if re.search(driver_regex, line, re.IGNORECASE):
                parts = line.split()
                package = parts[0].split('/')[0]
                new_version = parts[1]

                current_version = "N/A"
                installed_match = re.search(r'\[upgradable from: ([^\]]+)\]', line)
                if installed_match:
                    current_version = installed_match.group(1)

                updates_found.append({
                    "package": package,
                    "new_version": new_version,
                    "current_version": current_version,
                    "type": "apt"
                })
                self.log.emit(f"- Ditemukan potensi pembaruan: {package}")

        self.progress.emit(100, "Pemindaian selesai")
        self.finished.emit(updates_found)

    def _is_command_available(self, cmd: str) -> bool:
        """Memeriksa apakah sebuah perintah tersedia di PATH sistem."""
        return subprocess.run(["which", cmd], capture_output=True, text=True).returncode == 0

    def _run_command(self, cmd: list, check: bool = False, capture_output: bool = True) -> subprocess.CompletedProcess:
        """Wrapper untuk menjalankan subprocess dengan penanganan interupsi."""
        if not self._is_running:
            raise InterruptedError("Process interrupted by user.")
        return subprocess.run(cmd, capture_output=capture_output, text=True, check=check)

    @Slot()
    def stop(self):
        self.log.emit("Received signal to stop...")
        self._is_running = False


class UpdateWorker(QObject):
    """Menjalankan proses pembaruan di thread terpisah."""
    log = Signal(str)
    finished = Signal(bool, str) # success, message

    def run_update(self, packages: List[str]) -> None:
        """Runs `apt install`/upgrade for the selected packages.
        If any package is not installed, remove --only-upgrade so the package can be installed.
        """
        try:
            pkg_str = " ".join(packages)
            self.log.emit(f"Starting update for: {pkg_str}")

            def _is_installed(pkg: str) -> bool:
                try:
                    p = subprocess.run(["apt-cache", "policy", pkg], capture_output=True, text=True)
                    m = re.search(r"Installed:\s*([^\n]+)", p.stdout)
                    return bool(m and m.group(1).strip() and m.group(1).strip() != "(none)")
                except Exception:
                    return True  # konservatif: anggap terpasang agar tidak meng-install tanpa perlu

            any_not_installed = any(not _is_installed(p) for p in packages)

            base_cmd = ["pkexec", "apt", "install", "-y"]
            if not any_not_installed:
                base_cmd.insert(3, "--only-upgrade")  # pkexec apt install --only-upgrade -y

            cmd = base_cmd + packages

            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if proc.stdout:
                self.log.emit(proc.stdout)
            if proc.stderr:
                self.log.emit(proc.stderr)

            if proc.returncode == 0:
                self.finished.emit(True, "Update completed successfully.")
            else:
                self.finished.emit(False, f"Update failed with exit code {proc.returncode}.")
        except Exception as e:
            self.finished.emit(False, f"An error occurred during the update process: {e}")

class CircularScanButton(QWidget):
    """Tombol pemindaian melingkar dengan animasi dan label."""
    clicked = Signal()

    def __init__(self, diameter: int = 280, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._ring_color = QColor("#d35454")
        self._ring_glow = QColor("#ff6868")
        self._inner_color = QColor("#2b313a")
        self._text = "SCAN"
        self._text_color = QColor("#eaecef")
        self.setMinimumSize(self._diameter, self._diameter)
        self.setMaximumSize(self._diameter, self._diameter)
        self.setCursor(Qt.PointingHandCursor)

    def setText(self, text: str) -> None:
        self._text = text
        self.update()

    def setAccent(self, ring: QColor, glow: Optional[QColor] = None) -> None:
        self._ring_color = ring
        self._ring_glow = glow if glow else ring
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        d = min(rect.width(), rect.height())

        # Gambar glow
        p.setPen(QPen(self._ring_glow, 18, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(rect.center(), (d - 18) // 2, (d - 18) // 2)

        # Gambar ring utama
        p.setPen(QPen(self._ring_color, 10, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(rect.center(), (d - 36) // 2, (d - 36) // 2)

        # Gambar lingkaran dalam
        p.setBrush(self._inner_color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(rect.center(), (d - 72) // 2, (d - 72) // 2)

        # Gambar teks
        p.setPen(self._text_color)
        font = QFont("Arial", max(18, int(d * 0.1)), QFont.Bold)
        p.setFont(font)
        p.drawText(rect, Qt.AlignCenter, self._text)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class DriverUpdaterApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Driver Updater")
        self.setGeometry(100, 100, 960, 650)
        self.is_scanning = False
        self.current_scan_worker = None

        self.setup_styles()
        self.setup_ui()
        self.create_tray_icon()
        self.check_privileges()

    def setup_styles(self):
        self.setStyleSheet(
            """
            QMainWindow { background-color: #1b1f25; }
            QLabel { color: #eaecef; font-family: Arial; }
            QPushButton {
                background-color: #30363d; color: #eaecef; border: 1px solid #30363d;
                padding: 10px 12px; border-radius: 6px; font-weight: 600;
            }
            QPushButton:hover { background-color: #394049; }
            QPushButton:pressed { background-color: #2a3138; }
            QPushButton:disabled { background-color: #4c5258; color: #9a9fa5; }
            QProgressBar {
                border: 1px solid #30363d; border-radius: 5px; text-align: center;
                background-color: #22272e; color: #eaecef;
            }
            QProgressBar::chunk { background-color: #f39c12; }
            QTextEdit {
                background-color: #0d1117; color: #eaecef; border: 1px solid #30363d;
                border-radius: 8px; font-family: Monospace;
            }
            QFrame#sidebar { background-color: #161a20; border-radius: 0; }
            """
        )

    def setup_ui(self):
        root_widget = QWidget()
        self.setCentralWidget(root_widget)
        root_layout = QHBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(170)
        side_layout = QVBoxLayout(sidebar)
        title = QLabel("Driver Updater")
        title.setStyleSheet("font-size: 13pt; font-weight: 700; padding: 8px;")
        side_layout.addWidget(title)

        nav_scan = QPushButton("SCAN")
        nav_scan.setCheckable(True)
        nav_scan.setChecked(True)
        side_layout.addWidget(nav_scan)
        side_layout.addStretch()

        # Content Area with StackedWidget
        self.stacked_widget = QStackedWidget()
        self.scan_page = self.create_scan_page()
        self.results_page = QWidget() # Halaman hasil dibuat saat dibutuhkan

        self.stacked_widget.addWidget(self.scan_page)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.stacked_widget, 1)

    def create_scan_page(self) -> QWidget:
        """Menciptakan halaman pemindaian awal."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 18, 24, 18)
        self.banner_frame = QFrame()
        banner_layout = QHBoxLayout(self.banner_frame)
        self.banner_icon = QLabel("✔")
        self.banner_text = QLabel("All your drivers are up to date!")
        self.set_banner_style("ok")
        banner_layout.addWidget(self.banner_icon)
        banner_layout.addWidget(self.banner_text, 1)
        layout.addWidget(self.banner_frame)
        layout.setSpacing(18)
        self.banner_frame = QFrame()
        banner_layout = QHBoxLayout(self.banner_frame)
        self.banner_icon = QLabel("✔")
        self.banner_text = QLabel("All your drivers are up to date!")
        self.set_banner_style("ok")
        banner_layout.addWidget(self.banner_icon)
        banner_layout.addWidget(self.banner_text, 1)
        layout.addWidget(self.banner_frame)

        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setAlignment(Qt.AlignCenter)
        self.scan_circle = CircularScanButton(300)
        self.scan_circle.clicked.connect(self.toggle_scan)
        self.scan_circle.setAccent(QColor("#2ecc71"), QColor("#58d68d")) # Hijau untuk siap
        center_layout.addWidget(self.scan_circle)
        layout.addWidget(center_widget, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(160)
        self.log_text.append("System ready. Click SCAN to check for driver updates.")

        layout.addWidget(self.log_text)

        return page

    def set_banner_style(self, state: str):
        """Set banner style based on status (ok, scanning, error)."""
        styles = {
            "ok": ("✔", "#2ecc71", "All your drivers are up to date!"),
            "scanning": ("...", "#f39c12", "Scanning for drivers..."),
            "error": ("✖", "#e74c3c", "Scan failed. Check the log for details."),
            "updates_found": ("!", "#e67e22", "Driver updates found!")
        }
        icon, color, text = styles[state]
        self.banner_icon.setText(icon)
        self.banner_icon.setStyleSheet(f"color: {color}; font-size: 16pt; font-weight: 900;")
        self.banner_text.setText(text)

    def check_privileges(self):
        """Memeriksa apakah perintah yang diperlukan tersedia."""
        if not self._is_command_available("pkexec") or not self._is_command_available("apt"):
            QMessageBox.warning(
                self, "Dependensi Hilang",
                "Aplikasi ini memerlukan sistem berbasis Debian (dengan 'apt') dan 'pkexec' untuk tugas administratif. Beberapa fitur mungkin tidak berfungsi."
            )

    def _is_command_available(self, cmd: str) -> bool:
        return subprocess.run(["which", cmd], capture_output=True).returncode == 0

    def toggle_scan(self):
        if self.is_scanning:
            if self.scan_thread and self.scan_thread.isRunning():
                self.log_text.append("\n==> Mencoba membatalkan pemindaian...")
                self.current_scan_worker.stop()
                self.scan_thread.quit()
                self.scan_thread.wait(1000)
                self.reset_scan_state("Pemindaian dibatalkan oleh pengguna.")
        else:
            self.start_scan()

    def start_scan(self):
        self.is_scanning = True
        self.scan_circle.setText("Batal")
        self.progress_bar.setVisible(True)
        self.log_text.clear()

        self.scan_thread = QThread()
        self.current_scan_worker = ScanWorker()
        self.current_scan_worker.moveToThread(self.scan_thread)

        # Hubungkan sinyal
        self.current_scan_worker.progress.connect(self.update_progress)
        self.current_scan_worker.log.connect(self.log_text.append)
        self.current_scan_worker.finished.connect(self.finish_scan)
        self.current_scan_worker.error.connect(self.scan_error)
        self.scan_thread.started.connect(self.current_scan_worker.run_scan)

        self.scan_thread.start()

    def update_progress(self, value: int, text: str):
        self.progress_bar.setValue(value)
        self.progress_bar.setFormat(text)
        self.scan_circle.setText(f"{value}%")
        self.banner_text.setText(text)
        if not self.is_scanning: return
        self.scan_circle.setAccent(QColor("#f39c12"), QColor("#ffb64c"))
        self.set_banner_style("scanning")

    def finish_scan(self, found_updates: List[Dict]):
        self.log_text.append("\nScan completed.")
        self.reset_scan_state()
        self.create_results_page(found_updates)
        self.stacked_widget.setCurrentWidget(self.results_page)

    def scan_error(self, message: str):
        self.log_text.append(f"\nERROR: {message}")
        self.set_banner_style("error")
        self.scan_circle.setAccent(QColor("#e74c3c"), QColor("#ff6868")) # Merah untuk error
        self.reset_scan_state(message)

    def reset_scan_state(self, message: Optional[str] = None):
        self.is_scanning = False
        self.scan_circle.setText("SCAN")
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)

        if hasattr(self, 'scan_thread') and self.scan_thread.isRunning():
            self.scan_thread.quit()
            self.scan_thread.wait()

        if message:
            self.banner_text.setText(message)
        else:
            self.set_banner_style("ok")
            self.scan_circle.setAccent(QColor("#2ecc71"), QColor("#58d68d"))

    def create_results_page(self, found_updates: List[Dict]):
        self.results_page = QWidget()
        layout = QVBoxLayout(self.results_page)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(12)

        n_updates = len(found_updates)

        # Header
        header_layout = QHBoxLayout()
        summary_text = (f'<span style="font-size: 15pt;">Found <b style="color:#e67e22;">{n_updates} driver update(s)</b></span>'
                if n_updates else '<span style="font-size: 15pt; color:#2ecc71;">All drivers are up to date</span>')
        summary_label = QLabel(summary_text)
        header_layout.addWidget(summary_label, 1)

        rescan_btn = QPushButton("Rescan")
        rescan_btn.clicked.connect(self.go_to_scan_page)
        header_layout.addWidget(rescan_btn)

        if n_updates > 0:
            update_all_btn = QPushButton("Update All")
            update_all_btn.setStyleSheet("background-color: #2ecc71; color: white;")
            update_all_btn.clicked.connect(lambda: self.start_update([d['package'] for d in found_updates]))
            header_layout.addWidget(update_all_btn)
        layout.addLayout(header_layout)

        # Daftar yang dapat digulir
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("background: transparent; border: none;")
        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)

        if n_updates:
            for item in found_updates:
                list_layout.addWidget(self.create_driver_item_widget(item))
        else:
            list_layout.addWidget(QLabel("No driver updates are currently available."))

        list_layout.addStretch(1)
        scroll_area.setWidget(list_container)
        layout.addWidget(scroll_area, 1)

        # Tambahkan kembali log text
        layout.addWidget(self.log_text)

        # Hapus halaman hasil lama jika ada dan tambahkan yang baru
        if self.stacked_widget.count() > 1:
            old_results_page = self.stacked_widget.widget(1)
            self.stacked_widget.removeWidget(old_results_page)
            old_results_page.deleteLater()

        self.stacked_widget.addWidget(self.results_page)

    def create_driver_item_widget(self, item_data: Dict) -> QFrame:
        """Creates a widget for each driver item in the results list."""
        frame = QFrame()
        frame.setStyleSheet("background-color: #23272e; border-radius: 6px; padding: 8px;")
        layout = QHBoxLayout(frame)

        pkg = item_data['package']
        name_label = QLabel(f"<b>{pkg}</b><br><small>{item_data.get('model', 'System Package')}</small>")
        version_label = QLabel(f"Current: {item_data['current_version']}<br>Available: {item_data['new_version']}")

        update_btn = QPushButton("Update")
        update_btn.clicked.connect(lambda: self.start_update([pkg]))

        layout.addWidget(name_label, 3)
        layout.addWidget(version_label, 2)
        layout.addWidget(update_btn, 1)
        return frame

    def go_to_scan_page(self):
        """Return to the scan page."""
        self.log_text.clear()
        self.log_text.append("System ready. Click SCAN to start again.")
        self.reset_scan_state()
        self.stacked_widget.setCurrentWidget(self.scan_page)

    def start_update(self, packages: List[str]):
        """Memulai proses pembaruan di thread terpisah."""
        if not packages: return

        reply = QMessageBox.question(self, "Update Confirmation",
                         f"You are about to update {len(packages)} package(s). Continue?\n\n"
                         f"({', '.join(packages)})",
                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.No: return

        self.log_text.append(f"\n>>> Starting update process for: {', '.join(packages)}")

        # Nonaktifkan tombol untuk mencegah klik ganda
        for btn in self.results_page.findChildren(QPushButton):
            btn.setEnabled(False)

        self.update_thread = QThread()
        self.update_worker = UpdateWorker()
        self.update_worker.moveToThread(self.update_thread)

        self.update_worker.log.connect(self.log_text.append)
        self.update_worker.finished.connect(self.finish_update)
        self.update_thread.started.connect(lambda: self.update_worker.run_update(packages))

        self.update_thread.start()

    def finish_update(self, success: bool, message: str):
        """Called after the update thread finishes."""
        self.log_text.append(f">>> {message}")

        if success:
            QMessageBox.information(self, "Update Complete", message)
            # Check if reboot is recommended
            if any(k in message for k in ["linux-", "kernel", "nvidia"]):
                QMessageBox.information(self, "Reboot Recommended",
                                      "Some core drivers have been updated. It is recommended to reboot your computer to apply the changes.")
        else:
            QMessageBox.warning(self, "Update Failed", message)

        # Re-enable buttons on the results page
        for btn in self.results_page.findChildren(QPushButton):
            btn.setEnabled(True)

        self.update_thread.quit()
        self.update_thread.wait()

    # --- Manajemen Tray Icon dan Jendela ---
    def create_tray_icon(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")  # Ganti dengan icon.svg jika perlu
        app_icon = QIcon(icon_path) if os.path.exists(icon_path) else self.style().standardIcon(QStyle.SP_ComputerIcon)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon = QSystemTrayIcon(app_icon, self)
            menu = QMenu()
            menu.addAction("Open", self.showNormal)
            menu.addSeparator()
            menu.addAction("Exit", QApplication.quit)
            self.tray_icon.setContextMenu(menu)
            self.tray_icon.setIcon(app_icon)
            self.tray_icon.activated.connect(lambda r: self.showNormal() if r == QSystemTrayIcon.DoubleClick else None)
            self.tray_icon.show()
        else:
            # Fallback: tray tidak tersedia (misal: Wayland tanpa xembedsniproxy)
            QMessageBox.information(self, "Tray Not Available",
                "System tray is not available on your desktop environment.\n"
                "The application will keep running, but tray features are disabled.")

    def closeEvent(self, event):
        if hasattr(self, 'tray_icon') and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            self.tray_icon.showMessage("Driver Updater", "The application is still running in the background.", msecs=2000)
        else:
            QApplication.quit()


def main() -> None:
    app = QApplication(sys.argv)
    # Set global app icon (shows in taskbar/dock and some DEs)
    icon_path = os.path.join(os.path.dirname(__file__), "icon.png")  # Ganti dengan icon.svg jika perlu
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)
    else:
        app_icon = app.style().standardIcon(QStyle.SP_ComputerIcon)
        app.setWindowIcon(app_icon)
    app.setQuitOnLastWindowClosed(False)
    window = DriverUpdaterApp()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
