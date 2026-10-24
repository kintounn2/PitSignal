"""F1 24 UDP terminal prototype. Python 3.9+, standard library only."""
import argparse
import collections
import math
import os
import select
import socket
import struct
import time

HEADER = struct.Struct('<HBBBBBQfIIBB')
# Packet lengths and per-car strides, F1 24 UDP v1, packed little endian.
SIZES = {1: 753, 2: 1285, 6: 1352, 7: 1239, 10: 953, 11: 1460}
STRIDES = {2: 57, 6: 60, 7: 55, 10: 42}

class Receiver:
    def __init__(self):
        self.reset(None)
        self.good = 0
        self.ignored = 0

    def reset(self, uid):
        self.uid = uid
        self.groups = {}
        self.stamps = {}
        self.frames = {}
        self.player = None
        self.best_lap_ms = None
        self.best_event = 0
        self.histories = {}
        self.roster = set()
        self.active_cars = set()
        self.pending_record = None

    def feed(self, data, now=None):
        now = time.monotonic() if now is None else now
        if len(data) < HEADER.size:
            self.ignored += 1
            return False
        fmt, year, major, minor, version, pid, uid, stamp, frame, overall, player, secondary = HEADER.unpack_from(data)
        if fmt != 2024 or version != 1 or pid not in SIZES or len(data) != SIZES[pid] or player >= 22:
            self.ignored += 1
            return False
        if uid != self.uid or player != self.player:
            self.reset(uid)
            self.player = player
        if overall < self.frames.get(pid, -1):
            return False
        self.frames[pid] = overall
        b = HEADER.size + STRIDES.get(pid, 0) * player
        if pid == 1:
            group = dict(weather=data[b], total=data[b+3], session_type=data[b+6],
                         time_left=struct.unpack_from('<H', data, b+9)[0], safety_car=data[b+124],
                         track=struct.unpack_from('<b', data, b+7)[0],
                         ideal=data[b+653], latest=data[b+654], rejoin=data[b+655])
        elif pid == 2:
            last, current = struct.unpack_from('<II', data, b)
            group = dict(last=last, current=current, position=data[b+32], lap=data[b+33], pit=data[b+34], driver=data[b+44], result=data[b+45])
            group['delta_leader'] = struct.unpack_from('<H', data, b+17)[0]/1000 + data[b+19]*60
            group['invalid'] = bool(data[b+37])
            group['best_lap_ms']=self.best_lap_ms
            group['best_event']=self.best_event
            self.roster={i for i in range(22) if data[29+57*i+32]>0 and data[29+57*i+45] in (2,3,4,5,6,7)}
            self.active_cars={i for i in self.roster if data[29+57*i+45]==2}
            position = group['position']
            group['ahead'] = None
            group['behind'] = None
            for index in range(22):
                other = 29 + 57*index
                other_position = data[other+32]
                if index == player or data[other+45] not in (2, 3) or not other_position:
                    continue
                if position > 1 and other_position == position-1:
                    group['ahead'] = dict(position=other_position, gap=struct.unpack_from('<H',data,b+14)[0]/1000+data[b+16]*60)
                if position > 0 and other_position == position+1:
                    group['behind'] = dict(position=other_position, gap=struct.unpack_from('<H',data,other+14)[0]/1000+data[other+16]*60)
        elif pid == 6:
            group = dict(speed=struct.unpack_from('<H', data, b)[0],
                         throttle=struct.unpack_from('<f', data, b+2)[0],
                         brake=struct.unpack_from('<f', data, b+10)[0],
                         gear=struct.unpack_from('<b', data, b+15)[0],
                         rpm=struct.unpack_from('<H', data, b+16)[0],
                         drs=bool(data[b+18]),
                         rev_lights_pct=data[b+19],
                         inner=list(data[b+34:b+38]))
        elif pid == 7:
            group = dict(fuel=struct.unpack_from('<f', data, b+5)[0],
                         fuel_mix=data[b+2], brake_bias=data[b+3], limiter=bool(data[b+4]),
                         max_rpm=struct.unpack_from('<H',data,b+17)[0],
                         margin=struct.unpack_from('<f', data, b+13)[0],
                         compound=data[b+26], age=data[b+27],
                         ers=struct.unpack_from('<f', data, b+37)[0] / 4000000 * 100)
            group['ers_mode'] = data[b+41]
            group['fia_flag'] = struct.unpack_from('<b',data,b+28)[0]
            group['ers_overtake'] = group['ers_mode'] == 3
            group['drs_allowed'] = bool(data[b+22])
            capacity = struct.unpack_from('<f', data, b+9)[0]
            group['fuel_capacity'] = capacity
            group['fuel_percent'] = min(100.0,max(0.0,100*group['fuel']/capacity)) if capacity>0 else None
            group['ers_power'] = struct.unpack_from('<f',data,b+33)[0]
            group['ers_deploying'] = group['ers_power'] > 0
        elif pid == 11:
            car=data[29]
            count=min(data[30],100)
            if car>=22:return False
            valid=[]
            for index in range(count):
                offset=36+14*index
                lap_time=struct.unpack_from('<I',data,offset)[0]
                if lap_time and data[offset+13]&1:valid.append(lap_time)
            best=min(valid) if valid else None
            previous=self.histories.get(car)
            if car==player and previous is not None and best is not None and (previous['best'] is None or best<previous['best']):
                self.pending_record=(best,now,overall)
            self.histories[car]={'best':best,'seen':now}
            known=[entry['best'] for entry in self.histories.values() if entry['best'] is not None]
            ready=self.roster and self.roster.issubset(self.histories)
            self.best_lap_ms=min(known) if ready and known else None
            if self.pending_record and ready:
                candidate,recorded,event=self.pending_record
                fresh=all(self.histories[i]['seen']>=recorded for i in self.active_cars)
                if fresh and now-recorded>=1.1:
                    rivals=[entry['best'] for i,entry in self.histories.items() if i!=player and entry['best'] is not None]
                    if self.groups.get(1,{}).get('session_type') in (15,16,17) and self.histories[player]['best']==candidate and (not rivals or candidate<min(rivals)):
                        self.best_event=event
                    self.pending_record=None
                elif now-recorded>5:self.pending_record=None
            if 2 in self.groups:
                self.groups[2]['best_lap_ms']=self.best_lap_ms
                self.groups[2]['best_event']=self.best_event
            group={'car':car}
        else:
            group = dict(wear=struct.unpack_from('<4f', data, b), wing=list(data[b+24:b+27]))
            labels = ['pneu traseiro esquerdo', 'pneu traseiro direito', 'pneu dianteiro esquerdo', 'pneu dianteiro direito',
                      'freio traseiro esquerdo', 'freio traseiro direito', 'freio dianteiro esquerdo', 'freio dianteiro direito',
                      'asa dianteira esquerda', 'asa dianteira direita', 'asa traseira', 'assoalho', 'difusor', 'lateral']
            group['parts'] = dict(zip(labels, data[b+16:b+30]))
            group['parts'].update({'câmbio': data[b+32], 'motor': data[b+33]})
            group['faults'] = {'DRS': bool(data[b+30]), 'ERS': bool(data[b+31]),
                               'motor estourado': bool(data[b+40]), 'motor travado': bool(data[b+41])}
        numbers = [n for v in group.values() for n in (v if isinstance(v, (tuple, list)) else [v])]
        if any(not math.isfinite(v) for v in numbers if isinstance(v, float)):
            self.ignored += 1
            return False
        self.groups[pid] = group
        self.stamps[pid] = now
        self.good += 1
        return True

    def fresh(self, pid, now):
        return self.groups.get(pid, {}) if now-self.stamps.get(pid, -1e9) < 3 else {}

