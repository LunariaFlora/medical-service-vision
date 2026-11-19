"""主机替身只检查诊断控制流程，不能证明相机、固件或识别能力。"""
import contextlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest

PATH = Path(__file__).resolve().parents[1] / 'firmware/diagnostics/barcode_resolution_test.py'
spec = importlib.util.spec_from_file_location('resolution', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Frame:
    def __init__(self, size, copy_error=False, interrupt=False):
        self.size = size
        self.copy_error = copy_error
        self.interrupt = interrupt
        self.modified = False

    def width(self):
        return self.size[0]

    def height(self):
        return self.size[1]

    def find_barcodes(self):
        if self.interrupt:
            raise Exception('IDE interrupt')
        return []

    def copy(self):
        if self.copy_error:
            raise MemoryError('test heap exhausted')
        return Frame(self.size)

    def binary(self, thresholds):
        self.modified = True


class Sensor:
    GRAYSCALE = 1
    VGA = (640, 480)
    SVGA = (800, 600)
    XGA = (1024, 768)
    SXGA = (1280, 1024)

    def __init__(self, unsupported=None, actual=None, copy_error=False, interrupt=False):
        self.unsupported, self.actual = unsupported, actual
        self.copy_error, self.interrupt = copy_error, interrupt
        self.calls, self.frames = [], []

    def reset(self):
        self.calls.append('reset')
        self.window = None

    def set_pixformat(self, fmt):
        pass

    def set_framesize(self, size):
        if size == self.unsupported:
            raise ValueError('unsupported mode')
        self.size = size

    def set_windowing(self, window):
        self.window = window

    def set_auto_gain(self, enabled, gain_db):
        self.calls.append(('gain', enabled, gain_db))

    def set_auto_exposure(self, enabled, exposure_us):
        self.calls.append(('exposure', enabled, exposure_us))

    def skip_frames(self, time):
        pass

    def snapshot(self):
        frame = Frame(self.actual or self.window or self.size, self.copy_error, self.interrupt)
        self.frames.append(frame)
        return frame


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        module.time = SimpleNamespace(ticks_ms=lambda: 0, ticks_diff=lambda a, b: a-b,
                                     sleep_ms=lambda ms: None)
        self.log = io.StringIO()
        self.output = contextlib.redirect_stdout(self.log)
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)

    def test_same_frame_copy_and_explicit_full_sequence(self):
        sensor = Sensor()
        results = module.run_test(sensor, module.PROFILES)
        self.assertEqual(len(sensor.frames), 18)  # 两种处理共用同一次snapshot。
        self.assertTrue(all(not f.modified for f in sensor.frames))
        self.assertTrue(all(r['frames'] == 3 and r['state'] == 'COMPLETE' for r in results))
        self.assertEqual(sensor.calls.count(('exposure', False, 250)), 6)
        self.assertEqual(sensor.calls.count(('gain', False, 0)), 6)
        self.assertEqual(sensor.window, None)  # 返回VGA时无残留裁剪。

    def test_unsupported_mode_does_not_block_later_modes(self):
        results = module.run_test(Sensor(unsupported=Sensor.XGA), module.PROFILES)
        self.assertEqual(results[2]['state'], 'CONFIG_ERROR')
        self.assertEqual(results[-1]['state'], 'COMPLETE')

    def test_silent_resize_not_counted_as_requested_resolution(self):
        result = module.run_profile(Sensor(actual=(640, 480)), module.PROFILES[3])
        self.assertEqual(result['state'], 'SIZE_MISMATCH')
        self.assertEqual(result['frames'], 0)

    def test_copy_memory_error_keeps_raw_path(self):
        result = module.run_profile(Sensor(copy_error=True), module.PROFILES[0])
        self.assertEqual(result['frames'], 3)
        self.assertEqual(result['raw_errors'], 0)
        self.assertEqual(result['bin_errors'], 3)
        self.assertEqual(result['state'], 'COMPLETE_WITH_ERRORS')

    def test_ide_stop_propagates(self):
        with self.assertRaisesRegex(Exception, 'IDE interrupt'):
            module.run_test(Sensor(interrupt=True))

    def test_default_runs_single_svga_only(self):
        sensor = Sensor()
        results = module.run_test(sensor)
        self.assertEqual([r['profile'] for r in results], ['SVGA'])
        self.assertEqual(len(sensor.frames), 3)
        self.assertEqual(sensor.calls.count('reset'), 1)

    def test_step_logs_locate_failed_configuration(self):
        result = module.run_profile(Sensor(unsupported=Sensor.SVGA), module.PROFILES[1])
        self.assertEqual(result['state'], 'CONFIG_ERROR')
        log = self.log.getvalue()
        self.assertIn('STEP SVGA framesize BEGIN', log)
        self.assertIn('STEP SVGA framesize FAIL', log)
        self.assertNotIn('STEP SVGA framesize DONE', log)
        self.assertNotIn('STEP SVGA skip_frames BEGIN', log)


if __name__ == '__main__':
    unittest.main()
