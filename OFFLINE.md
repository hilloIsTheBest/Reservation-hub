Offline Windows App (.exe)

This repo includes an offline mode that packages the FastAPI app into a single Windows executable. When you run the .exe, it starts a local server, opens your browser to the UI, and works fully offline. It can also import (sync) data from a running online server when you provide its URL.

Build steps (Windows)

1) Install dependencies:

   - Python 3.12 (recommended)
   - In a virtualenv: `pip install -r requirements.txt`
   - Add the packager: `pip install pyinstaller`

2) Build the executable:

   pyinstaller --onefile --name FamilyFleetOffline \
     --add-data "templates;templates" \
     --add-data "static;static" \
     offline_runner.py

3) Run the app:

   - Launch `dist/FamilyFleetOffline.exe`.
   - It will pick a free local port (e.g., 127.0.0.1:9629) and open your browser.
   - Data is stored in `%APPDATA%\FamilyFleetOffline\app.db`.

How offline works

- The app runs in OFFLINE mode (no OIDC login). You are automatically logged in as a local admin user.
- UI is the new Homes experience. You can create homes/resources and schedule single or recurring reservations.
- Click the Sync button (top-right) to import from an online server:
  - Enter the base URL of the online server (e.g., `https://example.com:9629`).
  - The offline app fetches `/api/resources` and `/ics/all.ics` and imports them into an "Offline Home".
  - This is a one-way import (pull). Pushing changes back requires authentication on the server and is not automated here.

Notes

- If you want bidirectional sync, the online server would need a token-based API or a sync endpoint to accept uploads. The current server exposes public reads but requires login to write.
- The executable bundles templates/static via PyInstaller’s `--add-data`. If you change those folders, rebuild the exe.
- The offline app can run without any internet connection; the Sync step is optional.

