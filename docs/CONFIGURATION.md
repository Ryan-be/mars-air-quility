# Configuration Reference

All configuration for MLSS Monitor is managed via environment variables, loaded by [Dynaconf](https://www.dynaconf.com) with the prefix `MLSS_`. Copy `.env.example` to `.env` and fill in your values.

[Back to main README](../readme.md)

---

## Environment variables

### Core settings

| Variable | Default | Description |
|---|---|---|
| `ENV_FOR_DYNACONF` | `production` | Dynaconf environment name |
| `LOG_INTERVAL` | `10` | Sensor polling interval in seconds |
| `LOG_FILE` | `data/log.csv` | Legacy CSV log path (not actively used by the web app) |
| `DB_FILE` | `data/sensor_data.db` | SQLite database file path |
| `FAN_KASA_SMART_PLUG_IP` | `192.168.1.63` | IP address of the TP-Link Kasa smart plug |

### Authentication (GitHub OAuth)

MLSS Monitor uses GitHub OAuth for authentication. Local username/password login is not supported.

| Variable | Default | Description |
|---|---|---|
| `MLSS_SECRET_KEY` | dev fallback | Flask session signing secret -- **must be set to a random value in production** |
| `MLSS_GITHUB_CLIENT_ID` | *(required)* | GitHub OAuth App client ID |
| `MLSS_GITHUB_CLIENT_SECRET` | *(required)* | GitHub OAuth App client secret |
| `MLSS_ALLOWED_GITHUB_USER` | *(unset)* | Bootstrap admin GitHub username -- always grants admin access, even without a DB entry. Use for first-time setup and recovery. |

> **Setup:** Create a GitHub OAuth App at `https://github.com/settings/developers`. Set the callback URL to `https://yourdomain.com/auth/callback` (or `http://localhost:5000/auth/callback` for development). Copy the client ID and secret into your `.env`.

### TLS (optional, for direct HTTPS)

These are needed if you want the application server to terminate TLS itself (no nginx in front). The same three keys are honoured by both the Flask development server and gunicorn (`gunicorn.conf.py` reads `SSL_CERT_FILE` / `SSL_KEY_FILE` from dynaconf), so HTTPS works in both modes without any extra flags. For internet-facing deployments, an nginx reverse proxy with Let's Encrypt is still recommended -- see the [Production deployment guide](PRODUCTION.md).

| Variable | Default | Description |
|---|---|---|
| `HTTPS_ENABLED` | `true` | Enable TLS on the Flask dev server (gunicorn enables TLS automatically when both cert and key files exist) |
| `SSL_CERT_FILE` | `certs/cert.pem` | Path to TLS certificate file |
| `SSL_KEY_FILE` | `certs/key.pem` | Path to TLS private key file |

---

## Secrets management

For production deployments, secrets should be separated from non-secret config:

- **`.env`** (in project directory) -- non-secret configuration only (readable by the service user)
- **`/etc/mlss/secrets.env`** (root-owned, mode 600) -- secrets injected by systemd at startup

The `mlss-monitor.service` systemd unit loads both files:

```ini
EnvironmentFile=%h/projects/git_versions/mars-air-quility/.env
EnvironmentFile=/etc/mlss/secrets.env
```

Generate a secret key:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Application settings (database)

These settings are stored in the `app_settings` key/value table and managed via the admin UI or REST API.

| Key | API endpoint | Description |
|---|---|---|
| `location_lat` | `POST /api/settings/location` | Latitude for weather lookups |
| `location_lon` | `POST /api/settings/location` | Longitude for weather lookups |
| `location_name` | `POST /api/settings/location` | Display name for the location |
| `energy_unit_rate_pence` | `POST /api/settings/energy` | Electricity cost in pence per kWh (for energy cost estimates) |

---

## Fan auto-mode settings (database)

Fan threshold settings are stored in the `fan_settings` table and managed via the admin UI or `POST /api/fan/settings`.

| Field | Default | Description |
|---|---|---|
| `temp_max` | *(user-configured)* | Temperature above which the fan turns on (C) |
| `temp_min` | *(user-configured)* | Temperature below which the fan turns off (C) |
| `tvoc_max` | *(user-configured)* | TVOC level above which the fan turns on (ppb) |
| `tvoc_min` | *(user-configured)* | TVOC level below which the fan turns off (ppb) |
| `enabled` | `0` | Whether auto mode is active (`1` = enabled) |

---

## Inference thresholds

Inference thresholds are stored in the `inference_thresholds` table. Each threshold has a default value and an optional user override. Manage via the admin settings page or `POST /api/settings/thresholds`.

### Air quality thresholds

| Key | Default | Unit | Description |
|---|---|---|---|
| `tvoc_high` | 500 | ppb | WHO "high" threshold for TVOC |
| `tvoc_moderate` | 250 | ppb | WHO "good" ceiling for TVOC |
| `eco2_cognitive` | 1000 | ppm | eCO2 level associated with cognitive impairment |
| `eco2_danger` | 2000 | ppm | eCO2 level associated with headaches and drowsiness |

### Climate thresholds

| Key | Default | Unit | Description |
|---|---|---|---|
| `temp_high` | 28.0 | C | Upper comfort boundary for temperature |
| `temp_low` | 15.0 | C | Lower comfort boundary for temperature |
| `hum_high` | 70.0 | % | Upper ideal boundary for humidity (mould risk) |
| `hum_low` | 30.0 | % | Lower ideal boundary for humidity (dry air) |

### Plant/VPD thresholds

| Key | Default | Unit | Description |
|---|---|---|---|
| `vpd_low` | 0.4 | kPa | VPD below which air is too saturated (plant stress) |
| `vpd_high` | 1.6 | kPa | VPD above which plant stomata close (plant stress) |

### Mould risk thresholds

| Key | Default | Unit | Description |
|---|---|---|---|
| `mould_hum` | 70.0 | % | Sustained humidity level for mould risk |
| `mould_temp` | 20.0 | C | Temperature above which mould risk increases |
| `mould_hours` | 4 | hours | Duration of sustained conditions before flagging |

### Detection parameters

| Key | Default | Unit | Description |
|---|---|---|---|
| `spike_factor` | 2.0 | multiplier | Factor above rolling mean to classify as a spike |
| `min_readings` | 6 | count | Minimum data points required before analysis runs |

---

## Dynaconf details

Configuration is loaded in `config.py`:

```python
from dynaconf import Dynaconf

config = Dynaconf(
    envvar_prefix="MLSS",
    settings_files=[".env"],
    load_dotenv=True,
)
```

- All environment variables prefixed with `MLSS_` are automatically available via `config.VARIABLE_NAME`.
- The `.env` file is loaded automatically on startup.
- Environment variables set in the shell or systemd override `.env` values.
