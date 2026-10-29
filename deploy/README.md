# Mirmera on a server (AletCloud or any Ubuntu VPS)

One server runs everything with Docker:

- **web** (Caddy) serves the app at `https://DOMAIN`, sends `https://DOMAIN/api/...` to the
  backend, and gets the HTTPS certificate from Let's Encrypt by itself.
- **app** (the backend, with Chromium) listens on `127.0.0.1:8000`, which is not reachable from
  outside. Its data (database, evidence, `secret.key`) lives in `/srv/mirmera-data` on the
  server, never in the image.
- **AI:** the backend asks `127.0.0.1:11434`, which is Qwen on your laptop through an SSH tunnel
  (`scripts/qwen-tunnel`), or optionally Ollama on the server itself.

Commands marked **server** run in an SSH session on the server (bash). Commands marked
**laptop** run on your Windows computer, in PowerShell or cmd.

## 0. What you need

- **A VPS:** Ubuntu 24.04, 4 vCPU and 8 GB RAM (2 vCPU and 4 GB is the minimum), plus a
  public IP.
- **A domain or subdomain**, e.g. `mirmera.addisverify.com`, with an **A record** pointing to
  the public IP. Caddy can't get a certificate until it does.
- **Your SSH key** added when you create the server (or the root password AletCloud gives you).

## 1. Prepare the server

**laptop** (the same in PowerShell and cmd):
```
ssh root@SERVER_IP
```

**server:**
```bash
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh          # Docker and the compose plugin
ufw allow OpenSSH && ufw allow 80,443/tcp && ufw --force enable
```

## 2. Get the code

The repository is private, so give the server a read-only **deploy key**.

**server:**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/mirmera_deploy -N "" -C "mirmera server"
cat ~/.ssh/mirmera_deploy.pub
```
On GitHub, open attuye/mirmera → Settings → Deploy keys → Add deploy key. Paste the key and
leave "Allow write access" off. Then:
```bash
cat >> ~/.ssh/config <<'CFG'
Host github.com
  IdentityFile ~/.ssh/mirmera_deploy
CFG
git clone git@github.com:attuye/mirmera.git /srv/mirmera
mkdir -p /srv/mirmera-data && chown 10001:10001 /srv/mirmera-data   # the app's user in the image
```

## 3. Settings

**server:**
```bash
cd /srv/mirmera
cp deploy/.env.example deploy/.env && chmod 600 deploy/.env
nano deploy/.env
```
Fill in `DOMAIN`, `PUBLIC_APP_URL`, `CORS_ORIGINS` (all the same address) and
`MIRMERA_SECRET_KEY`:

- **Moving from your laptop:** use the same value as in `backend\.env` on the laptop. Saved
  keys (Jira, GitHub, Telegram…) are encrypted with it.
- **New install:** use a new random phrase. Keep a copy somewhere safe.

## 4. Bring your laptop's data (optional)

Skip this to start empty.

1. **laptop:** stop the backend (Ctrl+C), so the database is complete in one file.
2. **laptop**, copy the database and the evidence:

   PowerShell:
   ```powershell
   cd E:\addis-verify-projects\mirmera\backend
   scp .\mirmera.db root@SERVER_IP:/srv/mirmera-data/
   scp -r .\evidence root@SERVER_IP:/srv/mirmera-data/
   ```
   cmd:
   ```bat
   cd /d E:\addis-verify-projects\mirmera\backend
   scp mirmera.db root@SERVER_IP:/srv/mirmera-data/
   scp -r evidence root@SERVER_IP:/srv/mirmera-data/
   ```
   If your laptop's `backend\.env` has no `MIRMERA_SECRET_KEY`, copy `secret.key` the same way
   (`scp secret.key root@SERVER_IP:/srv/mirmera-data/`), and leave `MIRMERA_SECRET_KEY` empty
   on the server.
3. **server:** give the files to the app's user, build, and point the evidence at the new
   folder. The database keeps full Windows paths (`E:\...\evidence\...`):
   ```bash
   chown -R 10001:10001 /srv/mirmera-data
   cd /srv/mirmera
   docker compose -f deploy/docker-compose.yml build
   docker compose -f deploy/docker-compose.yml run --rm app python -m app.move_evidence           # shows what changes
   docker compose -f deploy/docker-compose.yml run --rm app python -m app.move_evidence --apply   # changes it
   ```

## 5. Start

**server:**
```bash
cd /srv/mirmera
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f      # Ctrl+C to stop watching
```
The first build takes a few minutes. Open `https://DOMAIN` once Caddy logs `certificate obtained`.

## 6. Qwen on your laptop, for the server

The laptop opens an SSH tunnel to the server. The server's `127.0.0.1:11434` then leads to
Ollama on the laptop. Nothing on the laptop is opened to the internet, and the tunnel's key can
do nothing else on the server.