def lap_time(ms):
    return '--' if not ms else f'{ms//60000}:{ms%60000/1000:06.3f}'

def render(r, demo=False):
    now = time.monotonic()
    s, l, t, c, d = (r.fresh(pid, now) for pid in (1, 2, 6, 7, 10))
    live = bool(t)
    lines = ['='*70, ' PitSignal | PROTOTIPO DE TELEMETRIA | F1 24',
             ' DEMONSTRACAO: DADOS SIMULADOS' if demo else ' UDP local: 127.0.0.1:20777 | Ctrl+C para encerrar', '='*70,
             ' CONECTADO - recebendo dados' if live else ' AGUARDANDO DADOS DO CARRO (ou sinal interrompido)',
             f' Pacotes validos: {r.good} | Ignorados/outros: {r.ignored}', '']
    if l:
        lines += [f" Volta: {l['lap']}/{s.get('total', '?')} | Posicao: {l['position']} | Boxes: {l['pit']}",
                  f" Volta atual: {lap_time(l['current'])} | Anterior: {lap_time(l['last'])}"]
    if t:
        lines += [f" Velocidade: {t['speed']} km/h | Marcha: {t['gear']} | RPM: {t['rpm']}",
                  f" Acelerador: {t['throttle']:.0%} | Freio: {t['brake']:.0%}"]
    if c:
        tyre = {16:'Macio',17:'Medio',18:'Duro',7:'Intermediario',8:'Chuva'}.get(c['compound'], str(c['compound']))
        lines += [f" Combustivel: {c['fuel']:.2f} kg | ERS: {c['ers']:.0f}%",
                  f" Margem de combustivel do MFD: {c['margin']:+.2f} voltas",
                  f" Pneus: {tyre} | Idade: {c['age']} voltas"]
    lines += ['', ' Pneus                  TE       TD       DE       DD']
    if t:
        lines += [' Temperatura interna ' + ''.join(f'{v:8d}' for v in t['inner'])+' C']
    if d:
        lines += [' Desgaste (%)        ' + ''.join(f'{v:8.1f}' for v in d['wear'])]
    lines += ['', ' AVISOS (regras experimentais; sem IA nesta versao)']
    alerts = []
    race = s.get('session_type') in (15,16,17)
    if c and race and c['margin'] < 0:
        alerts.append('Combustivel abaixo da previsao do jogo: considere economizar.')
    if d and max(d['wear']) >= 60:
        alerts.append('Desgaste acima de 60%: avalie pneus e ritmo. Limiar experimental.')
    if d and max(d['wing']) > 0:
        alerts.append('Dano de asa detectado. Avalie perda de desempenho.')
    if race and s.get('ideal',0):
        alerts.append(f"Estrategia DO JOGO: parada ideal volta {s['ideal']}; limite {s['latest']}.")
    lines += [' - '+x for x in alerts] or [' - Sem alertas com os dados disponiveis.']
    lines += ['', ' Janela de boxes acima vem do jogo; nao e um calculo proprio.',
              ' Campos sem atualizacao por 3 segundos sao ocultados.',
              ' Sem sinal? Confira UDP ligado, IP, porta e formato 2024.', '='*70]
    return '\n'.join(lines)

