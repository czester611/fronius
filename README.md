## Use systemd — It's the most efficient way to provide autorun in Raspberry Pi.
Create service file:
```
bashsudo nano /etc/systemd/system/fronius-lcd.service
```
paste following settings:

```
ini[Unit]
Description=Fronius LCD Display
After=network.target

[Service]
ExecStart=/usr/bin/python /home/pi/fronius/fronius_lcd.py
WorkingDirectory=/home/pi/fronius
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=5
User=pi

[Install]
WantedBy=multi-user.target
```

Turn on service:
```
bashsudo systemctl daemon-reload
sudo systemctl enable fronius-lcd
sudo systemctl start fronius-lcd
```

Check if it's work:
```
bashsudo systemctl status fronius-lcd
```
Usefull commands:
```
bashsudo systemctl stop fronius-lcd      # zatrzymaj
sudo systemctl restart fronius-lcd   # restart
journalctl -u fronius-lcd -f         # podgląd logów na żywo

    python3 -m venv venv
    source venv/bin/activate

```
Restart=always i RestartSec=5 Provide way to auto restart frozen script
