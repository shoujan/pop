from __future__ import annotations

import json
import logging
import secrets
import socket
import string
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QMessageBox, QPushButton,
    QPlainTextEdit, QVBoxLayout, QHBoxLayout, QWidget, QGroupBox
)

APP_DIR = Path(__file__).resolve().parent
LOG_PATH = APP_DIR / "audit.log"
logging.Formatter.converter = time.gmtime
logging.basicConfig(
    filename=LOG_PATH, level=logging.INFO,
    format="%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
)
log = logging.getLogger("android_lab")

state_lock = threading.Lock()
pairing_code = ""
pairing_expiry = 0.0
bearer_token = ""
paired_device: dict[str, Any] | None = None
last_seen = "Never"
latest_diagnostics: dict[str, Any] = {}
attempts: dict[str, list[float]] = {}

app = FastAPI(title="Personal Android Security Lab", docs_url=None, redoc_url=None)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit(event: str, **fields: Any) -> None:
    safe = {k: v for k, v in fields.items() if k.lower() not in {"token", "authorization", "password", "secret"}}
    log.info(json.dumps({"time": utc_now(), "event": event, **safe}, ensure_ascii=False))


class PairRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    device_name: str = Field(min_length=1, max_length=80)
    model: str = Field(default="Unknown", max_length=100)
    android_version: str = Field(default="Unknown", max_length=40)
    app_version: str = Field(default="1.0.0", max_length=40)


class DiagnosticRequest(BaseModel):
    battery_percent: int = Field(ge=0, le=100)
    battery_charging: bool
    android_version: str = Field(max_length=40)
    model: str = Field(max_length=100)
    app_version: str = Field(max_length=40)


def check_rate_limit(ip: str) -> None:
    now = time.time()
    bucket = [t for t in attempts.get(ip, []) if now - t < 60]
    if len(bucket) >= 8:
        raise HTTPException(status_code=429, detail="Too many pairing attempts. Wait one minute.")
    bucket.append(now)
    attempts[ip] = bucket


def require_auth(authorization: str | None) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Pair this device from the visible Android app.")
    supplied = authorization[7:]
    with state_lock:
        expected = bearer_token
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Session revoked or invalid. Pair again.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "personal-android-lab", "status": "ready", "time": utc_now()}


@app.post("/pair")
def pair(body: PairRequest, request: Request) -> dict[str, str]:
    global bearer_token, paired_device, last_seen, pairing_code, pairing_expiry
    check_rate_limit(request.client.host if request.client else "unknown")
    with state_lock:
        if time.time() > pairing_expiry or not pairing_code:
            audit("pair_rejected", reason="expired_or_disabled")
            raise HTTPException(status_code=403, detail="Pairing code expired. Generate a new code on Windows.")
        if not secrets.compare_digest(body.code, pairing_code):
            audit("pair_rejected", reason="invalid_code")
            raise HTTPException(status_code=403, detail="Incorrect pairing code.")
        if paired_device is not None:
            audit("pair_rejected", reason="device_already_paired")
            raise HTTPException(status_code=409, detail="A device is already paired. Revoke it on Windows first.")
        bearer_token = secrets.token_urlsafe(32)
        paired_device = {
            "name": body.device_name, "model": body.model,
            "android_version": body.android_version, "app_version": body.app_version,
            "paired_at": utc_now(), "ip": request.client.host if request.client else "unknown"
        }
        last_seen = utc_now()
        pairing_code = ""
        pairing_expiry = 0
        token = bearer_token
    audit("device_paired", device=body.device_name, model=body.model, ip=paired_device["ip"])
    return {"token": token, "message": "Paired. This app is now connected to your Windows lab."}


@app.post("/heartbeat")
def heartbeat(authorization: str | None = Header(default=None)) -> dict[str, str]:
    global last_seen
    require_auth(authorization)
    last_seen = utc_now()
    audit("heartbeat", device=paired_device.get("name", "unknown") if paired_device else "unknown")
    return {"status": "connected", "time": last_seen}


@app.post("/diagnostics")
def diagnostics(body: DiagnosticRequest, authorization: str | None = Header(default=None)) -> dict[str, str]:
    global latest_diagnostics, last_seen
    require_auth(authorization)
    latest_diagnostics = body.model_dump()
    last_seen = utc_now()
    audit("diagnostics_received", model=body.model, android_version=body.android_version,
          battery_percent=body.battery_percent, charging=body.battery_charging)
    return {"status": "recorded", "time": last_seen}


@app.post("/disconnect")
def disconnect(authorization: str | None = Header(default=None)) -> dict[str, str]:
    global bearer_token, paired_device, latest_diagnostics, last_seen
    require_auth(authorization)
    name = paired_device.get("name", "unknown") if paired_device else "unknown"
    with state_lock:
        bearer_token = ""
        paired_device = None
        latest_diagnostics = {}
        last_seen = "Never"
    audit("device_disconnected_by_client", device=name)
    return {"status": "disconnected"}


class ApiServer(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8765, log_level="warning", access_log=False))

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True


