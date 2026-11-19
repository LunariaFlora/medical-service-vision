"""主机任务层：二维码定顺序，间隔及稳定差异推测分组；不控制车辆。"""
import copy
import json
from pathlib import Path
import sys
import threading
import uuid

# 复用已经测试过的二维码含义解析，不再维护另一套11/13/31/33映射。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware.qr_scanner import parse_qr


class LocalInterfaces:
    """未来对接位置。当前只落地接口样本，绝不声称已发给队友或导航。"""
    def __init__(self, output):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)

    def _record(self, destination, message):
        row = {'destination': destination, 'delivery': 'LOCAL_ONLY', 'message': message}
        with (self.output / 'interface_events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        print('LOCAL_ONLY', destination, json.dumps(message, ensure_ascii=False), flush=True)

    def publish_plan(self, plan):
        """给路径规划队友：后续在此接ROS/进程消息，不是直接发电机命令。"""
        self._record('navigation', plan)

    def publish_results(self, snapshot):
        """给显示队友：后续在此接双方确认的串口协议；当前不打开串口。"""
        self._record('display', snapshot)

    def save_state(self, snapshot):
        # 单个JSON保存当前两床状态；临时文件替换，避免读取半份状态。
        tmp = self.output / 'mission_state.tmp'
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.output / 'mission_state.json')


def different(a, b):
    """编辑距离超过30%才作为明显不同候选；不修正/拼接任何payload。"""
    previous = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(x != y)))
        previous = current
    return previous[-1] / max(len(a), len(b), 1) > 0.30


