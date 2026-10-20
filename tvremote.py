#!/usr/bin/env python3
"""Toshiba (Vestel) TV için Mac'ten kumanda (port 4660, "Sanal Kumanda" açık olmalı).

  python3 tvremote.py up down ok back      # tuşları sırayla bas
  python3 tvremote.py                      # klavyeyle canlı kumanda
"""
import socket, sys, termios, time, tty

from tvcast import find_tv

PORT = 4660
KEYS = {
    "power": 1012, "up": 1020, "down": 1019, "left": 1021, "right": 1022, "ok": 1053,
    "back": 1010, "exit": 1037, "volup": 1016, "voldown": 1017, "mute": 1013,
    "chup": 1032, "chdown": 1033, "menu": 1048, "home": 1048, "source": 1056, "info": 1015,
    "red": 1055, "green": 1054, "yellow": 1050, "blue": 1052,
    **{str(n): 1000 + n for n in range(10)},
}


def send(sock, name):
    code = KEYS[name] if name in KEYS else int(name)
    sock.sendall(f"{code}\r\n".encode())


def live(sock):
    arrows = {"A": "up", "B": "down", "C": "right", "D": "left"}
    keymap = {"\r": "ok", "\x7f": "back", "+": "volup", "-": "voldown", "m": "mute",
              "h": "home", "s": "source", "p": "power", "e": "exit", "i": "info",
              "]": "chup", "[": "chdown", **{str(n): str(n) for n in range(10)}}
    print("Oklar: yön | Enter: OK | Backspace: geri | +/-: ses | m: sessiz | h: menü | "
          "s: kaynak | [ ]: kanal | p: güç | q: çık")
    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin)
        while (c := sys.stdin.read(1)) != "q":
            if c == "\x1b" and sys.stdin.read(1) == "[":
                name = arrows.get(sys.stdin.read(1))
            else:
                name = keymap.get(c)
            if name:
                send(sock, name)
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


def main():
    TV = find_tv()[0]
    try:
        sock = socket.create_connection((TV, PORT), timeout=3)
    except OSError:
        sys.exit(f"{TV}:{PORT} kapalı. TV açık mı ve Ayarlar'da 'Sanal Kumanda' açık mı?")
    with sock:
        if len(sys.argv) > 1:
            for name in sys.argv[1:]:
                send(sock, name)
                time.sleep(0.3)
        else:
            live(sock)


if __name__ == "__main__":
    main()
