#!/usr/bin/env python3
"""Mac ekranını Toshiba TV'ye yansıt.

Bu TV dışarıdan gönderilen videoyu (DLNA push) ekrana getirmiyor, ama kendi açtığı
DLNA sunucularını oynatıyor. Bu yüzden Mac, "Mac Ekranı" adında bir medya sunucusu
gibi görünür; TV'de Kaynak → "Mac Ekranı" → "Mac Ekranı (canlı)" açılınca ekran gelir.

  python3 tvscreen.py            # 1280x720, 30 fps
  python3 tvscreen.py 1080       # 1920x1080 (daha net, biraz daha gecikmeli)
  python3 tvscreen.py 720 16     # bit hızı (Mbps): yüksek = daha az gecikme, Wi-Fi zayıfsa takılır
Durdurmak için Ctrl+C.
"""
import http.server, os, shutil, socket, socketserver, struct, subprocess, sys, tempfile, threading, time
from xml.sax.saxutils import escape

from tvcast import my_ip, PORT as CAST_PORT

PORT = CAST_PORT + 1
HEIGHT = int(sys.argv[1]) if len(sys.argv) > 1 else 720
BITRATE = int(sys.argv[2]) if len(sys.argv) > 2 else 10  # Mbps; takılırsa düşür, gecikirse artır
MIME = "video/mpeg"
NAME = "Mac Ekranı"
UUID = "uuid:5f1c2a9e-7b3d-4e8a-9c61-0d2f3b4a5e6f"
SSDP = ("239.255.255.250", 1900)
DEVICE = "urn:schemas-upnp-org:device:MediaServer:1"
CDS = "urn:schemas-upnp-org:service:ContentDirectory:1"
CMS = "urn:schemas-upnp-org:service:ConnectionManager:1"
IP = my_ip()
BASE = f"http://{IP}:{PORT}"


HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(HERE, "maccapture")
# Mac ekranı 16:10; TV standart 16:9 boyut istiyor, yoksa siyah kalıyor (maccapture kenarları siyah bırakır).
WIDTH = HEIGHT * 16 // 9


def build_capture():
    """Ekranı ve sistem sesini yakalayan yardımcıyı (maccapture.swift) gerekirse derle."""
    src = CAPTURE + ".swift"
    if os.path.exists(CAPTURE) and os.path.getmtime(CAPTURE) >= os.path.getmtime(src):
        return
    print("Yakalama yardımcısı derleniyor (ilk seferde ~30 sn)...", flush=True)
    if subprocess.run(["swiftc", "-O", src, "-o", CAPTURE]).returncode != 0:
        sys.exit("maccapture derlenemedi (Xcode komut satırı araçları kurulu mu?)")


def ffmpeg_cmd(audio_fifo):
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        # Görüntü maccapture'ın stdout'undan (NV12, 30 fps), ses ayrı bir fifo'dan gelir.
        "-thread_queue_size", "512",
        "-f", "rawvideo", "-pixel_format", "nv12", "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", "30", "-i", "pipe:0",
        "-thread_queue_size", "512", "-f", "f32le", "-ar", "48000", "-ac", "2", "-i", audio_fifo,
        "-vf", "format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        # TV oynatmadan önce sabit miktarda veri biriktiriyor: yüksek, sabit bit hızı (boş paketlerle
        # doldurulmuş) bu tamponu hızlı doldurur ve gecikmeyi kısaltır.
        "-profile:v", "high", "-level", "4.1",
        "-b:v", f"{BITRATE}M", "-minrate", f"{BITRATE}M", "-maxrate", f"{BITRATE}M", "-bufsize", f"{BITRATE // 4}M",
        "-x264-params", "nal-hrd=cbr",
        "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "128k",
        "-f", "mpegts", "-muxrate", f"{BITRATE + 4}M", "-muxdelay", "0.1", "-flush_packets", "1", "pipe:1",
    ]


