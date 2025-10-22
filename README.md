# Attendance Sync

> Community-driven attendance dashboard for ZKTeco biometric devices. Intended **for non-commercial use only**.

Attendance Sync is a Flask-based web application that downloads punches from a ZKTeco terminal, stores them in SQLite, and presents a modern dashboard for reviewing attendance data, editing make-up hours, and exporting timesheets. The project also includes tooling to test device connectivity before running a sync, making it approachable for small offices and open communities.


## Features

- Guided device onboarding with live connection testing and support for common ZKTeco models.
- One-click attendance sync that imports punches into SQLite and keeps users in sync with the device roster.
- Interactive dashboard with filters, per-employee summaries, make-up hour editing, and Excel export.
- Device management UI for storing multiple ZKTeco terminals and reusing their configuration.
- Lightweight data layer built on SQLite—no additional services required.


## Project Structure

```
.
├── app.py                # Flask application factory and HTTP routes
├── db.py                 # SQLite helpers and CRUD utilities
├── summary.py            # Attendance aggregation and reporting helpers
├── zk_sync.py            # ZKTeco connectivity & sync helpers
├── templates/            # Flask templates (Bootstrap 5 UI)
├── static/               # CSS and static assets
├── requirements.txt      # Python dependencies
└── README.md             # This guide
```


## Prerequisites

- Python 3.10 or later
- A ZKTeco attendance device reachable from the machine running the app
- Recommended: a virtual environment (``python -m venv .venv``)


## Getting Started

```bash
git clone https://github.com/hnthnh/zk-pi-attendance.git
cd zk-pi-attendance/MayChamCongApp
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Run the development server:

```bash
flask --app app run
```

Navigate to `http://127.0.0.1:5000` and you will be taken to the device connection wizard.


## Connecting a ZKTeco Device

1. Open the **Connect** tab in the navigation bar.
2. Enter the device IP, port (default `4370`), password (if configured), and timeout.
3. Click **Test connection** to verify the device responds. Firmware and serial number details will be displayed on success.
4. Save the configuration to unlock the dashboard and start syncing.

The connection wizard stores the working configuration in the user session and requires a new device selection before you can reach the dashboard—preventing accidental syncs against the wrong terminal.


## Syncing Attendance Data

- Use the **Sync Data** button on the dashboard once a device is connected.
- Imported records are added to `attendance.db`. Existing entries are de-duplicated automatically.
- User information from the device is mirrored into the local database so you can enrich profiles with names and departments manually.


## Data Export

- Filter the dashboard by user and/or date range.
- Click **Export to Excel** to download a spreadsheet with working hours, make-up entries, and totals.
- Make-up hour adjustments can be entered directly from the dashboard modal and are tracked in a dedicated SQLite table.


## Configuration Reference

The application reads optional environment variables to provide defaults for device connectivity:

| Variable | Description | Default |
| --- | --- | --- |
| `ZK_DEVICE_IP` | Fallback IP address for the device wizard | `192.168.0.201` |
| `ZK_DEVICE_PORT` | Default port value | `4370` |
| `ZK_DEVICE_TIMEOUT` | Connection timeout in seconds | `5` |
| `ZK_DEVICE_PASSWORD` | Device password (numeric) | `0` |
| `ZK_DEVICE_FORCE_UDP` | Force UDP transport (`true` / `false`) | `false` |
| `FLASK_SECRET_KEY` | Secret key for Flask sessions | `maychamcong-secret-key` |


## Contributing

Contributions that improve device compatibility, documentation, or user experience are welcome. Please:

1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/awesome`).
3. Commit changes with clear messages.
4. Open a pull request describing your improvements.

By contributing, you confirm that your changes are intended for community and non-commercial purposes.


## License

This project is released for community collaboration. **Commercial redistribution or resale is not permitted.** See `LICENSE` (to be provided) for full terms or include this README notice with any distribution.


## Acknowledgements

- Built on [Flask](https://flask.palletsprojects.com/) and [Bootstrap 5](https://getbootstrap.com/)
- Uses the [`zk`](https://pypi.org/project/zk/) library for ZKTeco integrations
