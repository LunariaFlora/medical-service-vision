"""RCD1 USB results: bounded, CRC checked, per-frame messages including empty results."""
import json
import struct
import time
import zlib


def receive_result(port):
    deadline = time.monotonic()+10
    def exact(n):
        data=bytearray()
        while len(data)<n:
            if time.monotonic()>deadline: raise TimeoutError('USB result timeout; reconnect board')
            data.extend(port.read(n-len(data)))
        return bytes(data)
    magic,size,crc=struct.unpack('<4sII',exact(12))
    if magic!=b'RCD1' or not 0<size<=65536: raise ValueError('Invalid USB result header')
    body=exact(size)
    if zlib.crc32(body)&0xffffffff != crc: raise ValueError('USB result CRC mismatch')
    row=json.loads(body)
    if row.get('protocol')!=1 or not isinstance(row.get('boot'),str) or type(row.get('seq')) is not int:
        raise ValueError('Invalid result identity')
    if not isinstance(row.get('records'),list) or not isinstance(row.get('errors'),dict):
        raise ValueError('Invalid result lists')
    for r in row['records']:
        if not isinstance(r,dict) or not isinstance(r.get('payload'),str) or not isinstance(r.get('type'),str) or type(r.get('valid')) is not bool:
            raise ValueError('Invalid decoded record')
    if row.get('barcode_like') is not None and type(row['barcode_like']) is not bool:
        raise ValueError('Invalid appearance hint')
    if not isinstance(row.get('board_ms'),(float,int)) or row['board_ms']<0:
        raise ValueError('Invalid board time')
    row['packet_bytes']=size+12
    return row


def _session(args,stop=None):
    import serial
    port=serial.Serial(args.usb_results,115200,timeout=.2,write_timeout=2)
    last_seq,boot=-1,None
    start=time.monotonic()
    try:
        port.dtr=True
        port.reset_input_buffer()
        index=0
        while args.frames==0 or index<args.frames:
            if stop is not None and stop.is_set(): return
            mission = getattr(args, 'mission_controller', None)
            barcode_phase = mission is not None and mission.snapshot()['plan'] is not None
            command = b'BARS' if barcode_phase or getattr(args,'code','BOTH') == 'BAR' else b'SCAN'
            request = getattr(args, 'snapshot_request', None)
            capture = request is not None and request.is_set()
            if capture:
                request.clear()
                command = b'PICB' if command == b'BARS' else b'PICQ'
            if port.write(command)!=4: raise OSError('Incomplete scan request')
            row=receive_result(port)
            if boot is not None and (row['boot']!=boot or row['seq']<=last_seq):
                raise ValueError('Board restarted/repeated frame; start a new run')
            boot,last_seq=row['boot'],row['seq']
            gray = None
            if capture:
                if row.get('has_image') is not True: raise ValueError('Board does not support requested original image')
                from wire import receive
                import numpy as np
                seq,w,h,data = receive(port)
                if seq != row['seq']: raise ValueError('Image/result sequence mismatch')
                gray = np.frombuffer(data,np.uint8).reshape(h,w).copy()
            elif row.get('has_image'):
                raise ValueError('Unexpected image payload')
            args.board_result=row
            args.capture_transport='usb_native_decode'
            yield index,(time.monotonic()-start)*1000,gray
            index+=1
    finally:
        port.close()


def frames(args, stop=None):
    """部署入口可重连；原诊断入口仍在协议错误时立即报错。"""
    import threading
    from pathlib import Path
    stop = stop if stop is not None else threading.Event()
    start, index = time.monotonic(), 0
    reconnect = getattr(args, 'reconnect', False)
    while not stop.is_set():
        source = _session(args, stop)
        try:
            for _, _, gray in source:
                if reconnect:
                    health = {'state': 'connected', 'boot': args.board_result['boot'],
                              'seq': args.board_result['seq'], 'updated_unix': time.time()}
                    path = Path(args.out)/'connection.json'
                    tmp = path.with_suffix('.tmp')
                    tmp.write_text(json.dumps(health), encoding='utf-8')
                    tmp.replace(path)
                yield index, (time.monotonic()-start)*1000, gray
                index += 1
                if args.frames and index >= args.frames:
                    return
            return
        except (OSError, ValueError, TimeoutError) as exc:
            if not reconnect:
                raise
            mission = getattr(args, 'mission_controller', None)
            if mission is not None:
                mission.connection_lost()
            request = getattr(args, 'snapshot_request', None)
            if request is not None:
                request.set()  # 失败的原图请求不能让按钮永远停在等待中。
            event = {'state': 'disconnected', 'error': str(exc), 'updated_unix': time.time()}
            folder = Path(args.out)
            with (folder/'connection_events.jsonl').open('a', encoding='utf-8') as f:
                f.write(json.dumps(event)+'\n')
            tmp = folder/'connection.tmp'
            tmp.write_text(json.dumps(event), encoding='utf-8')
            tmp.replace(folder/'connection.json')
            print('USB_RECONNECT', str(exc), flush=True)
            # 板端发送超时为3秒；留足时间让残留发送结束，再重新同步。
            if stop.wait(4):
                return
        finally:
            source.close()
