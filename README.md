# Personal Android Security Lab

A local-network, one-device-at-a-time test application with a Windows/PySide6 dashboard and a visible Android companion app. It supports one-time pairing, authenticated check-ins, explicitly requested basic diagnostics, revocation, and an audit log. It does **not** execute remote commands, hide itself, persist after uninstall, access private user data, or provide remote shell/file/camera/microphone access.

## Security boundary

This starter uses plain HTTP on port 8765 for a **trusted, isolated home/lab Wi-Fi network only**. Do not port-forward it, expose it to the internet, or use it on public Wi-Fi. HTTP means traffic on the same network is not encrypted. For use beyond an isolated lab, add TLS with certificate validation/pinning before connecting devices. Windows firewall should allow port 8765 only on Private networks. Pairing codes expire after five minutes, are single-use, and pairing attempts are rate-limited. The bearer token is stored in Android app-private preferences and can be revoked from Windows.

## 1. Run the Windows controller

Requirements: Windows 10/11, Python 3.12, and a trusted private Wi-Fi network shared with your own Android test phone.

1. Extract the project ZIP.
2. Open `windows_controller` in File Explorer.
3. Double-click `run_windows.bat` (or run it from a terminal).
4. If Windows Firewall prompts, allow Python only on **Private networks**. Do not create a public-network or router port-forward rule.
5. The app shows the PC's LAN IP address and port `8765`. If several IPs are listed, use the address on the same Wi-Fi as the phone.
6. Click **Generate pairing code**. The code is six digits and expires in five minutes.

Manual setup alternative:

```powershell
cd windows_controller
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python main.py
```

## 2. Build and install the Android app

Requirements: Android Studio (current stable), Android SDK Platform 35, and a USB-connected test phone or Android emulator.

1. Open the `android_client` folder in Android Studio.
2. Let Gradle sync and install the required Android SDK components if prompted.
3. Select your own test phone and press **Run**. For an emulator, use the host PC address `10.0.2.2` instead of the Windows LAN IP.
4. In the app, enter the Windows LAN IP only (no `http://` and no port), enter the six-digit code, and tap **Pair this device**.
5. Tap **Send approved diagnostics** to send model, Android version, app version, battery percentage, and charging state.
6. Use **Disconnect and forget token** on Android, or **Revoke paired device** on Windows to end access. Windows revocation invalidates the token immediately.

Build an APK from Android Studio using **Build > Build Bundle(s) / APK(s) > Build APK(s)**. Android Studio will show the generated APK path.

## 3. Audit trail

`windows_controller/audit.log` records pairing attempts, pairing success, heartbeat/check-ins, diagnostic summaries, and revocations. Authentication tokens and pairing codes are never written to the audit log. The dashboard displays recent log entries.

## 4. Troubleshooting

- **Phone cannot connect:** Verify both devices are on the same private Wi-Fi, use the Windows IPv4 address shown in the dashboard, and allow inbound TCP 8765 on the Windows Private firewall profile only.
- **Pairing code rejected:** Generate a fresh code; codes are single-use and expire after five minutes.
- **Already paired:** Revoke the existing phone from Windows before pairing another.
- **Android Studio sync failure:** Update Android Studio and install Android SDK Platform 35. This source was packaged as a starter project; an APK is not included.
- **Network protection:** Do not use this HTTP starter on public Wi-Fi or across networks. TLS and certificate validation are required before expanding beyond a trusted isolated lab.

## API summary

- `GET /health` — local service readiness.
- `POST /pair` — exchange the single-use code for a bearer token.
- `POST /heartbeat` — authenticated connection check-in.
- `POST /diagnostics` — authenticated, limited diagnostic report.
- `POST /disconnect` — authenticated client-side disconnect.

The API intentionally has no command execution endpoint.
