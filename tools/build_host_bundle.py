"""Build only the two current programs and their installation instructions."""
import ast
from datetime import datetime
from pathlib import Path
import shutil
import zipfile
from build_standalone import build_usb_results

ROOT = Path(__file__).resolve().parents[1]
FILES = {'qr_tcp.py', 'main.py', 'requirements.txt', 'README.md'}


def host_program():
    def read(name):
        return ast.parse((ROOT/name).read_text(encoding='utf-8-sig')).body

    nodes = ast.parse('import zxingcpp').body
    nodes += [n for n in read('firmware/qr_scanner.py')
              if isinstance(n, ast.FunctionDef) and n.name == 'parse_qr'
              or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'QR_PAYLOADS' for t in n.targets)]
    nodes += [n for n in read('host/scan.py') if isinstance(n, ast.FunctionDef) and n.name == 'decode']
    nodes += read('host/qr_tcp.py')

    class LocalImports(ast.NodeTransformer):
        def visit_ImportFrom(self, node):
            return None if node.module in ('scan', 'qr_camera', 'firmware.qr_scanner') else node

        def visit_Import(self, node):
            node.names = [n for n in node.names if n.name != 'scan']
            return node if node.names else None

        def visit_Expr(self, node):
            if isinstance(node.value, ast.Call) and ast.unparse(node.value.func) == 'sys.path.insert':
                return None
            return self.generic_visit(node)

    module = LocalImports().visit(ast.Module(body=nodes, type_ignores=[]))
    text = '# Generated from current QR source; edit host/ sources, then rebuild.\n' + ast.unparse(module) + '\n'
    compile(text, 'qr_tcp.py', 'exec')
    return text


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update', type=Path, required=True, help='Existing bundle inside build/')
    args = parser.parse_args()
    out = args.update.resolve()
    build = (ROOT/'build').resolve()
    if out == build or not out.is_relative_to(build) or not out.is_dir():
        raise ValueError('Target must be an existing directory inside project build/')
    content = {'qr_tcp.py': host_program(),
               'main.py': build_usb_results(payload_usb=True).read_text(encoding='utf-8-sig'),
               'requirements.txt': 'zxing-cpp==3.1.1\nopencv-python-headless==4.13.0.92\nnumpy==2.2.6\n',
               'README.md': (ROOT/'host/README.md').read_text(encoding='utf-8-sig')}
    # Preserve old generated files and any local additions outside the active bundle.
    if {p.name for p in out.iterdir()} != FILES:
        if not (out/'manifest.json').is_file():
            raise ValueError('Unrecognized bundle layout; refusing to replace it')
        backup = (build/'backups'/('bundle_before_slim_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))).resolve()
        if not backup.is_relative_to(build) or backup.exists():
            raise ValueError('Unsafe backup path')
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out), str(backup))
        out.mkdir()
        print('Preserved previous bundle:', backup)
    for name, text in content.items():
        (out/name).write_text(text, encoding='utf-8', newline='\n')
    with zipfile.ZipFile(str(out)+'.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(FILES):
            archive.write(out/name, name)
    print(out)
    print(str(out)+'.zip')


if __name__ == '__main__':
    main()
