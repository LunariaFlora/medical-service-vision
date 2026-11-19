"""同一主机进程间的只读接口；没有车辆控制/启动指令。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


class TeamAPI:
    def __init__(self, mission, port=8765):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state = mission.snapshot()
                if self.path == '/v1/route':
                    body = {'schema_version': 1, 'run_id': state['run_id'],
                            'ready': state['plan'] is not None, 'plan': state['plan']}
                elif self.path == '/v1/state':
                    body = dict(state, schema_version=1)
                else:
                    self.send_error(404)
                    return
                data = json.dumps(body, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
