"""PitSignal preferences, with migration from the previous application name."""
import os
from pathlib import Path
import tempfile


def config_file(name):
    if name not in ('acesso.json', 'avisos.json', 'language.json'):
        raise ValueError('Unknown preference file')
    base = Path(os.environ.get('LOCALAPPDATA') or Path(__file__).resolve().parent)
    target = base / 'PitSignal' / name
    previous = base / 'CoachF1' / name
    if not target.exists() and previous.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = output.name
                output.write(previous.read_bytes())
            # Import each old preference only when it has no new counterpart.
            if not target.exists():
                os.replace(temporary, target)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    return target