DESC = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><specVersion><major>1</major><minor>0</minor></specVersion>
<device><deviceType>{DEVICE}</deviceType><friendlyName>{NAME}</friendlyName>
<manufacturer>tvscreen</manufacturer><modelName>tvscreen</modelName><modelNumber>1</modelNumber><UDN>{UUID}</UDN>
<serviceList>
<service><serviceType>{CDS}</serviceType><serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
<SCPDURL>/cds.xml</SCPDURL><controlURL>/cds/control</controlURL><eventSubURL>/cds/event</eventSubURL></service>
<service><serviceType>{CMS}</serviceType><serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
<SCPDURL>/cms.xml</SCPDURL><controlURL>/cms/control</controlURL><eventSubURL>/cms/event</eventSubURL></service>
</serviceList></device></root>"""


def scpd(actions):
    acts = "".join(f"<action><name>{a}</name></action>" for a in actions)
    return (f'<?xml version="1.0"?><scpd xmlns="urn:schemas-upnp-org:service-1-0"><specVersion><major>1</major>'
            f'<minor>0</minor></specVersion><actionList>{acts}</actionList><serviceStateTable/></scpd>')


ITEM = (f'<item id="1" parentID="0" restricted="1"><dc:title>{NAME} (canlı)</dc:title>'
        f'<upnp:class>object.item.videoItem</upnp:class>'
        f'<res protocolInfo="http-get:*:{MIME}:DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000">'
        f'{BASE}/screen.ts</res></item>')
ROOT = f'<container id="0" parentID="-1" restricted="1" childCount="1"><dc:title>{NAME}</dc:title><upnp:class>object.container.storageFolder</upnp:class></container>'


def didl(inner):
    return ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            f'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">{inner}</DIDL-Lite>')


def soap_reply(service, action, body):
    return (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action}Response xmlns:u="{service}">{body}</u:{action}Response></s:Body></s:Envelope>')


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a): pass

    def _send(self, body, ctype='text/xml; charset="utf-8"'):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream_headers(self):
        # TV boyutu bilinmeyen yayını istemiyor: canlı yayını çok büyük bir dosya gibi göster.
        size = 10 ** 11
        if self.headers.get("Range"):
            self.send_response(206)
            self.send_header("Content-Range", f"bytes 0-{size - 1}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", MIME)
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("contentFeatures.dlna.org", "DLNA.ORG_OP=00;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_HEAD(self):
        if self.path.startswith("/screen.ts"):
            self._stream_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):
        if self.path == "/desc.xml":
            return self._send(DESC)
        if self.path == "/cds.xml":
            return self._send(scpd(["Browse", "GetSystemUpdateID", "GetSortCapabilities", "GetSearchCapabilities"]))
        if self.path == "/cms.xml":
            return self._send(scpd(["GetProtocolInfo", "GetCurrentConnectionIDs", "GetCurrentConnectionInfo"]))
        if not self.path.startswith("/screen.ts"):
            self.send_error(404)
            return
        self.close_connection = True
        self._stream_headers()
        print(f"TV yayını açtı ({self.headers.get('Range', 'baştan')}).", flush=True)
        tmp = tempfile.mkdtemp(prefix="tvscreen")
        fifo = os.path.join(tmp, "audio")
        os.mkfifo(fifo)
        cap = subprocess.Popen([CAPTURE, str(WIDTH), str(HEIGHT), fifo], stdout=subprocess.PIPE)
        proc = subprocess.Popen(ffmpeg_cmd(fifo), stdin=cap.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        cap.stdout.close()  # artık sadece ffmpeg okuyor
        try:
            while buf := proc.stdout.read1(65536):
                self.wfile.write(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.kill()
            cap.kill()
            shutil.rmtree(tmp, ignore_errors=True)
            print("TV yayını kapattı.", flush=True)

    def do_SUBSCRIBE(self):
        self.send_response(200)
        self.send_header("SID", f"{UUID}-sub")
        self.send_header("TIMEOUT", "Second-1800")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_UNSUBSCRIBE(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode(errors="ignore")
        action = self.headers.get("SOAPACTION", "").strip('"').split("#")[-1]
        if self.path == "/cds/control":
            if action == "Browse":
                if "BrowseMetadata" in body:
                    result = didl(ITEM if "<ObjectID>1</ObjectID>" in body else ROOT)
                else:
                    result = didl(ITEM) if "<ObjectID>0</ObjectID>" in body else didl("")
                n = result.count("<item") + result.count("<container")
                return self._send(soap_reply(CDS, action, f"<Result>{escape(result)}</Result><NumberReturned>{n}</NumberReturned>"
                                                            f"<TotalMatches>{n}</TotalMatches><UpdateID>1</UpdateID>"))
            if action == "GetSystemUpdateID":
                return self._send(soap_reply(CDS, action, "<Id>1</Id>"))
            if action == "GetSortCapabilities":
                return self._send(soap_reply(CDS, action, "<SortCaps></SortCaps>"))
            if action == "GetSearchCapabilities":
                return self._send(soap_reply(CDS, action, "<SearchCaps></SearchCaps>"))
        if self.path == "/cms/control":
            if action == "GetProtocolInfo":
                return self._send(soap_reply(CMS, action, f"<Source>http-get:*:{MIME}:*</Source><Sink></Sink>"))
            if action == "GetCurrentConnectionIDs":
                return self._send(soap_reply(CMS, action, "<ConnectionIDs>0</ConnectionIDs>"))
        self.send_error(401)


def ssdp_headers(st):
    usn = UUID if st == UUID else f"{UUID}::{st}"
    return f"CACHE-CONTROL: max-age=1800\r\nLOCATION: {BASE}/desc.xml\r\nSERVER: macOS/1 UPnP/1.0 tvscreen/1\r\nUSN: {usn}\r\n"


TYPES = ["upnp:rootdevice", UUID, DEVICE, CDS, CMS]


def ssdp_listen():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    try:
        s.bind(("", SSDP[1]))
    except OSError:
        print("Uyarı: SSDP portu meşgul, sadece duyuru yapılacak.")
        return
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("4s4s", socket.inet_aton(SSDP[0]), socket.inet_aton(IP)))
    while True:
        data, addr = s.recvfrom(4096)
        msg = data.decode(errors="ignore")
        if not msg.startswith("M-SEARCH"):
            continue
        st = next((l.split(":", 1)[1].strip() for l in msg.splitlines() if l.upper().startswith("ST:")), "")
        for t in TYPES if st == "ssdp:all" else [st] if st in TYPES else []:
            s.sendto(f"HTTP/1.1 200 OK\r\n{ssdp_headers(t)}EXT:\r\nST: {t}\r\n\r\n".encode(), addr)


def ssdp_notify(nts="ssdp:alive"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(IP))
    for t in TYPES:
        s.sendto(f"NOTIFY * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n{ssdp_headers(t)}NT: {t}\r\nNTS: {nts}\r\n\r\n".encode(), SSDP)


def main():
    build_capture()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer.daemon_threads = True
    srv = socketserver.ThreadingTCPServer(("", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=ssdp_listen, daemon=True).start()
    print(f"Hazır. TV'de Kaynak → \"{NAME}\" → \"{NAME} (canlı)\" aç.  (durdurmak için Ctrl+C)", flush=True)
    try:
        while True:
            ssdp_notify()
            time.sleep(30)
    except KeyboardInterrupt:
        ssdp_notify("ssdp:byebye")


if __name__ == "__main__":
    main()
