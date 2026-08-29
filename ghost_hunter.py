#!/usr/bin/env python3
"""ghost_hunter.py — 扫描项目中所有 import，找出引用但文件不存在的本地项目模块。
用法: python ghost_hunter.py
退出码: 0=全部正常, 1=发现幽灵模块
"""
import os, re, sys, glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

# Python 标准库白名单（这些不需要本地文件）
STDLIB = {
    'abc', 'aifc', 'argparse', 'array', 'ast', 'asynchat', 'asyncio', 'asyncore',
    'atexit', 'audioop', 'base64', 'bdb', 'binascii', 'binhex', 'bisect',
    'builtins', 'bz2', 'calendar', 'cgi', 'cgitb', 'chunk', 'cmath', 'cmd',
    'code', 'codecs', 'codeop', 'collections', 'colorsys', 'compileall',
    'concurrent', 'configparser', 'contextlib', 'copy', 'copyreg', 'csv',
    'ctypes', 'dataclasses', 'datetime', 'dbm', 'decimal', 'difflib', 'dis',
    'doctest', 'email', 'enum', 'errno', 'faulthandler', 'fcntl', 'filecmp',
    'fileinput', 'fnmatch', 'fractions', 'ftplib', 'functools', 'gc', 'getopt',
    'getpass', 'gettext', 'glob', 'graphlib', 'grp', 'gzip', 'hashlib', 'heapq',
    'hmac', 'html', 'http', 'idlelib', 'imaplib', 'imghdr', 'imp', 'importlib',
    'inspect', 'io', 'ipaddress', 'itertools', 'json', 'keyword', 'linecache',
    'locale', 'logging', 'lzma', 'mailbox', 'mailcap', 'marshal', 'math',
    'mimetypes', 'mmap', 'multiprocessing', 'netrc', 'nis', 'nntplib', 'numbers',
    'operator', 'optparse', 'os', 'ossaudiodev', 'pathlib', 'pdb', 'pickle',
    'pickletools', 'pipes', 'pkgutil', 'platform', 'plistlib', 'poplib', 'posix',
    'posixpath', 'pprint', 'profile', 'pstats', 'pty', 'pwd', 'py_compile',
    'pyclbr', 'pydoc', 'queue', 'quopri', 'random', 're', 'readline', 'reprlib',
    'resource', 'rlcompleter', 'runpy', 'sched', 'secrets', 'select', 'selectors',
    'shelve', 'shlex', 'shutil', 'signal', 'site', 'smtpd', 'smtplib', 'sndhdr',
    'socket', 'socketserver', 'spwd', 'sqlite3', 'ssl', 'stat', 'statistics',
    'string', 'stringprep', 'struct', 'subprocess', 'sunau', 'sys', 'sysconfig',
    'syslog', 'tabnanny', 'tarfile', 'telnetlib', 'tempfile', 'termios', 'test',
    'textwrap', 'threading', 'time', 'timeit', 'tkinter', 'token', 'tokenize',
    'trace', 'traceback', 'tracemalloc', 'tty', 'turtle', 'turtledemo', 'types',
    'typing', 'unicodedata', 'unittest', 'urllib', 'uu', 'uuid', 'venv',
    'warnings', 'wave', 'weakref', 'webbrowser', 'winreg', 'winsound', 'wsgiref',
    'xdrlib', 'xml', 'xmlrpc', 'zipapp', 'zipfile', 'zipimport', 'zlib',
}

# 常见第三方库白名单
THIRD_PARTY = {
    'numpy', 'scipy', 'pandas', 'matplotlib', 'sklearn', 'scikit_learn',
    'requests', 'tqdm', 'joblib', 'PIL', 'pillow', 'cv2', 'opencv',
    'torch', 'tensorflow', 'jax', 'flax', 'optax', 'haiku',
}

SKIP = STDLIB | THIRD_PARTY

# 收集所有本地 .py 模块名
local_modules = set()
for pattern in ['*.py']:
    for f in glob.glob(pattern):
        local_modules.add(os.path.splitext(os.path.basename(f))[0])

# 扫描所有 .py 的 import 语句
ghosts = []
for f in sorted(glob.glob('*.py')):
    try:
        content = open(f, encoding='utf-8', errors='ignore').read()
        imports = re.findall(r'(?:^|\n)\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)', content)
        for mod in imports:
            if mod in SKIP or mod in local_modules:
                continue
            # 检查文件是否存在（含子目录/包）
            if os.path.isfile(mod + '.py') or os.path.isfile(os.path.join('configs', mod + '.py')) or os.path.isdir(mod):
                local_modules.add(mod)
                continue
            ghosts.append((f, mod))
    except Exception as e:
        print(f"WARN: cannot read {f}: {e}", file=sys.stderr)

# 输出结果
if ghosts:
    print(f"FAIL: {len(set(ghosts))} ghost module reference(s) found:")
    for file, mod in sorted(set(ghosts)):
        print(f"  {file}  ->  import {mod}  (file '{mod}.py' does not exist)")
    sys.exit(1)
else:
    print(f"PASS: all {len(local_modules)} local modules resolved, zero ghost imports.")
    sys.exit(0)
