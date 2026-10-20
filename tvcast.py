#!/usr/bin/env python3
"""Toshiba (Vestel) TV'yi Mac'ten DLNA ile kontrol et.

  python3 tvcast.py play  <dosya | http-url>   # video/müzik/foto gönder
  python3 tvcast.py pause | resume | stop | status
  python3 tvcast.py vol [0-100]                # sesi göster / ayarla
  python3 tvcast.py mute | unmute
"""
import http.server, mimetypes, os, socket, socketserver, sys, threading, time, urllib.parse, urllib.request
from xml.sax.saxutils import escape

PORT = 8765
_renderer = None


def find_tv():
    """TV'nin DLNA alıcısını ağda bul: (TV IP, açıklama URL'si). TV_IP ortam değişkeni aramayı daraltır."""
    want = os.environ.get("TV_IP")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    s.sendto(b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 2\r\n'
             b'ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n', ("239.255.255.250", 1900))
    try:
        while True:
            data, (ip, _) = s.recvfrom(4096)
            loc = next((l.split(":", 1)[1].strip() for l in data.decode(errors="ignore").splitlines()
                        if l.upper().startswith("LOCATION:")), None)
            if loc and (not want or ip == want):
                return ip, loc
    except socket.timeout:
        sys.exit("Ağda DLNA destekli TV bulunamadı. TV açık ve aynı Wi-Fi'de mi? (TV_IP=... ile belirtebilirsin)")


def renderer():
    """(TV IP, {servis: kontrol URL'si}) — bir kere bulunur, sonra hatırlanır."""
    global _renderer
    if _renderer is None:
        ip, loc = find_tv()
        desc = urllib.request.urlopen(loc, timeout=5).read().decode(errors="ignore")
        urls = {}
        for svc in desc.split("<service>")[1:]:
            name = tag(svc, "serviceType").split(":")[-2]
            urls[name] = urllib.parse.urljoin(loc, tag(svc, "controlURL"))
        _renderer = ip, urls
    return _renderer


def soap(service, action, args="", quiet=False):
    body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="urn:schemas-upnp-org:service:{service}:1">{args}</u:{action}>'
            f'</s:Body></s:Envelope>').encode()
    req = urllib.request.Request(renderer()[1][service], body, {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": f'"urn:schemas-upnp-org:service:{service}:1#{action}"'})
    try:
        return urllib.request.urlopen(req, timeout=8).read().decode(errors="ignore")
    except (TimeoutError, urllib.error.URLError) as e:
        if quiet:
            return ""
        if not isinstance(e, urllib.error.HTTPError):
            sys.exit(f"TV yanıt vermedi: {e}")
        sys.exit(f"TV hata döndü ({e.code}): {e.read().decode(errors='ignore')[:300]}")


def tag(xml, name):
    a = xml.find(f"<{name}>")
    return xml[a + len(name) + 2: xml.find(f"</{name}>")] if a >= 0 else "?"


def my_ip():
    """Mac'in yerel ağ IP'si (UDP connect paket göndermez, sadece yönlendirmeyi sorar)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 1))
    return s.getsockname()[0]


def dlna_class(mime):
    return {"video": "object.item.videoItem", "audio": "object.item.audioItem.musicTrack",
            "image": "object.item.imageItem.photo"}.get(mime.split("/")[0], "object.item")


def serve(path):
    """Dosyayı (Range desteğiyle) HTTP'den sun; TV buradan çeker."""
    d, name = os.path.split(os.path.abspath(path))

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(s, *a, **k): super().__init__(*a, directory=d, **k)
        def log_message(s, *a): pass
        def end_headers(s):
            s.send_header("transferMode.dlna.org", "Streaming")
            s.send_header("contentFeatures.dlna.org", "DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000")
            super().end_headers()
        def send_head(s):
            rng = s.headers.get("Range")
            fp = os.path.join(d, urllib.parse.unquote(s.path.lstrip("/")))
            if not rng or not os.path.isfile(fp):
                return super().send_head()
            size = os.path.getsize(fp)
            start, _, end = rng.replace("bytes=", "").partition("-")
            start, end = int(start or 0), int(end) if end else size - 1
            f = open(fp, "rb"); f.seek(start)
            s.send_response(206)
            s.send_header("Content-Type", s.guess_type(fp))
            s.send_header("Accept-Ranges", "bytes")
            s.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            s.send_header("Content-Length", str(end - start + 1))
            s.end_headers()
            s._left = end - start + 1
            return f
        def copyfile(s, src, dst):
            left = getattr(s, "_left", None)
            while left is None or left > 0:
                buf = src.read(65536 if left is None else min(65536, left))
                if not buf: break
                try: dst.write(buf)
                except (BrokenPipeError, ConnectionResetError): break
                if left is not None: left -= len(buf)

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://{my_ip()}:{PORT}/{urllib.parse.quote(name)}"


def play(src):
    local = not src.startswith("http")
    url = serve(src) if local else src
    mime = mimetypes.guess_type(src)[0] or "video/mp4"
    didl = (f'<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            f'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><item id="1" parentID="0" restricted="1">'
            f'<dc:title>{escape(os.path.basename(src))}</dc:title><upnp:class>{dlna_class(mime)}</upnp:class>'
            f'<res protocolInfo="http-get:*:{mime}:*">{escape(url)}</res></item></DIDL-Lite>')
    soap("AVTransport", "Stop", "<InstanceID>0</InstanceID>", quiet=True)
    soap("AVTransport", "SetAVTransportURI",
         f"<InstanceID>0</InstanceID><CurrentURI>{escape(url)}</CurrentURI><CurrentURIMetaData>{escape(didl)}</CurrentURIMetaData>")
    soap("AVTransport", "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>")
    print(f"TV'ye gönderildi: {url}")
    if local:
        print("Oynatılırken bu pencere açık kalmalı. Bitirmek için Ctrl+C.")
        try:
            while True:
                time.sleep(5)
                if tag(soap("AVTransport", "GetTransportInfo", "<InstanceID>0</InstanceID>"),
                       "CurrentTransportState") in ("STOPPED", "NO_MEDIA_PRESENT") and not mime.startswith("image"):
                    break
        except KeyboardInterrupt:
            pass


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, arg = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
    i = "<InstanceID>0</InstanceID>"
    if cmd == "play" and arg: play(arg)
    elif cmd == "pause": soap("AVTransport", "Pause", i)
    elif cmd == "resume": soap("AVTransport", "Play", i + "<Speed>1</Speed>")
    elif cmd == "stop": soap("AVTransport", "Stop", i)
    elif cmd == "status":
        t = soap("AVTransport", "GetTransportInfo", i)
        p = soap("AVTransport", "GetPositionInfo", i)
        v = soap("RenderingControl", "GetVolume", i + "<Channel>Master</Channel>")
        print(f"Durum: {tag(t, 'CurrentTransportState')}  Konum: {tag(p, 'RelTime')}/{tag(p, 'TrackDuration')}  Ses: {tag(v, 'CurrentVolume')}")
    elif cmd == "vol" and arg:
        soap("RenderingControl", "SetVolume", i + f"<Channel>Master</Channel><DesiredVolume>{int(arg)}</DesiredVolume>")
    elif cmd == "vol":
        print(tag(soap("RenderingControl", "GetVolume", i + "<Channel>Master</Channel>"), "CurrentVolume"))
    elif cmd in ("mute", "unmute"):
        soap("RenderingControl", "SetMute", i + f"<Channel>Master</Channel><DesiredMute>{int(cmd == 'mute')}</DesiredMute>")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
