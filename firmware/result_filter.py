"""两类识别器各用一个独立实例，防止一类结果影响另一类。"""
import time


def ticks_elapsed(now, then):
    return time.ticks_diff(now, then) if hasattr(time, 'ticks_diff') else now - then


class ResultFilter:
    def __init__(self, kind, now, confirm_frames=3, rearm_ms=1000,
                 wait_ms=5000, max_gap_ms=2000):
        self.kind = kind
        self.confirm_frames = confirm_frames
        self.rearm_ms, self.wait_ms, self.max_gap_ms = rearm_ms, wait_ms, max_gap_ms
        self.candidate, self.streak, self.accepted = None, 0, None
        self.last_valid = self.last_frame = self.wait_from = now
        self.wait_printed = False
        self.last_warning = None
        self.warning_at = None

    def feed(self, records, now):
        events = []
        if ticks_elapsed(now, self.last_valid) >= self.rearm_ms:
            self.accepted = None
        if ticks_elapsed(now, self.last_frame) > self.max_gap_ms:
            self.candidate, self.streak = None, 0
        self.last_frame = now
        valid = (len(records) == 1 and records[0]['kind'] == self.kind
                 and records[0]['valid'])
        if valid:
            record = records[0]
            payload = record['payload']
            self.last_valid, self.last_warning = now, None
            if payload == self.candidate:
                self.streak += 1
            else:
                self.candidate, self.streak = payload, 1
            if self.streak >= self.confirm_frames:
                self.wait_from, self.wait_printed = now, False
                if payload != self.accepted:
                    event = record.copy()
                    event['event'] = 'OK'
                    events.append(event)
                    self.accepted = payload
        else:
            self.candidate, self.streak = None, 0
            if records:
                reason = 'multiple_codes_of_same_kind' if len(records) > 1 else records[0].get('reason', 'unexpected_kind')
                values = tuple(r['payload'] for r in records)
                warning = (reason, values)
                if warning != self.last_warning and (self.warning_at is None or ticks_elapsed(now, self.warning_at) >= 1000):
                    events.append({'event': 'REJECT', 'kind': self.kind,
                                   'reason': reason, 'payloads': values})
                    self.last_warning, self.warning_at = warning, now
        if not self.wait_printed and ticks_elapsed(now, self.wait_from) >= self.wait_ms:
            events.append({'event': 'WAIT', 'kind': self.kind,
                           'reason': 'no_stable_valid_code'})
            self.wait_printed = True
        return events
