"""Painel local para F1 24. Sem dependencias externas; Python 3.9+."""
import argparse
import ipaddress
import json
import os
import re
import secrets
import select
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from conectar import qr_svg
from configuracao import config_file
from i18n import LanguageStore, translate

from f1_24 import Receiver, SIZES, demo_packet
from radio import Radio


def persistent_token(config_path=None):
    """Keep the access code across restarts, demo/live mode, and ZIP upgrades."""
    if config_path is None:
        config_path = config_file('acesso.json')
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with config_path.open('x', encoding='utf-8') as config:
            token = secrets.token_urlsafe(18)
            json.dump({'access_code': token}, config)
            return token
    except FileExistsError:
        try:
            token = json.loads(config_path.read_text(encoding='utf-8'))['access_code']
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f'Configuracao de acesso invalida: {config_path}. Envie este erro para reparo.') from exc
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', token):
            raise ValueError(f'Codigo de acesso invalido em {config_path}. Envie este erro para reparo.')
        return token


def local_addresses():
    candidates = set()
    try:
        candidates.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    # Choosing a route does not send a datagram or contact this address.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(('192.168.0.1', 9))
            candidates.add(probe.getsockname()[0])
        except OSError:
            pass
    return sorted(x for x in candidates if ipaddress.ip_address(x).is_private
                  and not ipaddress.ip_address(x).is_loopback
                  and not ipaddress.ip_address(x).is_link_local)


class Telemetry:
    def __init__(self, demo):
        self.receiver = Receiver()
        self.demo = demo
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.error = None
        self.udp = None
        if not demo:
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                self.udp.bind(('127.0.0.1', 20777))
                self.udp.setblocking(False)
            except OSError:
                self.udp.close()
                raise
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        tick = 0
        try:
            while not self.stop.is_set():
                if self.demo:
                    with self.lock:
                        for pid in SIZES:
                            self.receiver.feed(demo_packet(pid, tick))
                    tick += 1
                    self.stop.wait(0.25)
                elif select.select([self.udp], [], [], 0.1)[0]:
                    for _ in range(256):
                        try:
                            data, _ = self.udp.recvfrom(4096)
                        except BlockingIOError:
                            break
                        with self.lock:
                            self.receiver.feed(data)
        except OSError as exc:
            with self.lock:
                self.error = str(exc)

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        if self.udp:
            self.udp.close()

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            r = self.receiver
            groups = [dict(r.fresh(p, now)) for p in (1, 2, 6, 7, 10)]
            s, lap, car, status, damage = groups
            alerts = []
            race = s.get('session_type') in (15, 16, 17)
            if race and status and status['margin'] < 0:
                alerts.append({'level': 'warning', 'text': 'Combustível abaixo da previsão do jogo. Considere economizar.'})
            wear = damage.get('wear', [])
            if wear and max(wear) >= 60:
                alerts.append({'level': 'warning', 'text': 'Desgaste acima de 60%. Avalie os pneus e o ritmo; este limite é experimental.'})
            if damage and max(damage['wing']) > 0:
                alerts.append({'level': 'warning', 'text': 'Dano de asa detectado. Avalie a perda de desempenho.'})
            if self.error:
                alerts.append({'level': 'warning', 'text': 'Recepção de telemetria interrompida. Confira o terminal no PC.'})
            return {'demo': self.demo, 'connected': bool(car) and not self.error,
                    'session_uid': str(r.uid) if r.uid is not None else None,
                    'packets': r.good, 'session': s, 'lap': lap, 'car': car,
                    'status': status, 'damage': damage, 'alerts': alerts,
                    'race': race}


