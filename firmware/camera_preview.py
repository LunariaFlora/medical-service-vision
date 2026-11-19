# 在OpenMV IDE运行：持续原图预览，点停止结束。
# 不解码、不扫曝光、不画标记、不存图；旧排查程序保留在Git 3acc824。
EXPOSURE_US = 40  # 请求值；当前逐飞固件的实际微秒值未核实。


def main(requested=EXPOSURE_US):
    import sensor
    import time

    sensor.reset()
    sensor.set_pixformat(sensor.GRAYSCALE)
    sensor.set_framesize(sensor.VGA)
    sensor.set_auto_gain(False, gain_db=0)
    sensor.set_auto_exposure(False, exposure_us=requested)
    print('PREVIEW_ONLY | VGA 640x480 | exposure_request=%s | until IDE Stop' % requested)
    while True:
        sensor.snapshot()
        time.sleep_ms(30)


if __name__ == '__main__':
    # 保留已有电脑取图入口传入固定曝光的方式。
    main(globals().get('PREVIEW_EXPOSURE_US', EXPOSURE_US))
