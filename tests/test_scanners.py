"""电脑端检验解析、事件判定和调用顺序；不代表相机真实解码通过。"""
import ast
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'firmware'))
sys.path.insert(0, str(ROOT / 'tools'))
import main
from qr_scanner import parse_qr, scan_qr, QR_PAYLOADS
from barcode_scanner import parse_barcode, scan_barcodes, BAR_PAYLOADS
from result_filter import ResultFilter
from build_standalone import build_source


class Code:
    def __init__(self, value, code_type=128):
        self.value, self.code_type = value, code_type

    def payload(self):
        return self.value

    def type(self):
        return self.code_type

    def rect(self):
        return (10, 20, 100, 80)


class Frame:
    def __init__(self, qr=(), bar=(), fail_qr=False):
        self.qr, self.bar, self.fail_qr = qr, bar, fail_qr
        self.calls = []

    def find_qrcodes(self, **kwargs):
        assert 'draw' not in self.calls
        self.calls.append('QR')
        if self.fail_qr:
            raise RuntimeError('test decoder failure')
        return self.qr

    def find_barcodes(self, **kwargs):
        assert 'draw' not in self.calls
        self.calls.append('BAR')
        return self.bar

    def draw_rectangle(self, *args, **kwargs):
        self.calls.append('draw')

    def copy(self):
        return Frame(self.qr, self.bar)

    def binary(self, thresholds):
        assert thresholds == [(160, 255)]
        return self