1. **laptop**, make a key for the tunnel (press Enter at the passphrase questions):

   PowerShell:
   ```powershell
   ssh-keygen -t ed25519 -f "$HOME\.ssh\mirmera_tunnel" -C "qwen tunnel"
   Get-Content "$HOME\.ssh\mirmera_tunnel.pub"
   ```
   cmd:
   ```bat
   ssh-keygen -t ed25519 -f "%USERPROFILE%\.ssh\mirmera_tunnel" -C "qwen tunnel"
   type "%USERPROFILE%\.ssh\mirmera_tunnel.pub"
   ```
2. **server**, a user that may only open this one tunnel. Paste the key in place of `ssh-ed25519 AAAA...`:
   ```bash
   adduser --disabled-password --gecos "" tunnel
   install -d -m 700 -o tunnel -g tunnel /home/tunnel/.ssh
   echo 'restrict,port-forwarding,permitlisten="127.0.0.1:11434" ssh-ed25519 AAAA... qwen tunnel' > /home/tunnel/.ssh/authorized_keys
   chown tunnel:tunnel /home/tunnel/.ssh/authorized_keys && chmod 600 /home/tunnel/.ssh/authorized_keys
   ```
3. **laptop**, with Ollama running, start the tunnel and keep the window open. It reconnects
   by itself.

   PowerShell:
   ```powershell
   cd E:\addis-verify-projects\mirmera
   .\scripts\qwen-tunnel.ps1 -Server SERVER_IP
   ```
   cmd:
   ```bat
   cd /d E:\addis-verify-projects\mirmera
   scripts\qwen-tunnel.cmd SERVER_IP
   ```
   If PowerShell refuses to run scripts, run this first:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
4. **server**, check it works. This lists your models:
   ```bash
   curl -s http://127.0.0.1:11434/api/tags
   ```

While the laptop is off or the tunnel is closed, AI steps fail with "Couldn't reach Ollama…".
Everything else in Mirmera keeps working.

## 7. Close sign-ups

Once your account exists (and your team's, or invite them from the app), **server:**
```bash
cd /srv/mirmera
sed -i 's/^ALLOW_SIGNUPS=.*/ALLOW_SIGNUPS=false/' deploy/.env
docker compose -f deploy/docker-compose.yml up -d     # recreates the app with the new setting
```

## 8. Backups

`deploy/backup.sh` makes a consistent copy of the database every night and keeps it
`BACKUP_KEEP_DAYS` days in `/srv/mirmera-data/backups`. With `BACKUP_S3_*` filled in
(AletCloud object storage gives you the endpoint and keys), it also uploads the database and
any new evidence. **server:**
```bash
/srv/mirmera/deploy/backup.sh                           # once by hand, to check it
( crontab -l 2>/dev/null; echo '15 2 * * * /srv/mirmera/deploy/backup.sh >> /var/log/mirmera-backup.log 2>&1' ) | crontab -
```
`MIRMERA_SECRET_KEY` (or `secret.key`) is not in the backup on purpose. Keep it somewhere else:
a backup without it can't read the saved keys, and a backup with it gives them away.

## Updating

**server:**
```bash
cd /srv/mirmera && git pull
docker compose -f deploy/docker-compose.yml up -d --build
```
Database changes are applied when the app starts, as on your laptop. Back up first:
`deploy/backup.sh`.

## Using it from the server

- **Browser extension:** in its settings, set the API to `https://DOMAIN/api` and the app to
  `https://DOMAIN`.
- **Apps under test** must be reachable from the server. Apps on your laptop or on an office
  network aren't. Use a public staging address, or run the app on the server.
- **Manual sessions:** the server has no screen, so a session's recorded browser can't be shown
  to you there. Test in your own browser with the extension. Mirmera's own agents
  (exploration, reproduction, regression, compare) run on the server as usual.
- **The Jira app** works now that Mirmera has a public HTTPS address: see `jira-app/README.md`.
- **When people outside your team use it,** set `API_BLOCK_PRIVATE=true` and
  `SERVER_KEYS_FOR_ALL=false`.

## When something is wrong

| What you see | Look at / do |
|---|---|
| The site doesn't open | `docker compose -f deploy/docker-compose.yml ps`; Caddy's log for certificate errors. Check that the A record points here and ports 80/443 are open |
| "Could not reach the backend" | `docker compose -f deploy/docker-compose.yml logs app` |
| AI: "Couldn't reach Ollama" | The tunnel on the laptop (step 6). Check from the server with `curl http://127.0.0.1:11434/api/tags` |
| Explorations fail to start a browser | `logs app`; the `shm_size` and `init` lines in docker-compose.yml must stay |
| Screenshots missing after moving | Step 4.3 (`python -m app.move_evidence --apply`) |
