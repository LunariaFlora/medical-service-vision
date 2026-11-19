"""顺序候选汇总与双重换组判据；不打开硬件。"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _folder in ('firmware', 'host'):
    if str(_ROOT / _folder) not in sys.path:
        sys.path.insert(0, str(_ROOT / _folder))
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from mission import Mission, LocalInterfaces
import scan
import selftest


class MissionChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.m = Mission(LocalInterfaces(self.out), '13')
        self.a, self.b = '6944060407291', '6906841121017'

    def observe(self, value, now, appearance=False):
        records = [] if value is None else [{'type':'CODE128','payload':value}]
        return self.m.observe(records, self.m.capture_token(), now=now, barcode_like=appearance)

    def first(self):
        for t in (0, .6, 1.2):
            self.observe(self.a, t)

    def gap(self, appearance=False):
        for t in (2, 3, 4.1):
            self.observe(None, t, appearance)

    def second(self):
        for t in (4.5, 5.1, 5.7):
            self.observe(self.b, t)

    def test_all_routes_and_real_qr(self):
        for qr, beds in [('11',[1,3]),('13',[1,3]),('31',[3,1]),('33',[3,1])]:
            m=Mission(LocalInterfaces(self.out/qr), qr)
            self.assertEqual([s['bed'] for s in m.plan['stops']],beds)
        m=Mission(LocalInterfaces(self.out/'real'))
        self.assertFalse(m.set_qr('99'))
        self.assertTrue(m.observe([{'type':'QR','payload':'31'}],m.capture_token(),now=0))
        self.assertFalse(m.set_qr('13'))

    def test_first_frame_visible_and_no_arrival_required(self):
        self.assertTrue(self.observe(self.a,0))
        self.assertEqual(self.m.results,{'1':self.a,'3':None})
        self.assertTrue(self.m.snapshot()['provisional'])

    def test_gap_alone_same_code_does_not_advance(self):
        self.first(); self.gap(); self.observe(self.a,4.5)
        self.assertEqual(self.m.step,0)
        self.assertFalse(self.m.gap_ready)

    def test_difference_alone_and_one_outlier_do_not_advance(self):
        self.first(); self.second()
        self.assertEqual(self.m.step,0)
        self.assertIn(self.b,self.m.all_candidates)
        for t in (6,7,8.1): self.observe(None,t)
        self.observe('OTHER',8.5)
        self.assertEqual(self.m.step,0)

    def test_both_signals_advance_and_keep_two_rows(self):
        self.first(); self.gap(); self.second()
        self.assertEqual(self.m.results,{'1':self.a,'3':self.b})
        self.assertEqual(self.m.step,1)
        self.observe(None,8)
        self.assertEqual(self.m.results['1'],self.a)
        self.assertFalse(self.m.snapshot()['complete'])

    def test_bar_like_prevents_absence_evidence(self):
        self.first(); self.gap(True); self.second()
        self.assertEqual(self.m.step,0)
        self.assertFalse(self.m.gap_ready)

    def test_near_misread_is_candidate_not_second_bed_and_tie_pending(self):
        self.observe(self.a,0)
        similar='6944060407292'
        self.observe(similar,.4)
        self.assertIsNone(self.m.results['1'])
        self.observe(self.a,.8)
        self.assertEqual(self.m.results['1'],self.a)
        self.assertIn(similar,self.m.all_candidates)
        self.assertEqual(self.m.step,0)

    def test_multicode_all_recorded_not_arbitrarily_assigned(self):
        self.m.observe([{'type':'CODE128','payload':self.a},{'type':'CODE128','payload':self.b}],self.m.capture_token(),now=0)
        self.assertEqual(set(self.m.all_candidates),{self.a,self.b})
        self.assertEqual(self.m.results,{'1':None,'3':None})
        state=self.m.snapshot(); state['all_candidates'].clear()
        self.assertEqual(len(self.m.all_candidates),2)

    def test_real_decode_pipeline_raw_journal_and_timings(self):
        samples=selftest.Checks(); samples.setUp()
        original=samples.canvas(samples.bar)
        args=SimpleNamespace(usb=None,input='synthetic',roi=None,code='BOTH',out=str(self.out),mission_controller=self.m)
        with patch.object(scan,'images',return_value=iter([(0,0,original),(1,500,original)])):
            report=scan.collect(args)
        rows=[json.loads(x) for x in (self.out/'observations.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows),2)
        self.assertEqual(self.m.results['1'],'6946522463487')
        self.assertIn('capture_ms',report['timing_mean_ms'])
        self.assertEqual(self.m.groups[0]['6946522463487']['hits'],2)
        self.assertTrue(scan.barcode_like(original))
        self.assertFalse(scan.barcode_like(scan.np.full((480,640),255,scan.np.uint8)))


class ResultTransportChecks(unittest.TestCase):
    def packet(self,row):
        import struct,zlib
        body=json.dumps(row).encode()
        return struct.pack('<4sII',b'RCD1',len(body),zlib.crc32(body)&0xffffffff)+body

    def test_fragmented_crc_and_empty(self):
        from result_transport import receive_result
        row={'protocol':1,'boot':'a','seq':0,'records':[],'errors':{},'barcode_like':False,'board_ms':123}
        data=self.packet(row)
        got=receive_result(selftest.Fragmented(data))
        self.assertEqual(got['records'],[])
        self.assertFalse(got['barcode_like'])
        with self.assertRaises(ValueError):
            receive_result(selftest.Fragmented(data[:-1]+b'x'))
        row['records']=[{'type':'CODE128','payload':123,'valid':True}]
        with self.assertRaises(ValueError): receive_result(selftest.Fragmented(self.packet(row)))

    def test_result_only_collect_reuses_mission(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            m=Mission(LocalInterfaces(out),'13')
            raw={'records':[{'type':'CODE128','payload':'6944060407291','valid':True}],
                 'errors':{},'barcode_like':True,'board_ms':150,'packet_bytes':240}
            args=SimpleNamespace(usb=None,usb_results='fake',input=None,roi=None,out=folder,
                                 board_result=raw,mission_controller=m,code='BOTH')
            with patch.object(scan,'images',return_value=iter([(0,0,None),(1,150,None)])):
                report=scan.collect(args)
            self.assertEqual(m.groups[0]['6944060407291']['hits'],2)
            self.assertEqual(report['frames_with_decode'],2)
            self.assertFalse(list(out.glob('*.png')))

    def test_barcode_phase_skips_qr_but_keeps_binary_fallback(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'firmware'))
        import h7_usb_results as fw
        with patch.object(fw,'scan_qr') as qr, patch.object(fw,'scan_barcodes',return_value=[]) as bar, patch.object(fw,'stripe_hint',return_value=False):
            stages={}
            records,errors,hint=fw.sample(object(),include_qr=False,timings=stages)
        qr.assert_not_called()
        self.assertEqual(bar.call_args.kwargs['binary_threshold'],160)
        self.assertEqual(stages['qr_ms'],0)
        self.assertFalse(hint)
        self.assertFalse(errors)

    def test_snapshot_protocol_pairs_image_and_results(self):
        import io, threading
        from result_transport import frames
        from wire import packet
        class Port:
            def __init__(self,data): self.data=io.BytesIO(data); self.commands=[]
            def read(self,n): return self.data.read(n)
            def write(self,data): self.commands.append(data); return len(data)
            def reset_input_buffer(self): pass
            def close(self): pass
        row={'protocol':1,'boot':'a','seq':5,'records':[],'errors':{},'barcode_like':False,'board_ms':123,'has_image':True}
        for image_seq in (5,6):
            port=Port(self.packet(row)+packet(bytes([1,2,3,4]),2,2,image_seq))
            event=threading.Event(); event.set()
            args=SimpleNamespace(usb_results='test',frames=1,code='BAR',snapshot_request=event)
            with patch.object(scan.serial,'Serial',return_value=port):
                if image_seq==6:
                    with self.assertRaises(ValueError): list(frames(args))
                else:
                    rows=list(frames(args))
                    self.assertEqual(rows[0][2].tolist(),[[1,2],[3,4]])
                    self.assertEqual(port.commands,[b'PICB'])
                    self.assertFalse(event.is_set())

    def test_saved_original_and_decoder_comparison_share_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            samples=selftest.Checks(); samples.setUp(); original=samples.canvas(samples.bar)
            raw={'records':[], 'errors':{},'barcode_like':True,'board_ms':150,'packet_bytes':140,'seq':7}
            args=SimpleNamespace(usb=None,usb_results='fake',input=None,roi=None,out=folder,board_result=raw,code='BOTH')
            with patch.object(scan,'images',return_value=iter([(0,0,original)])):
                scan.collect(args)
            item=json.loads((Path(folder)/'original_00000000.json').read_text(encoding='utf-8'))
            self.assertEqual(item['records'],[])
            self.assertEqual(item['host_comparison'][0]['payload'],'6946522463487')
            saved=scan.np.array(scan.Image.open(Path(folder)/'original_00000000.png'))
            self.assertTrue(scan.np.array_equal(saved,original))

    def test_firmware_stripes_and_decoder_errors(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'firmware'))
        import h7_usb_results as fw
        class Frame:
            def width(self): return 640
            def height(self): return 480
            def bytearray(self): return bytes([255])*640*480
        self.assertFalse(fw.stripe_hint(Frame()))
        with patch.object(fw,'scan_qr',return_value=[]), patch.object(fw,'scan_barcodes',side_effect=ValueError('bad')):
            records,errors,hint=fw.sample(Frame())
        self.assertIn('BAR',errors)
        self.assertIsNone(hint)


if __name__ == '__main__':
    unittest.main()
