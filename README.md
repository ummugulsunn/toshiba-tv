# toshiba-tv

**Mirror your Mac's screen *and* audio to a non-Android Toshiba / Vestel smart TV, wirelessly.**
No dongle, no driver, nothing installed on the TV. Just Python, ffmpeg and a tiny Swift helper.

[Türkçe açıklama aşağıda ↓](#türkçe)

Tested on a Toshiba 50UL2063DTA (Vestel Linux firmware, 2020). It should work on other Vestel-made
TVs that have DLNA "Audio Video Sharing" (Toshiba UL series, Vestel, Telefunken, Hitachi, JVC, Finlux,
Panasonic/Sharp models built by Vestel…). If it works on yours, open an issue or PR so we can list it.

## Why this exists

These TVs are "smart" but not Android: no Chromecast, no AirPlay, no ADB, so tools like
[cleanyourtv](https://github.com/Gathin23/cleanyourtv) don't apply. They do have a DLNA renderer,
but on this firmware **pushing** a video to it (DLNA push / "Play To") shows a black screen.
What *does* work is the TV's own media browser **pulling** from a DLNA media server.

So `tvscreen.py` pretends to be a DLNA media server called **"Mac Ekranı"** whose only item is a live
stream of your screen and system audio:

```
maccapture (ScreenCaptureKit) ──NV12 video + f32 audio──▶ ffmpeg (H.264 + AAC → MPEG-TS)
      ──HTTP──▶ TV media browser  ◀── SSDP + UPnP ContentDirectory ── tvscreen.py
```

## Usage

Requirements: macOS 14+, Python 3, ffmpeg (`brew install ffmpeg`), Xcode command line tools (`swiftc`).

```bash
git clone https://github.com/ummugulsunn/toshiba-tv && cd toshiba-tv
python3 tvscreen.py            # 720p, 10 Mbps
python3 tvscreen.py 1080       # 1080p
python3 tvscreen.py 720 6      # lower bitrate if your Wi-Fi stutters
```

The first run compiles `maccapture.swift` and macOS asks for **Screen Recording** permission for your
terminal. On the TV: **Source** → **Mac Ekranı** → **Mac Ekranı (canlı)**. Stop with Ctrl+C.

- **Audio**: everything playing on the Mac goes to the TV. You can mute the Mac; capture still works.
- **Latency**: ~1–2 s. Most of it is the TV player's own buffer, which can't be controlled from the
  Mac. Fine for films and presentations, not for fast mouse work. A constant, padded bitrate keeps it
  this low; pushing the bitrate too high (e.g. 20 Mbps) can saturate Wi-Fi and make it worse.
- The TV only accepts a 16:9 frame at a fixed frame rate (otherwise: black screen), so a 16:10 Mac
  screen gets black bars.

## Other tools

| File | What it does |
|---|---|
| `tvcast.py` | Controls the TV's DLNA renderer: volume, mute, status, push a file (push shows no picture on this TV, audio-only use) |
| `tvremote.py` | Network remote on port 4660 (arrow keys, OK, volume…). Enable **Virtual Remote** in the TV settings first. Key codes from [this post](https://shkspr.mobi/blog/2018/11/telnet-control-of-toshiba-smart-tvs/); untested on this model. |
| `minidlna/minidlna.conf` | Optional: share a `media/` folder so the TV can play your files (`brew install minidlna`). Don't run it together with `tvscreen.py` — they both use the SSDP port. |

The TV is found automatically over SSDP; set `TV_IP=…` if you have several DLNA renderers.

## Troubleshooting

- **"Mac Ekranı" doesn't show up on the TV**: Mac and TV must be on the same network; enable
  *Audio Video Sharing* in the TV's network settings; make sure nothing else (minidlna) holds UDP 1900.
- **Black screen**: make sure the TV is actually on (it answers on the network in standby too).
- **No picture or audio at all**: grant Screen Recording to your terminal in System Settings → Privacy &
  Security, then restart the terminal.

## Türkçe

Mac ekranını **ve sesini**, Android olmayan Toshiba / Vestel akıllı TV'lere kablosuz yansıtır.
Ek cihaz, sürücü ya da TV'ye uygulama kurmak gerekmez.

Bu TV'ler DLNA ile dışarıdan gönderilen videoyu ekrana getirmiyor, ama kendi menüsünden açılan
medya sunucularını oynatıyor. `tvscreen.py`, Mac'i **"Mac Ekranı"** adında bir medya sunucusu gibi
tanıtıyor; içindeki tek öğe ekranın ve sesin canlı yayını.

```bash
python3 tvscreen.py            # 720p, 10 Mbps
python3 tvscreen.py 720 6      # Wi-Fi zayıfsa / görüntü takılıyorsa
```

TV'de: **Kaynak** → **Mac Ekranı** → **Mac Ekranı (canlı)**. İlk çalıştırmada Terminal'e
**Ekran Kaydı** izni vermen gerekir. Gecikme ~1-2 sn; Mac'te çalan her ses TV'ye gider,
Mac'i sessize alabilirsin.

## License

MIT
