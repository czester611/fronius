Najlepiej użyć systemd — to najbardziej niezawodny sposób na autostart w Raspberry Pi.
Utwórz plik usługi:
bashsudo nano /etc/systemd/system/fronius-lcd.service
Wklej zawartość:
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
Następnie włącz i uruchom usługę:
bashsudo systemctl daemon-reload
sudo systemctl enable fronius-lcd
sudo systemctl start fronius-lcd
Sprawdź czy działa:
bashsudo systemctl status fronius-lcd
Przydatne komendy na przyszłość:
bashsudo systemctl stop fronius-lcd      # zatrzymaj
sudo systemctl restart fronius-lcd   # restart
journalctl -u fronius-lcd -f         # podgląd logów na żywo
Restart=always i RestartSec=5 sprawiają że jeśli skrypt się wysypie (np. brak sieci przy starcie), systemd automatycznie spróbuje go uruchomić ponownie po 5 sekundach.
