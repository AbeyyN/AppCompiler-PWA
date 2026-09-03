#!/usr/bin/env python3
import json, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BIND='0.0.0.0'
PORT=9860
ROOT=Path('/home/abeyy/redmi-dashboard')
INDEX=ROOT/'index.html'
UPSTREAM='http://192.168.1.96:9862'


def get_upstream(path, timeout=8):
    req=urllib.request.Request(UPSTREAM+path, headers={'Cache-Control':'no-cache','User-Agent':'986-Raspi-Dashboard/3'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), r.headers.get('Content-Type','application/json')

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return
    def send_bytes(self, code, body, ctype, cache='no-store'):
        self.send_response(code)
        self.send_header('Content-Type',ctype)
        self.send_header('Cache-Control',cache)
        self.send_header('X-986-Dashboard-Owner','rapsi-nas')
        self.send_header('X-986-Data-Source','redmi-build')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path in ('/','/index.html'):
            try: body=INDEX.read_bytes()
            except Exception as e: return self.send_bytes(500,str(e).encode(),'text/plain; charset=utf-8')
            return self.send_bytes(200,body,'text/html; charset=utf-8','no-store')
        if self.path=='/health':
            try:
                code,raw,_=get_upstream('/health',4)
                u=json.loads(raw)
                out={'ok':bool(u.get('ok')),'owner':'rapsi-nas','source':'redmi-build','service':'redmi-dashboard-ui','telemetry':u}
                return self.send_bytes(200,json.dumps(out,separators=(',',':')).encode(),'application/json; charset=utf-8')
            except Exception as e:
                out={'ok':False,'owner':'rapsi-nas','source':'redmi-build','service':'redmi-dashboard-ui','error':str(e)}
                return self.send_bytes(503,json.dumps(out,separators=(',',':')).encode(),'application/json; charset=utf-8')
        if self.path in ('/api/status','/api/state','/state'):
            try:
                code,raw,ctype=get_upstream('/api/status',10)
                return self.send_bytes(code,raw,ctype)
            except Exception as e:
                out={'version':'proxy-error','source':'redmi-build','dashboard_owner':'rapsi-nas','error':str(e),'runners':[],'projects':[],'runs':[],'queue':[],'history':[]}
                return self.send_bytes(503,json.dumps(out,separators=(',',':')).encode(),'application/json; charset=utf-8')
        return self.send_bytes(404,b'not found','text/plain; charset=utf-8')

if __name__=='__main__':
    ThreadingHTTPServer((BIND,PORT),Handler).serve_forever()