class Mission:
    # 台架启发式默认值，不是比赛规则或已标定的可靠率。
    GAP_SECONDS = 2.0
    GAP_FRAMES = 3
    STABLE_HITS = 3
    STABLE_SECONDS = 1.0

    def __init__(self, interfaces, mock_qr=None):
        self.lock = threading.RLock()
        self.interfaces = interfaces
        self.run_id = uuid.uuid4().hex
        self.plan = None
        self.phase = 'WAIT_QR'
        self.step = 0
        self.results = {'1': None, '3': None}
        self.groups = [{}, {}]
        self.all_candidates = {}
        self.pending = {}
        self.generation = 0
        self.revision = 0
        self.empty_since = None
        self.empty_frames = 0
        self.gap_ready = False
        self.reason = '等待护士台二维码'
        if mock_qr is not None and not self.set_qr(mock_qr, source='PRESET'):
            raise ValueError('Invalid preset QR')

    def set_qr(self, payload, source='CAMERA'):
        with self.lock:
            parsed = parse_qr(payload)
            if self.plan is not None or not parsed['valid']:
                return False
            self.plan = {'event': 'route_plan', 'run_id': self.run_id,
                         'qr': payload, 'source': source,
                         'stops': [{'bed': parsed['first_bed'], 'box': parsed['first_box']},
                                   {'bed': parsed['next_bed'], 'box': parsed['next_box']}]}
            self.phase = 'COLLECTING'
            self.reason = '自动收集第一床候选（顺序推测）'
            self.generation += 1
            self.interfaces.publish_plan(copy.deepcopy(self.plan))
            self.interfaces.save_state(self.snapshot())
            return True

    def capture_token(self):
        with self.lock:
            return self.generation

    def connection_lost(self):
        """断线不是相机看到了空场景；保留已读结果，清除跨断线换组线索。"""
        with self.lock:
            self.empty_since, self.empty_frames = None, 0
            self.gap_ready = False
            self.pending = {}
            self.reason = '连接中断，保留候选；等待恢复采集'
            self.interfaces.save_state(self.snapshot())

    @staticmethod
    def add(bucket, payload, now):
        row = bucket.setdefault(payload, {'hits': 0, 'first_s': now, 'last_s': now})
        row['hits'] += 1
        row['last_s'] = now

    @staticmethod
    def winner(bucket):
        ranked = sorted(bucket, key=lambda p: -bucket[p]['hits'])
        if not ranked or (len(ranked)>1 and bucket[ranked[0]]['hits'] == bucket[ranked[1]]['hits']):
            return None
        return ranked[0]

    def stable(self, bucket, payload):
        row = bucket[payload]
        return row['hits'] >= self.STABLE_HITS and row['last_s']-row['first_s'] >= self.STABLE_SECONDS

    def observe(self, records, token, now=None, barcode_like=None):
        """原始帧由scan先完整记录；所有条码计入全局候选，歧义不强行分床。"""
        import time
        now = time.monotonic() if now is None else now
        with self.lock:
            bars = [r for r in records if r['type'] == 'CODE128' and r['payload'] and r.get('valid', True)]
            # 每个内容每帧只计一次，避免同帧重复检测放大权重。
            values = set(r['payload'] for r in bars)
            for value in values:
                self.add(self.all_candidates, value, now)
            if token != self.generation:
                return False
            if self.phase == 'WAIT_QR':
                qr = [r for r in records if r['type'] == 'QR' and r.get('valid', True)]
                return len(qr) == 1 and self.set_qr(qr[0]['payload'])
            if not bars:
                # None = 未做外观检查，不能当作确认离场。
                if barcode_like is False:
                    if self.empty_since is None:
                        self.empty_since = now
                    self.empty_frames += 1
                    if self.empty_frames >= self.GAP_FRAMES and now-self.empty_since >= self.GAP_SECONDS:
                        if not self.gap_ready:
                            self.pending = {}  # 新间隔后重新收集稳定差异；旧候选仍在all_candidates。
                        self.gap_ready = True
                else:
                    self.empty_since, self.empty_frames = None, 0
                self.reason = '候选保留；等待条码' if not barcode_like else '疑似条纹仍在，未判定离场'
                self.interfaces.save_state(self.snapshot())
                return False
            self.empty_since, self.empty_frames = None, 0
            if len(bars) != 1:
                self.reason = '多码均已记录，无法确定先后归属'
                self.interfaces.save_state(self.snapshot())
                return False
            value = bars[0]['payload']
            bucket = self.groups[self.step]
            best = self.winner(bucket)
            if self.step == 0 and best and self.stable(bucket, best) and different(best, value):
                self.add(self.pending, value, now)
                challenger = self.winner(self.pending)
                if self.gap_ready and challenger == value and self.stable(self.pending, challenger):
                    self.step = 1
                    self.groups[1] = self.pending
                    self.pending = {}
                    self.gap_ready = False
                    self.reason = '间隔及稳定差异成立，开始第二床候选（推测）'
                else:
                    self.reason = '不同内容已保留，等待间隔与稳定差异共同成立'
            else:
                self.add(bucket, value, now)
                # 回到第一床的相同/近似内容：撤销离场线索，防止抖动换床。
                if self.step == 0:
                    self.gap_ready = False
                    self.pending = {}
                self.reason = '按累计次数选择候选；同分待定（非最终确认）'
            old = dict(self.results)
            for index, group in enumerate(self.groups):
                bed = str(self.plan['stops'][index]['bed'])
                self.results[bed] = self.winner(group)
            changed = old != self.results
            if changed:
                self.revision += 1
            state = self.snapshot()
            self.interfaces.save_state(state)
            if changed:
                self.interfaces.publish_results(state)
            return changed

    def snapshot(self):
        with self.lock:
            return {'event': 'bed_results', 'run_id': self.run_id,
                    'revision': self.revision, 'plan': copy.deepcopy(self.plan),
                    'phase': self.phase, 'reason': self.reason,
                    'expected_bed': self.plan['stops'][self.step]['bed'] if self.plan else None,
                    'beds': dict(self.results), 'complete': False,
                    'association': 'ORDER_HEURISTIC', 'provisional': True,
                    'gap_ready': self.gap_ready, 'group_index': self.step,
                    'candidates': copy.deepcopy(self.groups),
                    'unassigned_candidates': copy.deepcopy(self.pending),
                    'all_candidates': copy.deepcopy(self.all_candidates)}
