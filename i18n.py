"""Local Portuguese/English catalogue, shared by UI, API and Windows speech."""
import json,os,re,tempfile,threading
from pathlib import Path
from configuracao import config_file

CATALOG={}
for line in Path(__file__).with_name('translations.tsv').read_text(encoding='utf-8').splitlines():
    if '\t' in line:
        source,target=line.split('\t',1)
        if source:CATALOG[source]=target
for source,target in list(CATALOG.items()):
    if source[0].islower():CATALOG.setdefault(source[0].upper()+source[1:],target[0].upper()+target[1:])
_PATTERN=re.compile('|'.join(re.escape(key) for key in sorted(CATALOG,key=len,reverse=True)))

def translate(value,language='pt'):
    if language!='en':return value
    if isinstance(value,str):return _PATTERN.sub(lambda match:CATALOG[match.group()],value)
    if isinstance(value,list):return [translate(item,language) for item in value]
    if isinstance(value,tuple):return [translate(item,language) for item in value]
    if isinstance(value,dict):return {translate(key,language):translate(item,language) for key,item in value.items()}
    return value

def speech_text(text,language):
    result=translate(text,language)
    return re.sub(r'(?<=\d),(?=\d)', '.', result) if language=='en' else result


class LanguageStore:
    def __init__(self,path=None):
        self.path=Path(path) if path else config_file('language.json')
        self.lock=threading.Lock()
        try:value=json.loads(self.path.read_text(encoding='utf-8')).get('language','pt')
        except (OSError,ValueError,AttributeError):value='pt'
        self.value=value if value in ('pt','en') else 'pt'
    def get(self):
        with self.lock:return self.value
    def set(self,value):
        if value not in ('pt','en'):raise ValueError('Invalid language')
        with self.lock:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            filename=None
            try:
                with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=self.path.parent,delete=False) as file:
                    filename=file.name;json.dump({'language':value},file)
                os.replace(filename,self.path)
                self.value=value
            finally:
                if filename and os.path.exists(filename):os.unlink(filename)