def local_ipv4_addresses() -> list[str]:
    found: set[str] = set()
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if not ip.startswith("127."):
                found.add(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 80))
        ip = s.getsockname()[0]
        if not ip.startswith("127."):
            found.add(ip)
        s.close()
    except OSError:
        pass
    return sorted(found) or ["127.0.0.1"]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Personal Android Security Lab")
        self.resize(780, 620)
        self.server = ApiServer()
        self.server.start()
        self.code_label = QLabel("No pairing code generated")
        self.code_label.setStyleSheet("font-size: 24px; font-weight: bold; letter-spacing: 4px;")
        self.expiry_label = QLabel("Pairing code expires after 5 minutes and works once.")
        self.address_label = QLabel("Windows addresses: " + ", ".join(local_ipv4_addresses()) + "\nAndroid port: 8765")
        self.status_label = QLabel("Device: not paired")
        self.last_seen_label = QLabel("Last check-in: Never")
        self.diagnostics_label = QLabel("Diagnostics: none received")
        self.audit_view = QPlainTextEdit()
        self.audit_view.setReadOnly(True)
        self.audit_view.setPlaceholderText("Audit events will appear here.")
        generate_btn = QPushButton("Generate pairing code")
        generate_btn.clicked.connect(self.generate_code)
        revoke_btn = QPushButton("Revoke paired device")
        revoke_btn.clicked.connect(self.revoke)
        refresh_btn = QPushButton("Refresh status")
        refresh_btn.clicked.connect(self.refresh)
        row = QHBoxLayout()
        row.addWidget(generate_btn)
        row.addWidget(revoke_btn)
        row.addWidget(refresh_btn)
        group = QGroupBox("Pairing")
        gl = QVBoxLayout(group)
        gl.addWidget(self.code_label)
        gl.addWidget(self.expiry_label)
        gl.addWidget(self.address_label)
        gl.addLayout(row)
        status_group = QGroupBox("Connected device")
        sl = QVBoxLayout(status_group)
        sl.addWidget(self.status_label)
        sl.addWidget(self.last_seen_label)
        sl.addWidget(self.diagnostics_label)
        layout = QVBoxLayout()
        layout.addWidget(group)
        layout.addWidget(status_group)
        layout.addWidget(QLabel("Audit log (also saved to audit.log)"))
        layout.addWidget(self.audit_view, 1)
        root = QWidget()
        root.setLayout(layout)
        self.setCentralWidget(root)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)
        self.refresh()

    def generate_code(self) -> None:
        global pairing_code, pairing_expiry
        alphabet = string.digits
        with state_lock:
            if paired_device is not None:
                QMessageBox.warning(self, "Device already paired", "Revoke the current device before pairing another.")
                return
            pairing_code = "".join(secrets.choice(alphabet) for _ in range(6))
            pairing_expiry = time.time() + 300
            code = pairing_code
        self.code_label.setText(code)
        self.expiry_label.setText("Valid for 5 minutes. Single use. Share only with your own test phone.")
        audit("pairing_code_generated", expires_in_seconds=300)
        self.refresh()

    def revoke(self) -> None:
        global bearer_token, paired_device, latest_diagnostics, last_seen, pairing_code, pairing_expiry
        with state_lock:
            old_name = paired_device.get("name", "unknown") if paired_device else "none"
            bearer_token = ""
            paired_device = None
            latest_diagnostics = {}
            last_seen = "Never"
            pairing_code = ""
            pairing_expiry = 0
        audit("device_revoked_from_windows", device=old_name)
        self.code_label.setText("No active pairing code")
        self.refresh()

    def refresh(self) -> None:
        global pairing_code, pairing_expiry
        with state_lock:
            device = dict(paired_device) if paired_device else None
            seen = last_seen
            diag = dict(latest_diagnostics)
            code = pairing_code
            expiry = pairing_expiry
        if code and time.time() >= expiry:
            with state_lock:
                pairing_code = ""
                pairing_expiry = 0
            self.code_label.setText("Pairing code expired")
        elif code:
            remaining = max(0, int(expiry - time.time()))
            self.code_label.setText(code)
            self.expiry_label.setText(f"Single-use code · expires in {remaining // 60}:{remaining % 60:02d}")
        self.status_label.setText("Device: not paired" if not device else f"Device: {device['name']} · {device['model']} · Android {device['android_version']} · IP {device['ip']}")
        self.last_seen_label.setText(f"Last check-in: {seen}")
        self.diagnostics_label.setText("Diagnostics: none received" if not diag else
            f"Diagnostics: battery {diag.get('battery_percent')}% · charging={diag.get('battery_charging')} · app {diag.get('app_version')}")
        try:
            lines = LOG_PATH.read_text(encoding="utf-8").splitlines()[-80:]
            current = "\n".join(lines)
            if self.audit_view.toPlainText() != current:
                self.audit_view.setPlainText(current)
                self.audit_view.verticalScrollBar().setValue(self.audit_view.verticalScrollBar().maximum())
        except OSError:
            pass

    def closeEvent(self, event: Any) -> None:
        self.server.stop()
        event.accept()


def main() -> None:
    app_qt = QApplication([])
    window = MainWindow()
    window.show()
    app_qt.exec()


if __name__ == "__main__":
    main()