def successes(events):
    return [e for e in events if e['event'] == 'OK']


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.image_patch = patch.dict(sys.modules, {'image': types.SimpleNamespace(CODE128=128)})
        self.image_patch.start()
        self.addCleanup(self.image_patch.stop)

    def test_all_qr_meanings(self):
        for value in QR_PAYLOADS:
            r = parse_qr(value)
            self.assertTrue(r['valid'])
            self.assertEqual((r['first_bed'], r['first_box']), tuple(map(int, value)))
            self.assertEqual(r['next_bed'] + r['first_bed'], 4)
            self.assertEqual(r['next_box'] + r['first_box'], 4)

    def test_qr_invalid_not_guessed_or_trimmed(self):
        for value in ('12', '11 ', 'patient11', '', '011', 'https://example.com'):
            r = parse_qr(value)
            self.assertFalse(r['valid'])
            self.assertNotIn('first_bed', r)

    def test_all_barcodes_and_wrong_type(self):
        for value in BAR_PAYLOADS:
            r = parse_barcode(value, True)
            self.assertTrue(r['valid'])
            self.assertIsInstance(r['payload'], str)
            self.assertNotIn('first_bed', r)
            self.assertFalse(parse_barcode(value, False)['valid'])

    def test_unknown_barcode_explicit_test_override(self):
        self.assertFalse(parse_barcode('1234567890123', True)['valid'])
        r = parse_barcode('1234567890123', True, False)
        self.assertTrue(r['valid'])
        self.assertFalse(r['known_sample'])
        self.assertFalse(parse_barcode('', True, False)['valid'])

    def test_method_and_attribute_firmware_result_styles(self):
        attr = types.SimpleNamespace(payload=BAR_PAYLOADS[0], type=128, rect=(1, 2, 3, 4))
        f = Frame(qr=[Code('11')], bar=[attr])
        self.assertEqual(scan_qr(f)[0]['payload'], '11')
        self.assertEqual(scan_barcodes(f)[0]['payload'], BAR_PAYLOADS[0])

    def test_binary_fallback_uses_copy_only_and_preserves_roi(self):
        events=[]
        roi=(0,100,640,160)
        class BinaryFrame:
            def binary(self, thresholds):
                events.append(('binary',thresholds))
            def find_barcodes(self, *, roi):
                events.append(('binary_decode',roi))
                return [Code(BAR_PAYLOADS[0])]
        class Original:
            def find_barcodes(self, *, roi):
                events.append(('raw',roi))
                return []
            def copy(self):
                events.append(('copy',))
                return BinaryFrame()
        found=scan_barcodes(Original(),roi,False,160)
        self.assertEqual(found[0]['method'],'BIN160')
        self.assertEqual(events,[('raw',roi),('copy',),('binary',[(160,255)]),('binary_decode',roi)])
        class RawSuccess:
            def find_barcodes(self): return [Code(BAR_PAYLOADS[0])]
            def copy(self): raise AssertionError('Unnecessary copy')
        self.assertEqual(scan_barcodes(RawSuccess(),None,False,160)[0]['method'],'RAW')

    def test_both_decoders_before_any_drawing(self):
        f = Frame([Code('11')], [Code(BAR_PAYLOADS[0])])
        records, timings, errors = main.decode_frame(f)
        self.assertEqual(f.calls, ['QR', 'BAR'])
        self.assertEqual(set(timings), {'QR', 'BAR'})
        self.assertFalse(errors)
        main.draw_results(f, records)
        self.assertEqual(f.calls, ['QR', 'BAR', 'draw', 'draw'])

    def test_decoder_failure_keeps_other_decoder_and_reports_error(self):
        f = Frame(bar=[Code(BAR_PAYLOADS[0])], fail_qr=True)
        records, _, errors = main.decode_frame(f)
        self.assertIn('QR', errors)
        self.assertEqual(records['QR'], [])
        self.assertTrue(records['BAR'][0]['valid'])

    def test_both_types_confirm_independently_same_frame(self):
        filters = {k: ResultFilter(k, 0) for k in ('QR', 'BAR')}
        output = []
        for now in (100, 200, 300):
            records, _, _ = main.decode_frame(Frame([Code('13')], [Code(BAR_PAYLOADS[0])]))
            for kind in filters:
                output += filters[kind].feed(records[kind], now)
        self.assertEqual([e['kind'] for e in successes(output)], ['QR', 'BAR'])

    def test_sequence_qr_bar_bar_qr_without_mode_change(self):
        filters = {k: ResultFilter(k, 0) for k in ('QR', 'BAR')}
        output, now = [], 0
        for kind, value in [('QR', '11'), ('BAR', BAR_PAYLOADS[0]),
                            ('BAR', BAR_PAYLOADS[1]), ('QR', '33')]:
            for _ in range(3):
                now += 100
                f = Frame([Code(value)] if kind == 'QR' else [],
                          [Code(value)] if kind == 'BAR' else [])
                records, _, _ = main.decode_frame(f)
                for k in filters:
                    output += filters[k].feed(records[k], now)
        self.assertEqual([e['payload'] for e in successes(output)],
                         ['11', BAR_PAYLOADS[0], BAR_PAYLOADS[1], '33'])

    def test_same_kind_ambiguity_does_not_block_other_kind(self):
        qf, bf = ResultFilter('QR', 0), ResultFilter('BAR', 0)
        qo, bo = [], []
        for now in (100, 200, 300):
            qo += qf.feed([parse_qr('11'), parse_qr('13')], now)
            bo += bf.feed([parse_barcode(BAR_PAYLOADS[0], True)], now)
        self.assertFalse(successes(qo))
        self.assertEqual(qo[0]['reason'], 'multiple_codes_of_same_kind')
        self.assertEqual(len(successes(bo)), 1)

    def test_missing_or_invalid_frame_resets_confirmation(self):
        for interrupted in ([], [parse_qr('12')]):
            f = ResultFilter('QR', 0)
            for now in (100, 200):
                self.assertFalse(successes(f.feed([parse_qr('11')], now)))
            f.feed(interrupted, 300)
            for now in (400, 500):
                self.assertFalse(successes(f.feed([parse_qr('11')], now)))
            self.assertEqual(len(successes(f.feed([parse_qr('11')], 600))), 1)

    def test_changed_payload_needs_own_confirmation(self):
        f = ResultFilter('QR', 0)
        for now in (100, 200):
            f.feed([parse_qr('11')], now)
        for now in (300, 400):
            self.assertFalse(successes(f.feed([parse_qr('13')], now)))
        self.assertEqual(successes(f.feed([parse_qr('13')], 500))[0]['payload'], '13')

    def test_repeat_suppression_and_rearm(self):
        f = ResultFilter('QR', 0)
        out = []
        for now in range(100, 1000, 100):
            out += f.feed([parse_qr('11')], now)
        self.assertEqual(len(successes(out)), 1)
        f.feed([], 2000)
        out = []
        for now in (2100, 2200, 2300):
            out += f.feed([parse_qr('11')], now)
        self.assertEqual(len(successes(out)), 1)

    def test_long_gap_is_not_consecutive_confirmation(self):
        f = ResultFilter('QR', 0)
        for now in (100, 200, 3000, 3100):
            self.assertFalse(successes(f.feed([parse_qr('11')], now)))
        self.assertEqual(len(successes(f.feed([parse_qr('11')], 3200))), 1)

    def test_wait_once_no_old_result_replayed(self):
        f = ResultFilter('QR', 0)
        for now in (100, 200, 300):
            f.feed([parse_qr('11')], now)
        self.assertEqual([e['event'] for e in f.feed([], 5300)], ['WAIT'])
        self.assertEqual(f.feed([], 6000), [])

    def test_rejected_content_is_visible_and_throttled(self):
        f = ResultFilter('QR', 0)
        r = [parse_qr('wrong')]
        self.assertEqual(f.feed(r, 100)[0]['payloads'], ('wrong',))
        self.assertEqual(f.feed(r, 200), [])
        self.assertEqual(f.feed(r, 1200), [])

    def test_generated_bundle_runs_same_pure_logic(self):
        source = build_source()
        for node in ast.parse(source).body:
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, ('config', 'qr_scanner', 'barcode_scanner', 'result_filter'))
        namespace = {'__name__': 'bundle_test'}
        exec(compile(source, '<generated>', 'exec'), namespace)
        f = Frame([Code('31')], [Code(BAR_PAYLOADS[2])])
        records, _, errors = namespace['decode_frame'](f)
        self.assertFalse(errors)
        qf = namespace['ResultFilter']('QR', 0)
        output = []
        for now in (100, 200, 300):
            output += qf.feed(records['QR'], now)
        self.assertEqual(namespace['format_event'](successes(output)[0]),
                         'OK QR payload=31 first_bed=3 first_box=1 next_bed=1 next_box=3')

    def test_continuous_default_only_prints_confirmed_results_and_keeps_running(self):
        import types
        record = parse_barcode(BAR_PAYLOADS[0], True)
        # Single hits emit immediately. Held code is deduplicated;
        # a one-second absence permits immediate output on reappearance.
        batches = [[record], [record], [], [record], [record], [record],
                   [record], [], [record], [record], [record]]
        sensor = types.SimpleNamespace(
            GRAYSCALE=1, VGA=2, reset=lambda: None,
            set_pixformat=lambda fmt: None, set_framesize=lambda size: None,
            set_auto_gain=lambda enabled, *, gain_db: None,
            set_auto_exposure=lambda enabled, *, exposure_us: None,
            skip_frames=lambda *, time: None,
            snapshot=unittest.mock.Mock(side_effect=[object() for _ in batches] + [KeyboardInterrupt()]))
        decoded = [({'QR': [], 'BAR': records}, {'QR': 1, 'BAR': 1}, {}) for records in batches]
        self.assertFalse(main.TEST_LOG)
        self.assertFalse(main.DEBUG_RAW)
        self.assertFalse(main.DEBUG_STATUS)
        printed = []
        with patch.dict(sys.modules, {'sensor': sensor}), \
             patch.object(main, 'decode_frame', side_effect=decoded), \
             patch.object(main, 'draw_results'), \
             patch.object(main, 'draw_status'), \
             patch.object(main, 'clock_ms', side_effect=[0,100,200,6000,6100,6200,6300,6400,8000,8100,8200,8300]), \
             patch.object(main.time, 'sleep_ms', create=True), \
             patch('builtins.print', side_effect=lambda line: printed.append((sensor.snapshot.call_count, line))):
            with self.assertRaises(KeyboardInterrupt):
                main.main()
        results = [(frame, line) for frame, line in printed if line.startswith('OK ')]
        self.assertEqual([frame for frame, line in results], [1, 4, 9])
        self.assertTrue(all(BAR_PAYLOADS[0] in line for frame, line in results))
        self.assertEqual(sensor.snapshot.call_count, 12)  # Stops only on external interrupt.
        self.assertEqual(len(printed), 5)  # Two startup lines, three single-frame reads.
        self.assertFalse(any(marker in line for frame, line in printed
                             for marker in ('#000', 'OBS ', 'WAIT ', 'COVERAGE', 'EXPECTED', 'LOST ')))

    @patch.object(main, 'CONFIRM_FRAMES', 3)
    def test_preview_shows_current_confirmation_not_stale_success(self):
        gates = {k: ResultFilter(k, 0) for k in ('QR', 'BAR')}
        record = parse_barcode(BAR_PAYLOADS[0], True)
        records = {'QR': [], 'BAR': [record]}
        for i in (1, 2, 3):
            gates['BAR'].feed([record], i * 100)
            lines = main.preview_status(records, gates, {}, i)
            self.assertEqual(lines[2], 'BAR: ' + ('CHECK %s/3' % i if i < 3 else 'OK ' + record['payload']))
        gates['BAR'].feed([], 400)
        lines = main.preview_status({'QR': [], 'BAR': []}, gates, {}, 4)
        self.assertEqual(lines[2], 'BAR: NO CODE')
        self.assertNotIn(record['payload'], ' '.join(lines))
        self.assertEqual(main.preview_status(records, gates, {'BAR': 'bad'}, 5)[2], 'BAR: ERROR')
        class StrictDisplay:
            def draw_rectangle(self, rect, *, color, fill):
                assert isinstance(rect, tuple) and len(rect) == 4
            def draw_string(self, args, *, color):
                # v4.3 py_image_draw_string consumes x, y, text together.
                if not isinstance(args, tuple) or len(args) != 3:
                    raise ValueError('requested length 3 but object has length 2')
                assert isinstance(args[0], int) and isinstance(args[1], int)
                assert isinstance(args[2], str)
        with self.assertRaisesRegex(ValueError, 'requested length 3'):
            StrictDisplay().draw_string((2, 0), color=255)
        main.draw_status(StrictDisplay(), lines)

    def test_both_board_bundles_run_with_strict_sensor_and_display_interfaces(self):
        # H7 v5.0.0 modules/py_image.c: draw_string takes (x,y), text;
        # OpenART's existing 4.3 interface takes (x,y,text).
        for board, enabled in (('OPENART', False), ('H7', False), ('H7', True)):
            with self.subTest(board=board, uart=enabled):
                ns = {'__name__': 'board_test'}
                exec(compile(build_source(board, enable_uart=enabled), '<board_bundle>', 'exec'), ns)
                wire = bytearray()
                class UART:
                    def write(self, data):
                        wire.extend(data[:2])
                        return min(2, len(data))
                sender = ns['H7PayloadUART'](UART()) if enabled else None
                class Display(Frame):
                    def draw_rectangle(self, rect, *, color, fill=False):
                        assert isinstance(rect, tuple) and len(rect) == 4
                        self.calls.append('draw')
                    def draw_string(self, coords, *text, color):
                        assert isinstance(coords, tuple)
                        if board == 'H7':
                            assert len(coords) == 2 and len(text) == 1
                            assert isinstance(text[0], str)
                        else:
                            assert len(coords) == 3 and not text
                            assert isinstance(coords[2], str)
                frames = [Display([Code('11')], [Code(BAR_PAYLOADS[0])]) for _ in range(4)]
                calls = []
                def gain(enabled, *, gain_db=None):
                    calls.append(('gain', enabled, gain_db))
                def exposure(enabled, *, exposure_us=None):
                    calls.append(('exposure', enabled, exposure_us))
                sensor = types.SimpleNamespace(
                    GRAYSCALE=1, VGA=2, reset=lambda: None,
                    set_pixformat=lambda fmt: self.assertEqual(fmt, 1),
                    set_framesize=lambda size: self.assertEqual(size, 2),
                    set_auto_gain=gain, set_auto_exposure=exposure,
                    skip_frames=lambda *, time: None,
                    snapshot=unittest.mock.Mock(side_effect=frames + [KeyboardInterrupt()]))
                emitted = []
                with patch.dict(sys.modules, {'sensor': sensor}), \
                     patch.object(ns['time'], 'sleep_ms', create=True), \
                     patch('builtins.print', side_effect=lambda line: emitted.append((sensor.snapshot.call_count, line))) as output:
                    with self.assertRaises(KeyboardInterrupt):
                        ns['main'](sender=sender)
                lines = [call.args[0] for call in output.call_args_list]
                self.assertEqual([frame for frame, line in emitted if line.startswith('OK ')], [1, 1])
                self.assertEqual(ns['CONFIRM_FRAMES'], 1)
                self.assertIn('confirm=1 frames', lines[0])
                self.assertEqual(sum(line.startswith('OK QR ') for line in lines), 1)
                self.assertEqual(sum(line.startswith('OK BAR ') for line in lines), 1)
                self.assertEqual(len(lines), 4)
                self.assertIn('board=' + board, lines[0])
                self.assertEqual(calls, [('gain', True, None), ('exposure', True, None)]
                                 if board == 'H7' else
                                 [('gain', False, 0), ('exposure', False, 40)])
                self.assertEqual(wire, ('11\r\n' + BAR_PAYLOADS[0] + '\r\n').encode() if enabled else b'')
                self.assertEqual(sensor.snapshot.call_count, 5)
                self.assertTrue(all(frame.calls[:2] == ['QR', 'BAR'] for frame in frames))

    def test_test_log_transient_is_not_confirmation(self):
        reporter = main.TestReporter(0)
        gate = ResultFilter('QR', 0)
        with patch('builtins.print') as output:
            for now, records in ((100, [parse_qr('13')]), (200, []), (900, [])):
                reporter.observe('QR', records, now, 15)
                for event in gate.feed(records, now):
                    reporter.event(event, now)
        lines = [str(call.args[0]) for call in output.call_args_list]
        self.assertTrue(any('OBS QR' in line for line in lines))
        self.assertTrue(any('LOST QR' in line and 'cause=unknown' in line for line in lines))
        self.assertFalse(any('OK QR' in line or 'COVERAGE' in line for line in lines))
        self.assertEqual(reporter.seen['QR'], [])

    def test_test_log_coverage_counts_unique_confirmed_values_only(self):
        reporter = main.TestReporter(0)
        gate = ResultFilter('QR', 0)
        with patch('builtins.print') as output:
            reporter.expectations()
            # 两个同类码不计入覆盖；同一值移开再呈现可以再次OK，但不重复计数。
            for now, records in ((100, [parse_qr('11'), parse_qr('13')]),
                                 (200, [parse_qr('13')]), (300, [parse_qr('13')]),
                                 (400, [parse_qr('13')]), (1800, []),
                                 (1900, [parse_qr('13')]), (2000, [parse_qr('13')]),
                                 (2100, [parse_qr('13')])):
                reporter.observe('QR', records, now, 15)
                for event in gate.feed(records, now):
                    reporter.event(event, now)
        lines = [str(call.args[0]) for call in output.call_args_list]
        self.assertEqual(sum('OK QR' in line for line in lines), 2)
        coverage = [line for line in lines if 'COVERAGE' in line]
        self.assertEqual(len(coverage), 1)
        self.assertIn('observed_QR=1/4', coverage[0])
        self.assertIn('physical_match=NOT_VERIFIED', coverage[0])
        self.assertEqual(reporter.seen['QR'], ['13'])


if __name__ == '__main__':
    unittest.main()
