# Running pulse-mirror on a Synology NAS

This guide gets pulse-mirror running in Container Manager, reachable on your home network.
Times are rough guesses. Steps marked 🖐 happen in the DSM web UI.

You need DSM 7.2 or newer, and a NAS model that supports **Container Manager** (most x86 models and the
ARMv8 "+"/"play" models). The image is built for `linux/amd64` and `linux/arm64`.

## 1. Prepare the NAS (10 min)

1. 🖐 **Package Center** → install **Container Manager**.
2. 🖐 **Control Panel → Info Center → General**: note the model and the CPU. Any amd64 or arm64 CPU works.
3. 🖐 **Control Panel → Terminal & SNMP** → enable **SSH service** (port 22, or another port you prefer).
4. 🖐 **Control Panel → User & Group → Advanced** → tick **Enable user home service** (needed for SSH keys).
5. 🖐 **File Station**: create the folder `docker/pulse-mirror` with a subfolder `data`.
   The path is usually `/volume1/docker/pulse-mirror`.

## 2. SSH with a key from your laptop (10 min)

On the laptop:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_nas -C "laptop → nas"   # skip if you already have a key
ssh-copy-id -i ~/.ssh/id_nas.pub <your-dsm-user>@<nas-name>.fritz.box
```

Synology refuses keys while the home folder is group-writable, so fix the permissions once, on the NAS.
If SSH says `Could not chdir to home directory … Permission denied`, your user has no access to the
`homes` shared folder: check *Control Panel → Shared Folder → homes → Edit → Permissions* for a
"No access" tick on your user or on one of its groups.

```bash
ssh <your-dsm-user>@<nas-name>.fritz.box
chmod 755 ~ && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
exit
```

Add a shortcut to `~/.ssh/config` on the laptop:

```
Host nas
    HostName <nas-name>.fritz.box
    User <your-dsm-user>
    IdentityFile ~/.ssh/id_nas
```

`ssh nas` should now work without a password. Disabling password logins for SSH is optional.
Only do it once key login works.

**Docker without sudo (optional, convenient for logs and restarts):**

1. 🖐 **Control Panel → User & Group → Group**: create a group `docker` and add your user.
   In the wizard's *shared folder permissions* step, **leave every row completely unticked**.
   ⚠️ Don't tick "No access": in DSM that is an explicit *deny* rule, and deny beats allow. Because
   your own user is in the group, it would lock you out of `homes` (and so out of SSH keys) and of
   every other shared folder.
2. Hand the Docker socket to that group:
   - 🖐 **Control Panel → Task Scheduler → Create → Triggered Task → User-defined script**
   - User: `root`, Event: `Boot-up`
   - Script: `chown root:docker /var/run/docker.sock`
   - Run the task once by hand.

Otherwise, prefix the Docker commands below with `sudo`.

Over a non-interactive SSH session, Docker isn't on the `PATH`. Use `/usr/local/bin/docker`.
DSM also has SFTP switched off by default, so plain `scp` fails; use `scp -O` or pipe through SSH
(`ssh nas 'cat > file' < file`).

## 3. Fixed address in the FRITZ!Box (2 min)

🖐 FRITZ!Box: **Home Network → Network → Network Connections** → edit the NAS → tick
**Always assign this network device the same IPv4 address**. The name `<nas-name>.fritz.box` then keeps
pointing to the NAS.

## 4. Configuration (5 min)

Put two files into `/volume1/docker/pulse-mirror/`:

- `compose.yaml`, copied from this repository.
- `.env`, created from [`.env.example`](../.env.example). It may stay almost empty. Typical content:

```bash
TZ=Europe/Berlin
PULSE_SYNC_TIME=08:00
# The NAS user that owns ./data. Find it with `id` in an SSH session (Synology: often 1026 / 100)
PUID=1026
PGID=100
```

The `data` folder must belong to that user. Created through File Station, it already does.

## 5. Start the container (5 min)

🖐 **Container Manager → Project → Create**

- Name: `pulse-mirror`
- Path: `/volume1/docker/pulse-mirror`
- Source: *Use existing docker-compose.yml*, then **Next → Done**

Or over SSH: `cd /volume1/docker/pulse-mirror && docker compose up -d`.

If pulling the image fails with "denied", the GHCR package is still private. On GitHub, go to
*Profile → Packages → pulse-mirror → Package settings → Change visibility → Public*.

## 6. One-time logins (5 min, needs you at the keyboard)

Both commands are interactive. You type the passwords yourself, and nothing goes through chat or the repo.

```bash
ssh nas
cd /volume1/docker/pulse-mirror