def handler_for(telemetry, token, html, radio=None, language=None):
    root = '/' + token + '/'
    addresses=local_addresses()
    language=language or LanguageStore()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in (root + 'radio', root + 'settings', root + 'language') or radio is None:
                self.send_error(404)
                return
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= (16384 if path == root + 'settings' else 256):
                    self.send_error(400)
                    return
                self.connection.settimeout(3)
                request = json.loads(self.rfile.read(length))
                if path == root + 'language':
                    language.set(request['language'])
                    radio.interrupt()
                    message='Language saved / Idioma salvo.'
                else:
                    message = radio.configure(request['settings']) if path == root + 'settings' else radio.command(request['action'])
            except (ValueError, KeyError, TypeError, OSError):
                self.send_error(400)
                return
            body = json.dumps({'message': translate(message,language.get())}, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # No directory browsing, remote control, file writes, or external resources.
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == root:
                selected=language.get()
                page=translate(html.decode('utf-8'),selected).replace('__APP_LANGUAGE__',selected)
                if selected=='en':
                    for source,target in [('TE','RL'),('TD','RR'),('DE','FL'),('DD','FR')]:
                        page=page.replace("['"+source+"',","['"+target+"',")
                body=page.encode('utf-8')
                mime = 'text/html; charset=utf-8'
            elif path == root + 'logo.png':
                body = Path(__file__).with_name('assets').joinpath('pitsignal-logo.png').read_bytes()
                mime = 'image/png'
            elif path == root + 'connect':
                port=self.server.server_address[1]
                links=[{'ip':address,'url':f'http://{address}:{port}{root}'} for address in addresses]
                body=json.dumps({'links':links},ensure_ascii=False).encode('utf-8')
                mime='application/json; charset=utf-8'
            elif path == root + 'qr.svg':
                try:
                    index=int(parse_qs(urlsplit(self.path).query).get('index',['0'])[0])
                    if not 0<=index<len(addresses):raise ValueError()
                except ValueError:
                    self.send_error(404)
                    return
                port=self.server.server_address[1]
                body=qr_svg(f'http://{addresses[index]}:{port}{root}')
                mime='image/svg+xml; charset=utf-8'
            elif path == root + 'api':
                snapshot = telemetry.snapshot()
                if radio:
                    snapshot['radio'] = radio.snapshot()
                    snapshot['thermal'] = radio.thermal_snapshot()
                snapshot['language']=language.get()
                body = json.dumps(translate(snapshot,language.get()), ensure_ascii=False, allow_nan=False).encode('utf-8')
                mime = 'application/json; charset=utf-8'
            elif path == root + 'settings' and radio:
                body = json.dumps(translate(radio.settings_snapshot(),language.get()), ensure_ascii=False).encode('utf-8')
                mime = 'application/json; charset=utf-8'
            else:
                body, mime = b'Endereco invalido. Use o link exibido no terminal do PC.', 'text/plain; charset=utf-8'
                self.send_response(404)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

    return Handler


def main():
    language=LanguageStore()
    def emit(message,**kwargs):
        print(translate(message,language.get()),**kwargs)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--http-port', type=int, default=8765)
    parser.add_argument('--seconds', type=float, default=0, help='Duracao de teste; 0 = continuo.')
    args = parser.parse_args()
    telemetry = None
    server = None
    radio = None
    try:
        html = Path(__file__).with_name('painel.html').read_bytes()
        telemetry = Telemetry(args.demo)
        token = persistent_token()
        radio = Radio(telemetry.snapshot, language=language)
        server = ThreadingHTTPServer(('0.0.0.0', args.http_port), handler_for(telemetry, token, html, radio, language))
        server.daemon_threads = True
        server.timeout = 0.25
        telemetry.thread.start()
        radio.thread.start()
        url = f'http://127.0.0.1:{args.http_port}/{token}/'
        emit('\nPitSignal - PAINEL NO NAVEGADOR', flush=True)
        emit('MODO: ' + ('DEMONSTRACAO (dados ficticios)' if args.demo else 'TELEMETRIA REAL DO F1 24'), flush=True)
        emit('\nNO PC: ' + url, flush=True)
        emit('\nNO CELULAR/TABLET (mesmo Wi-Fi/rede):', flush=True)
        addresses = local_addresses()
        for address in addresses:
            emit(f'  http://{address}:{args.http_port}/{token}/', flush=True)
        if not addresses:
            emit(f'  Use o IPv4 do PC no lugar de 127.0.0.1 no link acima (ipconfig).', flush=True)
        emit('\nSe o Firewall do Windows perguntar, permita Python apenas em redes privadas.', flush=True)
        emit('Codigo de acesso salvo: o mesmo link vale para demonstracao e jogo real.', flush=True)
        emit('Salve o link no tablet. Se o IP do PC mudar, atualize o endereco.', flush=True)
        emit('Mantenha esta janela aberta. Ctrl+C encerra.\n', flush=True)
        emit('RADIO: o audio sai pelo PC. Use Testar radio no painel para conferir o fone.', flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        start = time.monotonic()
        while not args.seconds or time.monotonic() - start < args.seconds:
            server.handle_request()
    except KeyboardInterrupt:
        emit('\nPainel encerrado.')
    except (OSError, ValueError) as exc:
        emit(f'\nNao foi possivel iniciar: {exc}', flush=True)
        emit('Confira se outro PitSignal ou aplicativo ocupa as portas 20777/8765.', flush=True)
        return 1
    finally:
        if server:
            server.server_close()
        if radio:
            radio.close()
        if telemetry:
            telemetry.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
