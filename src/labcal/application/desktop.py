"""Double-click launcher: reuse this workspace's healthy server or start it detached."""
import hashlib
import json
import socket
from pathlib import Path
import shlex
import subprocess
import sys
import time
from urllib.error import URLError, HTTPError
from urllib.request import ProxyHandler, build_opener
import webbrowser

from ..errors import LabcalError
from .. import __version__


def health_identity(workspace):
    return {"application": "labcal", "version": __version__, "workspace_id": hashlib.sha256(str(workspace.root.resolve()).encode()).hexdigest()}


def probe(workspace, port):
    # Explicitly bypass system proxies for the local application.
    try:
        with build_opener(ProxyHandler({})).open(f"http://127.0.0.1:{port}/api/health", timeout=.7) as response:
            record = json.load(response)
        if isinstance(record, dict) and record.get('application') == 'labcal' and record.get('workspace_id') == health_identity(workspace)['workspace_id']:
            return True
    except (URLError, HTTPError, OSError, ValueError):
        pass
    return False


def open_workbench(workspace, port=8765, *, open_browser=True, timeout=15):
    if not 1024 <= port <= 65535: raise LabcalError('PORT_INVALID', '工作台端口需要在1024至65535之间。')
    url = f'http://127.0.0.1:{port}/'
    reused = probe(workspace, port)
    runtime = workspace.data / 'runtime'; runtime.mkdir(parents=True, exist_ok=True)
    log = runtime / 'workbench.log'
    process = None
    if not reused:
        with socket.socket() as check:
            if check.connect_ex(('127.0.0.1', port)) == 0:
                raise LabcalError('WORKBENCH_PORT_IN_USE', f'端口{port}被其他服务占用，未停止或替换该服务。可使用 ./labcal open --port 其他端口。')
        with log.open('ab') as output:
            process = subprocess.Popen([sys.executable, str(workspace.root / 'labcal'), '--workspace', str(workspace.root), 'serve', '--port', str(port)],
                cwd=workspace.root, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if probe(workspace, port): break
            if process.poll() is not None:
                raise LabcalError('WORKBENCH_START_FAILED', f'工作台启动失败，请查看日志：{log}')
            time.sleep(.15)
        else: raise LabcalError('WORKBENCH_START_TIMEOUT', f'工作台启动超时，请查看日志：{log}')
        (runtime / 'workbench-process.json').write_text(json.dumps({'pid':process.pid, 'port':port, **health_identity(workspace)}, ensure_ascii=False, indent=2)+'\n')
    browser_opened = webbrowser.open(url) if open_browser else False
    return {'url':url, 'reused':reused, 'browser_opened':browser_opened, 'log':str(log), 'status':'ready'}


def install_desktop_entry(workspace, directory=None):
    """An explicit installation command; never called by normal application startup."""
    directory = Path(directory) if directory else Path.home() / 'Desktop'
    directory.mkdir(parents=True, exist_ok=True)
    entry = directory / '校准证书工作台.command'
    marker = '# LabCal desktop launcher v1'
    if entry.exists() and marker not in entry.read_text(encoding='utf-8'):
        raise LabcalError('DESKTOP_ENTRY_EXISTS', '桌面存在同名文件，已保留；请更换安装目录。')
    text = '\n'.join(['#!/bin/zsh', marker, 'labcal_project='+shlex.quote(str(workspace.root)),
        'labcal_python='+shlex.quote(sys.executable),
        'if [[ ! -f "$labcal_project/labcal" ]]; then',
        '  print "工作台工程目录已移动，请从新位置重新安装桌面入口。"',
        '  read "labcal_answer?按回车关闭"', '  exit 1', 'fi',
        '"$labcal_python" "$labcal_project/labcal" --workspace "$labcal_project" open',
        'labcal_exit_code=$?', 'if (( labcal_exit_code != 0 )); then',
        '  print "启动未完成，错误详情如上。日志位于工程的 data/runtime/workbench.log。"',
        '  read "labcal_answer?按回车关闭"', 'fi', 'exit "$labcal_exit_code"', ''])
    entry.write_text(text, encoding='utf-8'); entry.chmod(0o755)
    return {'entry':str(entry), 'status':'installed', 'launch_command':'./labcal open'}
