"""Validated, persistent controls for the existing radio messages."""
import copy
import json
import os
from pathlib import Path
import tempfile
from configuracao import config_file

PART_NAMES = ['pneu traseiro esquerdo', 'pneu traseiro direito', 'pneu dianteiro esquerdo', 'pneu dianteiro direito',
              'freio traseiro esquerdo', 'freio traseiro direito', 'freio dianteiro esquerdo', 'freio dianteiro direito',
              'asa dianteira esquerda', 'asa dianteira direita', 'asa traseira', 'assoalho', 'difusor', 'lateral', 'câmbio', 'motor']
TYRES = ['traseiro esquerdo', 'traseiro direito', 'dianteiro esquerdo', 'dianteiro direito']
FAULT_NAMES = ['DRS', 'ERS', 'motor estourado', 'motor travado']
DEFINITIONS = [dict(id=key, label=label, group=group, percent=False) for key, label, group in [
    ('radio_enabled','Ativar rádio automático','Gerais'),
    ('radio_check','Radio check antes da largada','Gerais'), ('lap_summary','Resumo a cada volta','Gerais'),
    ('fuel','Combustível insuficiente','Gerais'), ('pit','Janela de parada do jogo','Gerais'),
    ('summary_time','Tempo de volta','Conteúdo do resumo'), ('summary_position','Posição','Conteúdo do resumo'),
    ('summary_fuel','Combustível','Conteúdo do resumo'), ('summary_tyres','Desgaste dos pneus','Conteúdo do resumo'),
    ('summary_gaps','Intervalos para frente e para trás','Conteúdo do resumo')]]
DEFINITIONS += [dict(id=f'wear_{i}', label='Desgaste: '+name, group='Desgaste dos pneus', percent=True) for i, name in enumerate(TYRES)]
DEFINITIONS += [dict(id=f'part_{i}', label=name.capitalize(), group='Danos do carro', percent=True) for i, name in enumerate(PART_NAMES)]
DEFINITIONS += [dict(id=f'fault_{i}', label=name, group='Falhas', percent=False) for i, name in enumerate(['DRS','ERS','Motor estourado','Motor travado'])]
for definition in DEFINITIONS:
    if definition['id'] in ('radio_enabled', 'radio_check', 'lap_summary'):
        definition['group'] = 'Narrador'
    if definition['id'] == 'radio_enabled':
        definition['label'] = 'Ativar narrador e falas automáticas'
    if definition['id'] == 'lap_summary':
        definition.update(label='Resumos de corrida', interval=True)
DEFAULTS = {d['id']: dict(enabled=True, **({'step': 10} if d['percent'] else {'step': 1} if d.get('interval') else {})) for d in DEFINITIONS}
for key, label, low, high in [('thermal_dry','Pneus secos',90,105), ('thermal_inter','Intermediários',70,95), ('thermal_wet','Chuva',60,85)]:
    DEFINITIONS.append(dict(id=key,label=label,group='Cores de temperatura · faixas de referência',percent=False,thermal=True))
    DEFAULTS[key] = dict(enabled=True,low=low,high=high)


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError('Configuração incompleta. Reabra a tela de avisos.')
    result = {}
    for definition in DEFINITIONS:
        key = definition['id']
        item = value[key]
        numeric = definition['percent'] or definition.get('interval')
        expected = {'enabled','low','high'} if definition.get('thermal') else {'enabled', 'step'} if numeric else {'enabled'}
        if not isinstance(item, dict) or set(item) != expected or type(item['enabled']) is not bool:
            raise ValueError('Configuração de aviso inválida.')
        result[key] = {'enabled': item['enabled']}
        if definition.get('thermal'):
            if type(item['low']) is not int or type(item['high']) is not int or not 1 <= item['low'] < item['high'] <= 150:
                raise ValueError('O limite frio deve ser menor que o limite quente, entre 1 e 150 graus.')
            result[key].update(low=item['low'],high=item['high'])
        if numeric:
            step = item['step']
            maximum = 100 if definition['percent'] else 20
            if type(step) is not int or not 1 <= step <= maximum:
                raise ValueError('Intervalo de aviso inválido.')
            result[key]['step'] = step
    return result


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else config_file('avisos.json')
        self.warning = ''

    def load(self):
        if not self.path.exists():
            return copy.deepcopy(DEFAULTS)
        try:
            previous = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(previous, dict):
                raise ValueError('Invalid settings')
            # New controls receive defaults while previous choices remain intact.
            return validate_settings({**copy.deepcopy(DEFAULTS), **previous})
        except (OSError, ValueError):
            self.warning = 'Não foi possível ler os avisos salvos. Padrões de 10 pontos carregados; salve novamente para reparar.'
            return copy.deepcopy(DEFAULTS)

    def save(self, value):
        value = validate_settings(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as file:
                name = file.name
                json.dump(value, file, ensure_ascii=True, indent=2)
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
        self.warning = ''
        return value
