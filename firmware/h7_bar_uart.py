"""H7自主扫条码 -> UART3；不等待USB请求、不推断床位。"""
from barcode_scanner import scan_barcodes
from h7_uart_output import H7PayloadUART


def send_frame(frame, sender):
    # 每帧只解原图一次；失败即进入下一帧，不复制或二值化重试。
    records = scan_barcodes(frame, strict_samples=False)
    for row in records:
        if row['valid']:
            # 每个实际解出的内容都发送；多码不任取一个，重复帧不丢弃。
            sender.send({'event':'OK', 'valid':True, 'payload':row['payload']})
            print('BAR', row['payload'])
    return records


def main():
    import sensor
    sender = H7PayloadUART()
    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.VGA)
    sensor.set_auto_exposure(True)
    sensor.set_auto_gain(True)
    sensor.skip_frames(time=2000)
    print('H7 BAR UART3 | 115200 8N1 | payload CRLF | VGA AUTO | RAW_ONLY')
    while True:
        send_frame(sensor.snapshot(), sender)


if __name__ == '__main__':
    main()
