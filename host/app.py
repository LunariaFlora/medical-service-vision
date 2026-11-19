"""部署入口：配置 -> 独立运行目录 -> 现有接收/任务逻辑；无电机控制。"""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime
import json
from pathlib import Path
import signal
import threading
from types import SimpleNamespace

import scan
from mission import LocalInterfaces, Mission


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    expected = {'port', 'preset_qr', 'preview', 'output_dir'}
    optional = {'qr_camera', 'api_port'}
    if not expected <= set(config) or set(config)-expected-optional:
        raise ValueError('Required keys: '+', '.join(sorted(expected))+'; optional: qr_camera, api_port')
    if not isinstance(config['port'], str) or not config['port'].strip():
        raise ValueError('port must name the H7 USB device')
    if config['preset_qr'] not in (None, '11', '13', '31', '33'):
        raise ValueError('preset_qr must be null or 11/13/31/33')
    if type(config['preview']) is not bool:
        raise ValueError('preview must be true or false')
    if not isinstance(config['output_dir'], str) or not config['output_dir']:
        raise ValueError('output_dir must be a directory')
    config['output_dir'] = str((path.parent/config['output_dir']).resolve())
    config.setdefault('api_port', 8765)
    config.setdefault('qr_camera', None)
    if type(config['api_port']) is not int or not 1024 <= config['api_port'] <= 65535:
        raise ValueError('api_port must be 1024..65535')
    qr = config['qr_camera']
    if qr is not None:
        if config['preset_qr'] is not None:
            raise ValueError('Real QR camera requires preset_qr=null; never silently replace a preset')
        if not isinstance(qr, dict) or set(qr) != {'device','width','height','fps','fourcc'}:
            raise ValueError('qr_camera requires device,width,height,fps,fourcc')
        if not ((type(qr['device']) is int and qr['device'] >= 0) or
                (isinstance(qr['device'], str) and qr['device'].startswith('/dev/'))):
            raise ValueError('QR device must be a camera index or Linux /dev/ path')
        if any(type(qr[k]) is not int or qr[k] <= 0 for k in ('width','height','fps')):
            raise ValueError('QR width,height,fps must be positive integers')
        if qr['fourcc'] not in (None, 'MJPG', 'YUYV'):
            raise ValueError('fourcc must be null, MJPG or YUYV')
    return config


@contextmanager
def single_instance(root):
    """OS锁随进程退出释放，不靠可能遗留的pid文件判断。"""
    import os
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root/'app.lock').open('a+b') as handle:
        handle.write(b'0'); handle.flush(); handle.seek(0)
        locked = False
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
            yield
        finally:
            if locked:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


def run(config, frames=0):
    root = Path(config['output_dir'])
    with single_instance(root):
        # 新进程是新一场；不把昨天的识别结果悄悄复用到今天。
        out = root/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        out.mkdir()
        (out/'config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
        tmp = root/'latest.tmp'
        tmp.write_text(str(out), encoding='utf-8'); tmp.replace(root/'latest.txt')
        stop = threading.Event()
        args = SimpleNamespace(usb_results=config['port'], usb=None, openart=None,
                               input=None, roi=None, code='BAR' if config.get('qr_camera') else 'BOTH', frames=frames,
                               preview=config['preview'], snapshot_first=False,
                               out=str(out), reconnect=True, stop_event=stop,
                               setup='H7 deployment; USB results; VGA AUTO')
        args.mission_controller = Mission(LocalInterfaces(out), config['preset_qr'])
        # preview 使用自己的关闭事件；无窗口服务通过SIGTERM/Ctrl+C正常退出。
        previous = {}
        if not args.preview:
            for name in (signal.SIGINT, signal.SIGTERM):
                previous[name] = signal.signal(name, lambda *_: stop.set())
        print('RUN_DIRECTORY', out, flush=True)
        try:
            with ExitStack() as resources:
                from team_api import TeamAPI
                api = TeamAPI(args.mission_controller, config.get('api_port',8765))
                api.start(); resources.callback(api.close)
                print('TEAM_API http://127.0.0.1:%d/v1/route' % api.server.server_port, flush=True)
                if config.get('qr_camera'):
                    from qr_camera import QRCamera
                    qr = QRCamera(config['qr_camera'], args.mission_controller, out)
                    qr.start(); resources.callback(qr.close)
                return scan.run(args)
        finally:
            for name, handler in previous.items():
                signal.signal(name, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    parser.add_argument('--check', action='store_true', help='Validate config without opening USB')
    parser.add_argument('--frames', type=int, default=0, help='0: continuous; positive: installation check')
    args = parser.parse_args()
    if args.frames < 0:
        parser.error('frames must be >= 0')
    config = load_config(args.config)
    if args.check:
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return
    run(config, args.frames)


if __name__ == '__main__':
    main()