def demo_packet(pid, tick):
    data = bytearray(SIZES[pid])
    HEADER.pack_into(data, 0, 2024,24,1,0,1,pid,12345,float(tick),tick,tick,3,255)
    b = 29 + STRIDES.get(pid,0)*3
    if pid == 1:
        data[32]=20
        data[35]=15
        data[682:685]=bytes([10,13,8])
        struct.pack_into('<H',data,38,3872)
        data[153] = 2 if 40 <= tick % 160 < 80 else 0
    elif pid == 2:
        struct.pack_into('<II',data,b,91234,0 if tick < 4 else (tick % 80)*250)
        data[b+32:b+35]=bytes([5,1+min(tick//80,19),0])
        data[b+44:b+46]=bytes([4,2])
        struct.pack_into('<H',data,b+17,10365)
        struct.pack_into('<H',data,b+14,2450)
        for index, position in [(2,4),(4,6)]:
            other=29+57*index
            data[other+32:other+35]=bytes([position,1+min(tick//80,19),0])
            data[other+44:other+46]=bytes([4,2])
            struct.pack_into('<H',data,other+14,1250)
    elif pid == 6:
        struct.pack_into('<HfffBbH',data,b,0 if tick < 4 else 180+tick%80,0.7,0.1,0.0,0,6,11000)
        data[b+34:b+38]=bytes([94,95,98,99])
    elif pid == 7:
        data[b+2:b+5]=bytes([2,55,0])
        struct.pack_into('<fff',data,b+5,25.0,110.0,-0.3)
        data[b+26:b+28]=bytes([17,7])
        struct.pack_into('<f',data,b+37,2400000)
        data[b+41]=3
    elif pid == 10:
        struct.pack_into('<4f',data,b,31,33,40,42)
        data[b+24] = 10 if tick >= 96 else 5 if tick >= 48 else 0
    elif pid == 11:
        car=(2,3,4)[tick%3]
        data[29]=car;data[30]=1;data[32]=1
        value=90000 if car==2 else 92000 if car==4 else 91000 if tick<160 else 89000
        struct.pack_into('<I',data,36,value);data[49]=15
    return bytes(data)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--seconds', type=float, default=0, help='Encerrar apos N segundos (0 = continuo).')
    args = parser.parse_args()
    if os.name == 'nt':
        os.system('')  # Enable ANSI output in Windows Terminal / console.
    r = Receiver()
    sock = None
    start = time.monotonic()
    next_draw = 0
    tick = 0
    try:
        if not args.demo:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(('127.0.0.1',20777))
            sock.setblocking(False)
        print('\033[2J', end='')
        while not args.seconds or time.monotonic()-start < args.seconds:
            if sock:
                if select.select([sock],[],[],0.05)[0]:
                    # Bound each batch so a continuous stream cannot starve rendering.
                    for _ in range(256):
                        try:
                            data, addr = sock.recvfrom(4096)
                        except BlockingIOError:
                            break
                        r.feed(data)
            else:
                for pid in SIZES:
                    r.feed(demo_packet(pid,tick))
                tick += 1
                time.sleep(0.05)
            if time.monotonic() >= next_draw:
                print('\033[H'+render(r,args.demo)+'\033[J', flush=True)
                next_draw = time.monotonic()+0.25
    except KeyboardInterrupt:
        print('\nEncerrado.')
    except OSError as exc:
        print(f'\nNao foi possivel receber UDP: {exc}')
        print('Se a porta 20777 estiver em uso, feche outro aplicativo de telemetria.')
        return 1
    finally:
        if sock:
            sock.close()
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
