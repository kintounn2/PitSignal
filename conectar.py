"""Offline QR SVG rendering; access link stays on the local computer."""
from qrcodegen import QrCode

def qr_svg(url):
    code=QrCode.encode_text(url,QrCode.Ecc.MEDIUM)
    size=code.get_size()
    path=' '.join(f'M{x+4},{y+4}h1v1h-1z' for y in range(size) for x in range(size) if code.get_module(x,y))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size+8} {size+8}" '
            f'width="328" height="328" shape-rendering="crispEdges">'
            f'<rect width="100%" height="100%" fill="white"/><path d="{path}" fill="black"/></svg>').encode('utf-8')
