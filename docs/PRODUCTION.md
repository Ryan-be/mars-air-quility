# Production Deployment Guide

This document covers the steps required before safely exposing MLSS Monitor to the internet.

[Back to main README](../readme.md)

---

## Application-level safeguards (implemented)

These features are already built into the application:

| Feature | Status |
|---|---|
| GitHub OAuth login flow (`/auth/github`, `/auth/callback`) | Implemented |
| RBAC -- three roles (admin, controller, viewer) on all write endpoints | Implemented |
| User management UI -- add/remove GitHub users, change roles | Implemented |
| Login audit log -- per-user history, visible to admins | Implemented |
| Session guard -- redirects unauthenticated users | Implemented |
| `MLSS_ALLOWED_GITHUB_USER` bootstrap admin (permanent recovery path) | Implemented |
| `MLSS_SECRET_KEY` loaded from config/env | Implemented |
| Startup log confirms auth status (`Auth ENABLED`) | Implemented |
| Weather log rolling window (auto-purge > 7 days) | Implemented |
| CI pipeline -- separate lint + test workflows | Implemented |
| Pylint 10/10 score enforced in CI | Implemented |
| Unit test suite (fan settings, async, resilience, open-meteo, forecasts, weather history, RBAC) | Implemented |

---

## Authentication and secrets

- [ ] **Set `MLSS_SECRET_KEY`** to a cryptographically random value:
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
- [ ] **Configure GitHub OAuth** -- create an OAuth App at `https://github.com/settings/developers`. Set the callback URL to your domain (`https://yourdomain.com/auth/callback`).
- [ ] **Set `MLSS_ALLOWED_GITHUB_USER`** to your GitHub handle (bootstrap admin -- first login uses this).
- [ ] Remove or rotate any test/development credentials from `.env`.
- [ ] Confirm startup log shows `Auth ENABLED` before opening the firewall.

---

## TLS / HTTPS (nginx reverse proxy)

Running Flask directly on port 5000 over plain HTTP is not safe on the internet. Use **nginx** as a reverse proxy with a TLS certificate from Let's Encrypt.

### 1. Install nginx and certbot

```bash
sudo apt install nginx certbot python3-certbot-nginx -y
```

### 2. Create an nginx site config

Save the following to `/etc/nginx/sites-available/mlss`:

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Rate limiting -- protects login endpoint
    limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

    location /login {
        limit_req zone=login burst=10 nodelay;
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 3. Enable and obtain certificate

```bash
sudo ln -s /etc/nginx/sites-available/mlss /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com
```

### 4. Auto-renew

Certbot installs a cron/timer automatically. Verify with:

```bash
sudo certbot renew --dry-run
```

---

## Domain / DDNS

You need a hostname that points to your public IP.

| Option | Cost | Notes |
|---|---|---|
| [DuckDNS](https://www.duckdns.org) | Free | `yourname.duckdns.org` -- update script runs on Pi via cron |
| [No-IP](https://www.noip.com) | Free tier | Similar to DuckDNS |
| Custom domain | ~10/yr | Point an A record at your public IP; best with a static IP or DDNS |

DuckDNS cron update (every 5 minutes):

```bash
# Add to crontab -e
*/5 * * * * curl -s "https://www.duckdns.org/update?domains=yourname&token=YOUR_TOKEN&ip=" > /dev/null
```

---

## Firewall (ufw)

Block direct access to the Flask port; only allow nginx traffic:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # 80 + 443
sudo ufw deny 5000            # block Flask from external access
sudo ufw enable
sudo ufw status
```

---

## Additional hardening

- [ ] **fail2ban** -- auto-ban IPs with repeated failed logins:
  ```bash
  sudo apt install fail2ban -y
  # Configure /etc/fail2ban/jail.local for nginx
  ```
- [ ] **Unattended upgrades** -- keep OS patched automatically:
  ```bash
  sudo apt install unattended-upgrades -y
  sudo dpkg-reconfigure unattended-upgrades
  ```
- [ ] **SSH key-only auth** -- disable password SSH login (`PasswordAuthentication no` in `/etc/ssh/sshd_config`).
- [ ] **DB backups** -- add a cron job to copy `data/sensor_data.db` to a safe location:
  ```bash
  # Add to crontab -e
  0 3 * * * cp ~/mars-air-quality/data/sensor_data.db ~/backups/sensor_$(date +\%Y\%m\%d).db
  ```
- [ ] **Log rotation** -- ensure journald or a logrotate config is set so logs don't fill the SD card.
- [ ] **Flask `SESSION_COOKIE_SECURE=True`** -- ensure session cookies are HTTPS-only (set once TLS is in place).
- [ ] **Use gunicorn** for production instead of the Flask development server. The repo ships with a `gunicorn.conf.py` and a WSGI entry point (`mlss_monitor/wsgi.py`) -- always invoke gunicorn through them:
  ```bash
  poetry run gunicorn -c gunicorn.conf.py mlss_monitor.wsgi:application
  ```
  Why the conf file matters:
  - `worker_class = "gthread"`, `workers = 1`, `threads = 8` -- a single worker process keeps the event bus, hot tier, anomaly detector models, and SSE subscribers in **one** address space. Multiple workers would each run their own background services and clients would only see events from whichever worker happened to handle their `/api/stream` connection.
  - `timeout = 0` -- gthread workers must never be killed for being "idle"; SSE connections legitimately stay open for minutes. Per-connection lifetime (default 600 s) is enforced inside `generate()` in `routes/api_stream.py` instead.
  - `preload_app = True` -- the app module is imported once before the worker forks, so `_start_background_services()` (sensor poller, weather loop, inference engine, anomaly bootstrap timer) runs exactly once.
  - `SSL_CERT_FILE` / `SSL_KEY_FILE` are read from the same dynaconf keys the Flask dev server uses, so HTTPS works without any extra gunicorn flags.

  The `mlss-monitor.service` unit invokes gunicorn this way; do **not** revert to `python -m mlss_monitor.app` or a hand-rolled `gunicorn -w N -b ...` command.

---

## Pre-launch checklist

| # | Task | Done |
|---|---|---|
| 1 | `MLSS_SECRET_KEY` set to random 32-byte hex | [ ] |
| 2 | GitHub OAuth App created with HTTPS callback URL | [ ] |
| 3 | `MLSS_ALLOWED_GITHUB_USER` set | [ ] |
| 4 | nginx installed and reverse-proxying port 5000 | [ ] |
| 5 | TLS certificate obtained from Let's Encrypt | [ ] |
| 6 | HTTPS redirect in nginx (port 80 -> 443) | [ ] |
| 7 | Rate limiting on `/login` in nginx config | [ ] |
| 8 | Port 5000 blocked in ufw | [ ] |
| 9 | fail2ban installed and configured for nginx | [ ] |
| 10 | SSH key-only auth enabled | [ ] |
| 11 | Unattended upgrades enabled | [ ] |
| 12 | Daily DB backup cron job | [ ] |
| 13 | `Auth ENABLED -- GitHub OAuth` confirmed in service log | [ ] |
| 14 | Log in as bootstrap admin -> Settings -> Users -> add team members | [ ] |
| 15 | End-to-end test: login -> dashboard -> logout from external network | [ ] |
