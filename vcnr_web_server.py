import argparse
import functools
import http.server
import os
import socket
import threading
import webbrowser


class VCNRHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".vcnr": "application/octet-stream",
        ".js": "text/javascript; charset=utf-8",
    }

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()


def _discover_lan_addresses():
    addresses = []

    def add(address):
        if address and address not in addresses and not address.startswith("127."):
            addresses.append(address)

    try:
        hostname = socket.gethostname()
        for entry in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            add(entry[4][0])
    except OSError:
        pass

    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        if probe is not None:
            probe.close()

    return addresses


def _advertised_urls(host, port):
    if host == "0.0.0.0":
        hosts = ["127.0.0.1", *_discover_lan_addresses()]
    else:
        hosts = [host]
    return [f"http://{item}:{port}/web_player/" for item in hosts]


def main():
    parser = argparse.ArgumentParser(description="Run the VCNR browser player")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address. Use 0.0.0.0 to allow other devices to open the player.",
    )
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument(
        "--open-url",
        help="Optional URL to open in the local browser instead of the default local address.",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    handler = functools.partial(VCNRHandler, directory=root)
    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    urls = _advertised_urls(args.host, server.server_port)
    open_url = args.open_url or urls[0]
    print("VCNR browser player URLs:")
    for url in urls:
        print(f"  {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(open_url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
