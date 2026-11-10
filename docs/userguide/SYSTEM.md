# System page

A live status screen of the PC. Values update every second; the charts cover the last 2 minutes.

| Tile | Shows |
|---|---|
| CPU | total use, each core, clock, load average |
| CPU temperature | package temperature with a status color, fan speed, NVMe (SSD) temperature |
| Memory | RAM used, swap |
| GPU | NVIDIA GPU use, temperature, memory (*not available* without `nvidia-smi`) |
| Disk | read / write speed, space used |
| Network | download / upload (physical network cards only) |
| Header | host name, uptime, battery, clock |

**Top 5 processes:** tap the CPU or Memory tile to switch between the chart and the 5 processes using the most CPU / RAM. Processes with the same name are added together (e.g. all `chrome` processes).

The stats stream only runs while this page is open, so the iPad's Wi-Fi can sleep on the Tasks page.
