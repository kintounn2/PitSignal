"""Local Windows voice and rate-limited race messages; no cloud/API required."""
import json
import os
import subprocess
import threading
import time
import copy
from pathlib import Path
from avisos import DEFAULTS, DEFINITIONS, PART_NAMES, TYRES, FAULT_NAMES, SettingsStore
from i18n import translate, speech_text


class WindowsVoice:
    def __init__(self, language=None):
        self.lock = threading.Lock()
        self.process = None
        self.language=language

    def say(self, text, cancelled=None):
        if os.name != 'nt':
            raise RuntimeError('A voz pelo PC requer Windows. O painel continua funcionando.')
        executable = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        script = Path(__file__).with_name('voz.ps1')
        with self.lock:
            if cancelled is not None and cancelled.is_set():
                raise InterruptedError('Aviso substituído.')
            process = subprocess.Popen(
                [str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace', creationflags=subprocess.CREATE_NO_WINDOW)
            self.process = process
        try:
            language=self.language.get() if self.language else 'pt'
            output, error = process.communicate(json.dumps({'text': speech_text(text,language),'language':language}, ensure_ascii=True), timeout=40)
            if cancelled is not None and cancelled.is_set():
                raise InterruptedError('Aviso substituído.')
            try:
                result = json.loads(output.lstrip('\ufeff').strip())
            except ValueError:
                raise RuntimeError('Não foi possível iniciar a voz. Confira o áudio e as vozes instaladas no Windows.')
            if process.returncode or not result.get('ok'):
                raise RuntimeError(result.get('error', 'Falha na voz do Windows.'))
            return result.get('voice', 'Português')
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError('A voz demorou demais. Confira o áudio do Windows e teste novamente.')
        finally:
            with self.lock:
                if self.process is process:
                    self.process = None

    def cancel(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()


class RaceMessages:
    """Damage has priority; one fresh summary for each completed lap."""
    def __init__(self):
        self.settings = copy.deepcopy(DEFAULTS)
        self.reset(None)

    def on(self, key):
        return self.settings[key]['enabled']

    def reset(self, uid):
        self.session = uid
        self.lap = 0
        self.result = 2
        self.parts = {}
        self.faults = {}
        self.sent = {}
        self.last = -1e9
        self.pending_lap = None
        self.priority = 'normal'
        self.start_checked = False
        self.wear_values = {}
        self.pending_wear = {}

    def lap_report(self, d, completed, final=False):
        lap, status, damage, session = (d.get(k, {}) for k in ('lap', 'status', 'damage', 'session'))
        text = [f'Volta {completed} concluída.']
        ms = lap.get('current' if final and completed == lap.get('lap') else 'last', 0)
        if ms and self.on('summary_time'):
            minutes, seconds = divmod(round(ms / 1000), 60)
            unit = 'minuto' if minutes == 1 else 'minutos'
            text.append(f'Tempo, {minutes} {unit} e {seconds} segundos.' if minutes else f'Tempo, {seconds} segundos.')
        if lap.get('position') and self.on('summary_position'):
            text.append(f"Posição {lap['position']}.")
        if 'margin' in status and self.on('summary_fuel'):
            margin = status['margin']
            amount = f'{abs(margin):.1f}'.replace('.', ',')
            text.append('Combustível no limite pela previsão do jogo.' if abs(margin) < .05 else f'Combustível, déficit de {amount} volta. Economize.' if margin < 0 else f'Combustível, margem de {amount} volta pelo jogo.')
        if damage.get('wear') and self.on('summary_tyres'):
            text.append(f"Pneu mais gasto, {round(max(damage['wear']))} por cento.")
        if self.on('summary_gaps') and d.get('race'):
            for key,label in [('ahead','Carro à frente'),('behind','Carro atrás')]:
                other=lap.get(key)
                if other is not None:
                    gap=f"{other['gap']:.2f}".replace('.',',')
                    text.append(f'{label}, {gap} segundos.')
        if lap.get('pit'):
            text.append('Você está nos boxes.')
        elif final:
            text.append('Corrida concluída.')
        else:
            ideal, current = session.get('ideal', 0), lap.get('lap', 0)
            if self.on('pit') and ideal and current == ideal:
                text.append('Esta é a volta de parada prevista pelo jogo.')
            elif self.on('pit') and ideal and current < ideal:
                text.append(f'Parada prevista pelo jogo, volta {ideal}.')
        return ('Demonstração. ' if d.get('demo') else '') + ' '.join(text)

    def current_report(self, d):
        lap, session, status, damage = (d.get(key,{}) for key in ('lap','session','status','damage'))
        text = ['Resumo solicitado.']
        if lap.get('lap'):
            text.append(f"Volta {lap['lap']} de {session['total']}." if session.get('total') else f"Volta {lap['lap']}.")
        if self.on('summary_position') and lap.get('position'):
            text.append(f"Posição {lap['position']}.")
        if self.on('summary_time') and lap.get('last'):
            minutes,seconds = divmod(round(lap['last']/1000),60)
            text.append(f'Última volta, {minutes} minuto e {seconds} segundos.' if minutes else f'Última volta, {seconds} segundos.')
        if self.on('summary_fuel') and 'margin' in status:
            margin = status['margin']
            value = f'{abs(margin):.1f}'.replace('.',',')
            text.append(f'Combustível, déficit de {value} volta.' if margin<0 else f'Combustível, margem de {value} volta.')
        if self.on('summary_tyres') and damage.get('wear'):
            text.append(f"Maior desgaste, {round(max(damage['wear']))} por cento.")
        parts = [(name,value) for name,value in damage.get('parts',{}).items() if value>0]
        if parts:
            name,value = max(parts,key=lambda item:item[1])
            text.append(f'Maior dano, {name}, {value} por cento.')
        for key,label in [('ahead','À frente'),('behind','Atrás')]:
            other = lap.get(key) if d.get('race') and self.on('summary_gaps') else None
            if other:
                seconds = f"{other['gap']:.1f}".replace('.',',')
                text.append(f'{label}, {seconds} segundos.')
        if self.on('pit') and session.get('ideal') and d.get('race'):
            text.append(f"Parada prevista pelo jogo, volta {session['ideal']}.")
        return ('Demonstração. ' if d.get('demo') else '')+' '.join(text)

    def choose(self, d, now, available=True):
        self.priority = 'normal'
        if not self.on('radio_enabled') or not d.get('connected') or not d.get('race'):
            return None
        lap = d.get('lap', {})
        current = lap.get('lap', 0)
        uid = d.get('session_uid')
        if uid != self.session or (current and self.lap and current < self.lap):
            checked = self.start_checked if uid == self.session else False
            self.reset(uid)
            self.start_checked = checked
        result = lap.get('result', 2)
        car = d.get('car', {})
        if (self.on('radio_check') and not self.start_checked and current <= 1 and lap.get('current', 0) == 0
                and car.get('speed') is not None and car['speed'] <= 1 and result in (0, 1, 2)
                and available):
            self.start_checked = True
            self.last = now
            self.priority = 'check'
            return 'Radio check. PitSignal funcionando. Vou acompanhar sua corrida. Boa largada.'
        if not current:
            return None
        if self.on('lap_summary') and self.lap and current > self.lap:
            completed = current - 1
            self.pending_lap = (completed, now, result == 3) if completed % self.settings['lap_summary']['step'] == 0 or result == 3 else None
        elif self.on('lap_summary') and self.lap and result == 3 and self.result == 2:
            self.pending_lap = (current, now, True)
        self.lap, self.result = current, result
        active = result == 2 and lap.get('driver', 4) != 0
        damage = d.get('damage', {})
        parts = damage.get('parts')
        if parts is None:
            parts = dict(zip(['asa dianteira esquerda', 'asa dianteira direita', 'asa traseira'], damage.get('wing', [])))
        faults = damage.get('faults', {})
        changed = []
        for i, name in enumerate(PART_NAMES):
            if name not in parts:
                continue
            option = self.settings[f'part_{i}']
            value, previous = parts[name], self.parts.get(name, 0)
            if option['enabled'] and value // option['step'] > previous // option['step']:
                changed.append((name, value))
        new_faults = [name for i, name in enumerate(FAULT_NAMES)
                      if self.on(f'fault_{i}') and faults.get(name) and not self.faults.get(name, False)]
        if parts:
            self.parts = dict(parts)
        if faults:
            self.faults = dict(faults)
        if active and (changed or new_faults):
            items = sorted(changed, key=lambda pair: -pair[1])
            details = [f'{name}, {value} por cento' for name, value in items[:3]]
            details += [f'falha em {name}' for name in new_faults]
            self.priority = 'damage'
            return ('Demonstração. ' if d.get('demo') else '') + 'Dano detectado. ' + '. '.join(details) + '.'
        for i, value in enumerate(damage.get('wear', [])):
            key = f'wear_{i}'
            option = self.settings[key]
            previous = self.wear_values.get(key, 0)
            if active and option['enabled'] and value // option['step'] > previous // option['step']:
                self.pending_wear[key] = (value, now)
            if value < previous or not option['enabled']:
                self.pending_wear.pop(key, None)
            self.wear_values[key] = value
        if not available:
            return None
        if self.pending_lap:
            completed, recorded, final = self.pending_lap
            self.pending_lap = None
            if now-recorded <= 30 and (active or final):
                self.priority = 'lap'
                self.last = now
                return self.lap_report(d, completed, final)
        if not active or lap.get('pit') or now-self.last < 15:
            return None
        candidates = []
        session, status = d.get('session', {}), d.get('status', {})
        ideal, latest = session.get('ideal', 0), session.get('latest', 0)
        if self.on('pit') and ideal and current == ideal-1:
            candidates.append((f'pit-pre-{ideal}', float('inf'), f'Pelo plano do jogo, a parada está prevista para a próxima volta, volta {ideal}.'))
        if self.on('pit') and ideal and current == ideal:
            candidates.append((f'pit-now-{ideal}', float('inf'), 'Você chegou à volta de parada prevista pelo jogo. Confira pneus e tráfego antes de entrar.'))
        if self.on('pit') and latest and current == latest and latest != ideal:
            candidates.append((f'pit-limit-{latest}', float('inf'), 'Você chegou à volta limite de parada indicada pelo plano do jogo. Confira sua estratégia.'))
        if self.on('fuel') and status.get('margin', 0) < -0.05:
            candidates.append(('fuel', 120, 'A previsão do jogo indica combustível insuficiente para terminar. Considere economizar.'))
        updates = [(TYRES[int(key[-1])], value) for key, (value, recorded) in self.pending_wear.items()
                   if now-recorded <= 30 and self.on(key)]
        if updates:
            self.pending_wear.clear()
            details = '. '.join(f'{name}, {round(value)} por cento' for name, value in updates)
            candidates.insert(0, ('wear-update', 0, 'Desgaste dos pneus. ' + details + '.'))
        for key, cooldown, text in candidates:
            if key not in self.sent or now-self.sent[key] >= cooldown:
                self.sent[key] = now
                self.last = now
                return ('Demonstração. ' if d.get('demo') else '') + text
        return None


class Radio:
    def __init__(self, snapshot, voice=None, settings_path=None, language=None):
        self.source = snapshot
        self.voice = voice or WindowsVoice(language)
        self.messages = RaceMessages()
        self.lock = threading.Lock()
        self.settings_save_lock = threading.Lock()
        self.store = SettingsStore(settings_path)
        self.settings = self.store.load()
        self.revision = 0
        self.stop = threading.Event()
        self.test_pending = False
        self.summary_pending = False
        self.last_summary = -1e9
        self.active_priority = 'normal'
        self.enabled = self.settings['radio_enabled']['enabled']
        self.speaking = False
        self.error = ''
        self.last_text = ''
        self.voice_name = ''
        self.last_test = -1e9
        self.speech_thread = None
        self.speech_cancel = threading.Event()
        self.pending_damage = None
        self.pending_uid = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def snapshot(self):
        with self.lock:
            return dict(enabled=self.enabled, speaking=self.speaking, error=self.error,
                        last_text=self.last_text, voice=self.voice_name, settings_revision=self.revision)

    def settings_snapshot(self):
        with self.lock:
            return dict(settings=copy.deepcopy(self.settings), definitions=DEFINITIONS,
                        defaults=DEFAULTS, warning=self.store.warning)

    def thermal_snapshot(self):
        with self.lock:
            return {key:copy.deepcopy(self.settings['thermal_'+key]) for key in ('dry','inter','wet')}

    def configure(self, value):
        with self.settings_save_lock:
            saved = self.store.save(value)
            with self.lock:
                self.settings = saved
                self.enabled = saved['radio_enabled']['enabled']
                self.revision += 1
                self.test_pending = False
                self.summary_pending = False
            self.interrupt()
        return 'Configurações salvas no PC e aplicadas ao narrador.'

    def interrupt(self):
        self.speech_cancel.set()
        self.voice.cancel()

    def command(self, action):
        if action == 'summary':
            if not self.source().get('connected'):
                return 'Sem telemetria recente. Entre na pista para ouvir o resumo.'
            cancel = False
            with self.lock:
                now = time.monotonic()
                if self.summary_pending or now-self.last_summary<3:
                    return 'O resumo já foi solicitado. Aguarde o rádio.'
                self.summary_pending = True
                self.last_summary = now
                self.error = ''
                cancel = self.speaking and self.active_priority!='damage'
            if cancel:
                self.interrupt()
            return 'Resumo solicitado. O áudio sai pelo PC.'
        if action == 'toggle':
            settings = self.settings_snapshot()['settings']
            settings['radio_enabled']['enabled'] = not settings['radio_enabled']['enabled']
            return self.configure(settings)
        cancel = False
        with self.lock:
            if action == 'test':
                now = time.monotonic()
                if self.speaking or now-self.last_test < 5:
                    return 'Aguarde o rádio terminar antes de testar novamente.'
                self.last_test = now
                self.test_pending = True
                self.error = ''
            elif action == 'toggle':
                self.enabled = not self.enabled
                cancel = not self.enabled
                if cancel:
                    self.test_pending = False
            else:
                raise ValueError('Comando de rádio inválido.')
        if cancel:
            self.interrupt()
        return 'Teste solicitado. O áudio sai pelo PC.' if action == 'test' else 'Configuração do rádio atualizada.'

    def start_speech(self, text, test=False, priority='normal'):
        cancelled = threading.Event()
        with self.lock:
            if self.stop.is_set() or (not test and not self.enabled):
                return False
            self.speech_cancel = cancelled
            self.speaking, self.last_text = True, text
            self.active_priority = priority

        def speak():
            try:
                name = self.voice.say(text, cancelled=cancelled)
                with self.lock:
                    if not cancelled.is_set():
                        self.voice_name, self.error = name, ''
            except Exception as exc:
                with self.lock:
                    if not cancelled.is_set():
                        self.error = str(exc)
            finally:
                with self.lock:
                    self.speaking = False
        self.speech_thread = threading.Thread(target=speak, daemon=True)
        self.speech_thread.start()
        return True

    def run(self):
        applied_revision = -1
        while not self.stop.wait(0.1):
            with self.lock:
                enabled, error = self.enabled, self.error
                manual = self.summary_pending
                revision = self.revision
                settings = copy.deepcopy(self.settings) if revision != applied_revision else None
            if settings is not None:
                self.messages.settings = settings
                self.messages.pending_lap = None
                self.messages.pending_wear.clear()
                self.pending_damage = None
                applied_revision = revision
            busy = self.speech_thread is not None and self.speech_thread.is_alive()
            if not enabled:
                self.pending_damage = None
            data = self.source()
            now = time.monotonic()
            if enabled and not error:
                text = self.messages.choose(data, now, available=not busy and self.pending_damage is None and not manual)
                if text and self.messages.priority == 'damage':
                    self.pending_damage = (text, now)
                    self.pending_uid = data.get('session_uid')
                    if busy:
                        self.interrupt()
                elif text:
                    if not busy:
                        self.messages.last = now
                        busy = self.start_speech(text, priority=self.messages.priority)
            if self.pending_damage and not busy:
                text, recorded = self.pending_damage
                self.pending_damage = None
                if enabled and data.get('connected') and data.get('session_uid') == self.pending_uid and now-recorded <= 5:
                    self.messages.last = now
                    busy = self.start_speech(text, priority='damage')
            if not busy:
                with self.lock:
                    manual = self.summary_pending
                    self.summary_pending = False
                if manual and data.get('connected'):
                    self.messages.settings = self.settings_snapshot()['settings']
                    self.messages.last = now
                    busy = self.start_speech(self.messages.current_report(data), test=True, priority='manual')
            if not busy:
                with self.lock:
                    test = self.test_pending
                    self.test_pending = False
                if test:
                    self.messages.last = now
                    self.start_speech('Rádio conferido. Seu engenheiro está pronto. Os avisos seguem as configurações escolhidas no tablet.', test=True)

    def close(self):
        self.stop.set()
        self.interrupt()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
        if self.speech_thread and self.speech_thread.is_alive():
            self.speech_thread.join(timeout=3)
