"""Visible host decoding; Tk stays responsive while USB runs in a worker."""
import json
from pathlib import Path
import queue
import threading
import time


class LivePreview:
    def __init__(self, output, mission=None, result_mode=False):
        # Lazy import: offline/NUC runs do not require a graphical desktop.
        import tkinter as tk
        from PIL import Image, ImageDraw, ImageTk
        self.Image, self.ImageDraw, self.ImageTk = Image, ImageDraw, ImageTk
        self.output = Path(output)
        self.mission = mission
        self.result_mode = result_mode
        self.snapshot_request = threading.Event()
        self.snapshots = queue.Queue()
        self.have_snapshot = False
        self.stop = threading.Event()
        self.frames = queue.Queue(maxsize=1)
        self.finished = threading.Event()
        self.final_state = None
        self.current = None
        self.last_frame_at = time.monotonic()
        self.root = tk.Tk()
        self.root.title('相机实时识别')
        self.root.configure(bg='#17202a')
        self.status = tk.Label(self.root, text='等待相机画面…', font=('Microsoft YaHei', 18),
                               bg='#394657', fg='white', padx=12, pady=10)
        self.status.pack(fill='x')
        self.photo = ImageTk.PhotoImage(Image.new('RGB', (640, 480), '#17202a'))
        self.picture = tk.Label(self.root, image=self.photo, bg='#17202a')
        self.picture.pack(padx=12, pady=8)
        self.payload = tk.Label(self.root, text='—', font=('Microsoft YaHei', 13),
                                bg='#17202a', fg='white', justify='left', wraplength=640)
        self.payload.pack(fill='x', padx=12)
        self.info = tk.Label(self.root, text='尚未收到图像', bg='#17202a', fg='#c5ced8')
        self.info.pack(pady=6)
        if mission:
            self.task_label = tk.Label(self.root, font=('Microsoft YaHei', 12),
                                       bg='#17202a', fg='white', justify='left', wraplength=680)
            self.task_label.pack(fill='x', padx=12)
        controls = tk.Frame(self.root, bg='#17202a')
        controls.pack(pady=10)
        if result_mode:
            self.capture_button = tk.Button(controls, text='获取原图并保存', command=self.request_snapshot)
            self.capture_button.pack(side='left', padx=8)
        self.save_button = tk.Button(controls, text='保存当前图像 (S)', command=self.save,
                                     state='disabled', padx=15)
        self.save_button.pack(side='left', padx=8)
        tk.Button(controls, text='停止并关闭 (Esc)', command=self.close, padx=15).pack(side='left')
        self.notice = tk.Label(self.root, text='', bg='#17202a', fg='#c5ced8', wraplength=640)
        self.notice.pack(padx=12, pady=(0, 10))
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<Escape>', lambda event: self.close())
        self.root.bind('<s>', lambda event: self.save())
        self.root.bind('<S>', lambda event: self.save())
        self.root.after(40, self.poll)

    def request_snapshot(self):
        if not self.finished.is_set():
            self.snapshot_request.set()
            self.capture_button.configure(state='disabled')
            self.notice.configure(text='正在获取下一张原始帧及同帧解码结果…')

    def publish(self, gray, item):
        if self.result_mode and gray is not None:
            self.snapshots.put((gray.copy(),item))
        # Replace display backlog only; the worker logs every decoded frame.
        try:
            self.frames.get_nowait()
        except queue.Empty:
            pass
        self.frames.put_nowait((gray.copy() if gray is not None else None, item))

    def finish(self, state):
        self.final_state = state
        self.finished.set()

    def show(self, gray, item):
        self.current = gray, item
        self.last_frame_at = time.monotonic()
        records = item['records']
        if gray is not None:
            display = self.Image.fromarray(gray).convert('RGB')
            draw = self.ImageDraw.Draw(display)
            for record in ([] if self.result_mode else records):
                points = [tuple(p) for p in record['corners']]
                draw.line(points + points[:1], fill='#2ecc71', width=2)
            self.photo = self.ImageTk.PhotoImage(display)
            self.picture.configure(image=self.photo, text='', height=0)
            if self.result_mode:
                self.have_snapshot = True
                self.capture_button.configure(state='normal')
                self.notice.configure(text='已保存静态原图（不是实时画面）：'+item.get('original_file',''))
        elif not self.have_snapshot:
            self.picture.configure(image='', text='板端解码 · USB接收结果\n当前不传实时图像',
                                   fg='white', font=('Microsoft YaHei',16), height=5)
        self.status.configure(text='已读出' if records else '未读出',
                              bg='#18794e' if records else '#a52f39')
        self.payload.configure(text='\n'.join('%s：%s' % (r['type'], r['payload']) for r in records) or '—')
        self.info.configure(text='帧 %d  ·  %.1f 秒  ·  解码 %.1f ms' %
                            (item['frame'], item['time_ms'] / 1000, item['decode_ms']))
        self.save_button.configure(state='normal' if gray is not None else 'disabled')
        if self.result_mode:
            board = item['board_result']
            self.info.configure(text='帧 %d · 板端采集/解码 %.1f ms · 消息 %d 字节' % (item['frame'], board['board_ms'], board['packet_bytes']))
            if board['errors']:
                self.status.configure(text='板端处理异常', bg='#a52f39')
                self.payload.configure(text=str(board['errors']))

    def poll(self):
        try:
            self.show(*self.frames.get_nowait())
        except queue.Empty:
            pass
        try:
            snapshot, evidence = self.snapshots.get_nowait()
            # Show retained evidence after live result update, without replacing current result text.
            self.photo = self.ImageTk.PhotoImage(self.Image.fromarray(snapshot).convert('RGB'))
            self.picture.configure(image=self.photo, text='', height=0)
            self.have_snapshot = True
            self.capture_button.configure(state='normal')
            self.notice.configure(text='静态原图，帧 %d（不是实时画面）：%s' % (evidence['frame'],evidence['original_file']))
        except queue.Empty:
            pass
        if self.finished.is_set():
            if self.result_mode: self.capture_button.configure(state='disabled')
            error = self.final_state.startswith('error:')
            self.status.configure(text='采集或解码失败' if error else '采集已结束',
                                  bg='#a52f39' if error else '#394657')
            last = self.current[1]['records'] if self.current else []
            final_text = '最后一帧：' + ('\n'.join('%s：%s' % (r['type'], r['payload']) for r in last) or '未读出')
            self.payload.configure(text=self.final_state if error else final_text)
        elif time.monotonic() - self.last_frame_at > 1.5:
            self.status.configure(text='等待新画面…', bg='#394657')
            self.payload.configure(text='—')
        if self.mission:
            state = self.mission.snapshot()
            plan = state['plan']
            title = '等待真实二维码'
            if plan:
                steps = ' → '.join('%s床 / %s号药箱' % (s['bed'], s['box']) for s in plan['stops'])
                title = '%s二维码 %s：%s' % ('预设' if plan['source'] == 'PRESET' else '实扫', plan['qr'], steps)
            ranked = sorted(state['all_candidates'].items(), key=lambda x: -x[1]['hits'])
            candidate_text = '\n全部候选前六项：' + ('；'.join('%s ×%d' % (v,r['hits']) for v,r in ranked[:6]) or '尚无')
            self.task_label.configure(text='%s\n%s\n1床：%s\n3床：%s\n当前最佳候选（床位为顺序推测）；所有原始结果已记录' %
                                      (title, state['reason'], state['beds']['1'] or '未采集',
                                       state['beds']['3'] or '未采集') + candidate_text)
        self.root.after(40, self.poll)

    def save(self):
        if self.current is None or self.current[0] is None:
            return
        gray, item = self.current
        name = 'manual_%08d_%d' % (item['frame'], time.time_ns())
        try:
            self.output.mkdir(parents=True, exist_ok=True)
            path = self.output / (name + '.png')
            self.Image.fromarray(gray).save(path)
            path.with_suffix('.json').write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding='utf8')
            self.notice.configure(text='已保存当前图像：' + path.name)
        except OSError as exc:
            self.notice.configure(text='保存失败：' + str(exc))

    def close(self):
        self.stop.set()
        self.root.destroy()

    def mainloop(self):
        self.root.mainloop()
