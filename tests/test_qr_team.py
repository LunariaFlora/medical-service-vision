"""真实ZXing解码和HTTP联通；相机驱动使用有签名约束的替身。"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _folder in ('firmware', 'host'):
    if str(_ROOT / _folder) not in sys.path:
        sys.path.insert(0, str(_ROOT / _folder))
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from urllib.request import urlopen
from unittest.mock import patch

import app
import scan
from mission import Mission, LocalInterfaces
from qr_camera import capture, select_qr, QRCamera
from team_api import TeamAPI


def synthetic_worker(config, output, messages, stop):
    """只在测试中替代摄像头驱动，真正启动spawn子进程及ZXing解码。"""
    pixels=scan.np.asarray(scan.zxingcpp.create_barcode('31',scan.zxingcpp.BarcodeFormat.QRCode).to_image(scale=5))
    messages.put({'state':'frame','records':scan.decode(pixels,'QR')})


class QRTeamChecks(unittest.TestCase):
    def image(self, value):
        return scan.np.asarray(scan.zxingcpp.create_barcode(
            value, scan.zxingcpp.BarcodeFormat.QRCode).to_image(scale=5))

    def test_four_qr_images_to_http_route(self):
        for payload, stops in [('11',[(1,1),(3,3)]),('13',[(1,3),(3,1)]),
                               ('31',[(3,1),(1,3)]),('33',[(3,3),(1,1)])]:
            with tempfile.TemporaryDirectory() as directory:
                mission=Mission(LocalInterfaces(directory))
                api=TeamAPI(mission,0); api.start()
                try:
                    url='http://127.0.0.1:%d/v1/route'%api.server.server_port
                    with urlopen(url,timeout=2) as response:
                        self.assertFalse(json.load(response)['ready'])
                    decoded=select_qr(scan.decode(self.image(payload),'QR'))
                    self.assertTrue(mission.set_qr(decoded,source='USB_CAMERA'))
                    with urlopen(url,timeout=2) as response:
                        row=json.load(response)
                    self.assertEqual(row['plan']['qr'],payload)
                    self.assertEqual([(s['bed'],s['box']) for s in row['plan']['stops']],stops)
                    self.assertFalse(mission.set_qr('13'))
                finally: api.close()

    def test_blank_invalid_and_ambiguous(self):
        self.assertIsNone(select_qr(scan.decode(scan.np.full((100,100),255,scan.np.uint8),'QR')))
        self.assertIsNone(select_qr(scan.decode(self.image('99'),'QR')))
        self.assertIsNone(select_qr([{'type':'QR','payload':'13'},{'type':'QR','payload':'31'}]))
        self.assertIsNone(select_qr([{'type':'QR','payload':'13'},{'type':'QR','payload':'99'}]))
        self.assertEqual(select_qr([{'type':'QR','payload':'13'}]*2),'13')

    def test_capture_logs_before_delivery_and_releases_device(self):
        image=self.image('31')
        stop=threading.Event(); messages=queue.Queue()
        class Camera:
            released=False
            def isOpened(self): return True
            def set(self,prop,value): return False  # Unsupported requested settings must not be claimed actual.
            def get(self,prop): return 0
            def read(self): stop.set(); return True,image
            def release(self): self.released=True
        camera=Camera()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(scan.cv2,'VideoCapture',return_value=camera):
                capture({'device':0,'width':1920,'height':1080,'fps':30,'fourcc':'MJPG'},directory,messages,stop)
            rows=[json.loads(s) for s in (Path(directory)/'qr_observations.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(select_qr(rows[0]['records']),'31')
            self.assertEqual(messages.get()['actual']['width'],0)
            self.assertEqual(select_qr(messages.get()['records']),'31')
            self.assertTrue((Path(directory)/'qr_latest.png').exists())
            self.assertTrue(camera.released)

    def test_config_cannot_mix_preset_and_real_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'config.json'
            config={'port':'COM9','preset_qr':'13','preview':False,'output_dir':'data',
                    'qr_camera':{'device':0,'width':1920,'height':1080,'fps':30,'fourcc':'MJPG'}}
            file.write_text(json.dumps(config),encoding='utf-8')
            with self.assertRaises(ValueError): app.load_config(file)
            config['preset_qr']=None
            file.write_text(json.dumps(config),encoding='utf-8')
            self.assertIsNotNone(app.load_config(file)['qr_camera'])

    def test_supervisor_delivers_real_route_and_releases_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            mission=Mission(LocalInterfaces(directory))
            messages=queue.Queue()
            messages.close=lambda: None
            class Process:
                def start(self):
                    messages.put({'state':'frame','records':[{'type':'QR','payload':'31'}]})
                def join(self,timeout): pass
                def is_alive(self): return False
            process=Process()
            class Context:
                def Queue(self,maxsize): return messages
                def Event(self): return threading.Event()
                def Process(self,target,args,daemon): return process
            reader=QRCamera({},mission,directory)
            with patch('qr_camera.multiprocessing.get_context',return_value=Context()):
                reader.run()
            self.assertEqual(mission.plan['qr'],'31')
            self.assertEqual(mission.plan['source'],'USB_CAMERA')
            self.assertEqual(json.loads((Path(directory)/'qr_status.json').read_text())['state'],'locked')

    def test_http_state_keeps_both_beds(self):
        with tempfile.TemporaryDirectory() as directory:
            mission=Mission(LocalInterfaces(directory),'13')
            def observe(value, t):
                mission.observe([] if value is None else [{'type':'CODE128','payload':value}],
                                mission.capture_token(),now=t,barcode_like=False)
            for t in (0,.6,1.2): observe('6944060407291',t)
            for t in (2,3,4.1): observe(None,t)
            for t in (4.5,5.1,5.7): observe('6906841121017',t)
            api=TeamAPI(mission,0); api.start()
            try:
                with urlopen('http://127.0.0.1:%d/v1/state'%api.server.server_port,timeout=2) as response:
                    state=json.load(response)
                self.assertEqual(state['beds'],{'1':'6944060407291','3':'6906841121017'})
                self.assertTrue(state['provisional'])
            finally: api.close()

    def test_spawn_process_to_mission(self):
        with tempfile.TemporaryDirectory() as directory:
            mission=Mission(LocalInterfaces(directory))
            reader=QRCamera({},mission,directory,capture_target=synthetic_worker)
            reader.start()
            try:
                reader.thread.join(timeout=12)
                self.assertFalse(reader.thread.is_alive())
                self.assertEqual(mission.plan['qr'],'31')
            finally: reader.close()


if __name__ == '__main__':
    unittest.main()