# Dashboard password (stored hashed in data/auth.json)
docker exec -it pulse-mirror pulse-mirror auth set-password

# Garmin Connect: email, password and MFA code. Only the resulting tokens are stored, in data/garmin_tokens/
docker exec -it pulse-mirror pulse-mirror login garmin

docker restart pulse-mirror
```

⚠️ **Don't repeat the Garmin login in quick succession.** Garmin blocks accounts for hours or days after
repeated logins. If a login fails, wait at least 30 minutes before you try again.

Then open **`http://<nas-name>.fritz.box:8420`** on your phone or laptop and log in.

## 7. Load the history: backfill (runs one or two nights)

*Already ran the backfill on another machine?* Stop it there (Ctrl-C saves its position), copy
`data/pulse.db` and `data/garmin_tokens/` into the NAS's `data/` folder before the first start, and
rename the token folder on the old machine so that only one place uses the Garmin login.

```bash
docker exec -d pulse-mirror sh -c 'pulse-mirror backfill garmin >> /data/backfill.log 2>&1'
tail -f /volume1/docker/pulse-mirror/data/backfill.log      # Ctrl-C only stops watching
docker exec pulse-mirror pulse-mirror status
```

The backfill walks backwards one day at a time with pauses between requests. You can stop and restart it
at any time, and it resumes where it stopped. It finishes by itself once it reaches the days before your
first Garmin device. If Garmin rate-limits it, it pauses itself (status `paused`). Run the same command
again later to continue.

## 8. Updates (automatic, 3 min to set up)

New images are built by GitHub Actions on every merge to `main`. To pull them weekly:

🖐 **Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**

- User: `root`, Schedule: weekly (e.g. Sunday 04:00)
- Script:

```bash
cd /volume1/docker/pulse-mirror && docker compose pull && docker compose up -d && docker image prune -f
```

The same line, run by hand over SSH, updates immediately.

## 9. Backups

- `docker exec pulse-mirror pulse-mirror db backup` writes a consistent copy to `data/backups/` (keeps 14).
  Schedule it daily in the Task Scheduler, e.g. at 03:00:
  `docker exec pulse-mirror pulse-mirror db backup`
- Include `/volume1/docker/pulse-mirror/data` in **Hyper Backup**.

## 10. Morning email and weekly PDF (optional)

Add to `.env`, then restart the container:

```bash
PULSE_SMTP_HOST=smtp.example.com      # your mail provider's SMTP server
PULSE_SMTP_PORT=587
PULSE_SMTP_SECURITY=starttls          # or ssl with port 465
PULSE_SMTP_USER=you@example.com
PULSE_SMTP_PASSWORD=app-password      # an app password if your provider offers them
PULSE_NOTIFY_TO=you@example.com
```

Test it with `docker exec pulse-mirror pulse-mirror notify test`. The summary is sent right after the
daily sync. If the sync failed (for example because the Garmin login expired), you get an alert instead.
Some providers require SMTP access to be switched on in their web settings first.

**Weekly PDF in Synology Drive:** the report is written every Sunday at `PULSE_REPORT_TIME` (default
20:00). To get it onto your devices, mount a Drive folder into the container and point the report
directory at it. In `compose.yaml` under `volumes`, add
`- /volume1/homes/<your-dsm-user>/Drive/pulse-mirror:/reports`, and in `.env` set `PULSE_REPORT_DIR=/reports`.

## Everyday commands

```bash
docker logs --tail 100 pulse-mirror            # what happened
docker exec pulse-mirror pulse-mirror status   # last sync, backfill progress, data range
docker exec pulse-mirror pulse-mirror sync     # fetch now (same as the refresh button)
docker restart pulse-mirror
```
