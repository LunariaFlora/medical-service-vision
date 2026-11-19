"""当前功能验证参数。两种码每轮都检测，不设置QR/BAR二选一开关。"""

BOARD_PROFILE = 'OPENART'  # H7合并版由构建器载入config_h7.py覆盖板级参数。
AUTO_GAIN = False

EXPOSURE_MODE = 'MANUAL' # 用户看完纸质曝光组后选择手动曝光作为当前默认。
EXPOSURE_US = 40         # 2026-10-09换镜头合焦后沿用纯预览请求值，实际微秒未核实。
GAIN_DB = 0             # 与纸质曝光组一致，锁定增益以减少变量。
QR_ROI = None           # None搜索全幅；或(x, y, w, h)。
BAR_ROI = None          # 与二维码独立配置，不要求码必须居中。
BAR_BINARY_THRESHOLD = 160  # 原图未解出时，只对副本尝试旧BIN160；None关闭。
STRICT_BAR_SAMPLES = False  # 本轮测试不按6个样本拒绝Code128；原始解码始终可见。
CONFIRM_FRAMES = 1  # 两块板测试均单帧有效即输出；持续可见的相同码仍去重。
MAX_CONFIRM_GAP_MS = 2000  # 两次采样隔太久，不累计为连续确认。
REARM_MS = 1000         # 合法结果消失一秒后，可再次报告同一值。
WAIT_MS = 5000          # 每类未确认结果时只提示一次，不持续刷屏。
DEBUG_RAW = False       # True：保留的逐帧原始解码信息。
DEBUG_STATUS = False    # True：保留的每秒状态信息。
TEST_LOG = False         # 人工整批测试：打印预期集合、解码变化、带时间事件及覆盖情况。
DRAW_BOXES = True       # 必须在两种解码全部完成之后画框。


def validate_config():
    if EXPOSURE_MODE not in ('AUTO', 'MANUAL'):
        raise ValueError('EXPOSURE_MODE must be AUTO or MANUAL')
    if EXPOSURE_US <= 0 or CONFIRM_FRAMES < 1:
        raise ValueError('Exposure and confirmation count must be positive')
    if min(REARM_MS, WAIT_MS, MAX_CONFIRM_GAP_MS) <= 0:
        raise ValueError('Timing parameters must be positive')
