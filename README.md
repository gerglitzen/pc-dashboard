# PC Dashboard

A live system monitor for this PC that you open in a web browser on another device on the same network (e.g. an old iPad), so that device works as a wireless status screen.

It shows CPU (total, each core, clock, load), CPU temperature, fan speed, SSD temperature, RAM and swap, network download/upload, GPU (NVIDIA), disk read/write and space used, battery, and uptime. Values update every second and the charts cover the last 2 minutes.

## Requirements

- Linux with Python 3 and `psutil` (`sudo apt install python3-psutil` or `pip install psutil`)
- Optional: `nvidia-smi` for GPU stats (the GPU tile shows "not available" without it)

## Run manually

```bash
python3 server.py            # listens on port 8765
PORT=9000 python3 server.py  # use a different port
```

Open `http://<pc-ip>:8765` on the iPad. Find the PC's IP with `hostname -I`.

If the page doesn't load, open the port in the firewall:

```bash
sudo ufw allow 8765/tcp
```

## iPad tips

- **Full screen:** in Safari tap Share → *Add to Home Screen*, then open it from that icon.
- **Theme:** the button in the top-right corner switches between dark and light; the choice is remembered in that browser.
- **Keep the screen on:** Settings → Display & Brightness → Auto-Lock → *Never*.

## Start automatically (systemd user service)

### Add

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/pc-dashboard.service <<EOF
[Unit]
Description=PC Dashboard (LAN system monitor)
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 $(pwd)/server.py
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now pc-dashboard
```

Run this from the project folder: `$(pwd)` fills in the path to `server.py`.

This starts the dashboard when you log in. To start it at boot, before anyone logs in:

```bash
sudo loginctl enable-linger $USER
```

### Check

```bash
systemctl --user status pc-dashboard
journalctl --user -u pc-dashboard -f     # logs
systemctl --user restart pc-dashboard    # after editing server.py
```

### Remove

```bash
systemctl --user disable --now pc-dashboard
rm ~/.config/systemd/user/pc-dashboard.service
systemctl --user daemon-reload
sudo loginctl disable-linger $USER       # only if you enabled linger
```
