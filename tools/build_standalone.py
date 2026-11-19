"""把五个源文件合并为IDE可直接运行的临时单文件，不写板子。

用法：python tools/build_standalone.py
MicroPython可能无法挂载/sd；合并版不依赖从板端导入这些文件。
只删除已知的本项目模块import，其余源码按原样保留，不压缩、不改算法。
"""
import ast
import io
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('config', 'qr_scanner', 'barcode_scanner', 'result_filter', 'main')


def build_source(board='OPENART', enable_uart=False):
    if enable_uart and board != 'H7':
        raise ValueError('Payload UART profile is H7 only')
    if board not in ('OPENART', 'H7'):
        raise ValueError('Unknown board: ' + board)
    parts = ['# GENERATED from firmware/*.py; edit the source files and rebuild.\n'
             '# QR+BAR bench test; no motion. UART enabled only in h7_scan_uart.py.\n']
    modules = MODULES[:-1] + ('h7_uart_output', 'main') if enable_uart else MODULES
    for name in modules:
        path = ROOT / 'firmware' / (name + '.py')
        text = path.read_text(encoding='utf-8')
        lines = text.splitlines(keepends=True)
        remove = set()
        for node in ast.parse(text).body:
            if isinstance(node, ast.ImportFrom) and node.module in MODULES:
                remove.update(range(node.lineno - 1, node.end_lineno))
        parts.append('\n# ===== firmware/%s.py =====\n' % name)
        parts.append(''.join(line for i, line in enumerate(lines) if i not in remove))
        parts.append('\n')
        if name == 'config' and board == 'H7':
            parts.append('\n# ===== firmware/config_h7.py =====\n')
            parts.append((ROOT / 'firmware/config_h7.py').read_text(encoding='utf-8'))
    result = ''.join(parts)
    if enable_uart:
        result = result.replace("if __name__ == '__main__':\n    main()",
                                "if __name__ == '__main__':\n    main(sender=H7PayloadUART())")
    ast.parse(result)
    return result


def build(board='OPENART', enable_uart=False):
    filename = 'h7_scan_test.py' if board == 'H7' else 'openmv_scan_test.py'
    if enable_uart:
        filename = 'h7_scan_uart.py'
    out = ROOT / 'build' / filename
    out.parent.mkdir(exist_ok=True)
    out.write_text(build_source(board, enable_uart), encoding='utf-8', newline='\n')
    return out


def build_demo_source(enable_uart=False):
    modules = ('config', 'qr_scanner', 'barcode_scanner', 'result_filter',
               'uart_output', 'scan_demo')
    parts = []
    for name in modules:
        source = (ROOT / 'firmware' / (name + '.py')).read_text(encoding='utf-8')
        tree = ast.parse(source)
        remove = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.ImportFrom) and node.module in modules or
                    isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                remove.update(range(node.lineno, node.end_lineno + 1))
        for node in tree.body:
            if (name == 'config' and isinstance(node, ast.Assign) and
                    any(isinstance(t, ast.Name) and t.id in
                        ('DEBUG_RAW', 'DEBUG_STATUS', 'TEST_LOG', 'DRAW_BOXES')
                        for t in node.targets)):
                remove.update(range(node.lineno, node.end_lineno + 1))
            if isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'":
                remove.update(range(node.lineno, node.end_lineno + 1))
        lines = source.splitlines()
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                row, col = token.start
                lines[row - 1] = lines[row - 1][:col].rstrip()
        parts.append('\n'.join(line for i, line in enumerate(lines, 1) if i not in remove))
    parts.append("if __name__ == '__main__':\n    main(enable_uart=%s)\n" % enable_uart)
    result = '\n\n'.join(parts)
    ast.parse(result)
    return result


def build_demo(enable_uart=False):
    filename = 'openmv_scan_uart.py' if enable_uart else 'openmv_scan_demo.py'
    out = ROOT / 'build' / filename
    out.parent.mkdir(exist_ok=True)
    out.write_text(build_demo_source(enable_uart), encoding='utf-8', newline='\n')
    return out


def build_usb_results(uart=False, payload_usb=False):
    if uart and payload_usb:
        raise ValueError('Choose one output transport')
    modules = (('barcode_scanner', 'h7_bar_usb') if payload_usb else
               ('barcode_scanner', 'h7_uart_output', 'h7_bar_uart') if uart else
               ('qr_scanner', 'barcode_scanner', 'h7_usb_results'))
    parts = ['# GENERATED: H7 RAW_ONLY -> USB VCP payload CRLF; no UART.\n' if payload_usb else
             '# GENERATED: H7 native barcode -> UART3 payload lines.\n' if uart else
             '# GENERATED: H7 native decode -> USB results; no image streaming.\n']
    for name in modules:
        source = (ROOT / 'firmware' / (name+'.py')).read_text(encoding='utf-8')
        lines = source.splitlines(keepends=True)
        remove = set()
        for node in ast.parse(source).body:
            if isinstance(node, ast.ImportFrom) and node.module in modules:
                remove.update(range(node.lineno-1, node.end_lineno))
        parts.append(''.join(line for i,line in enumerate(lines) if i not in remove))
    result = '\n'.join(parts)
    ast.parse(result)
    out = ROOT/('build/h7_bar_usb.py' if payload_usb else 'build/h7_bar_uart.py' if uart else 'build/h7_usb_results.py')
    out.parent.mkdir(exist_ok=True)
    out.write_text(result,encoding='utf-8',newline='\n')
    return out


if __name__ == '__main__':
    print(build())
    print(build('H7'))
    print(build('H7', enable_uart=True))
    print(build_demo())
    print(build_demo(enable_uart=True))

    print(build_usb_results())
