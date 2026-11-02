# -*- coding: utf-8 -*-
"""跨进程文件锁与原子写入。

仅使用标准库，兼容 Windows。关键状态文件先写同目录临时文件、flush+fsync，
再 os.replace；共享文件在整个读改写周期持有锁，避免 15 台/多进程并发覆盖。
"""
import contextlib
import json
import os
import tempfile
import time


class LockTimeout(RuntimeError):
    pass


class FileLock:
    def __init__(self, target, timeout=30.0, poll=0.1):
        self.path = os.path.abspath(target + '.lock')
        self.timeout = float(timeout)
        self.poll = float(poll)
        self._fp = None

    def acquire(self):
        """拿锁；拿不到就轮询重试，直到超时。

        ⚠️ 2026-11-02 修正：原先 ``open()`` 写在重试循环外，Windows 上另一线程
        持有字节锁时 open 会抛 PermissionError（Errno 13），异常直接逃出本函数，
        调用方看到的是"权限错误"而不是排队等待 —— 并发写同一状态文件时丢更新
        （实测 4 线程各 25 次自增，结果只有 76 而不是 101）。
        现在 open + 加锁整体纳入重试，失败即关句柄再等下一轮。
        """
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            fp = None
            try:
                fp = open(self.path, 'a+b')
                if os.name == 'nt':
                    import msvcrt
                    fp.seek(0)
                    if os.path.getsize(self.path) == 0:
                        fp.write(b'0')
                        fp.flush()
                    fp.seek(0)
                    msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fp = fp
                return self
            except (OSError, IOError):
                if fp is not None:
                    try:
                        fp.close()
                    except OSError:
                        pass
                if time.monotonic() >= deadline:
                    self._fp = None
                    raise LockTimeout('等待文件锁超时：%s' % self.path)
                time.sleep(self.poll)

    def release(self):
        if self._fp is None:
            return
        try:
            if os.name == 'nt':
                import msvcrt
                self._fp.seek(0)
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
        finally:
            self._fp.close()
            self._fp = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()


def _fsync_dir(path):
    if os.name == 'nt':
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def atomic_write_bytes(path, data, mode=None):
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(path) + '.', suffix='.tmp', dir=parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
        _fsync_dir(parent)
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    return path


def atomic_write_text(path, text, encoding='utf-8'):
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path, data, indent=2, lock=False):
    payload = json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=False) + '\n'
    if lock:
        with FileLock(path):
            return atomic_write_text(path, payload)
    return atomic_write_text(path, payload)


def locked_json_update(path, update_fn, default=None, timeout=30.0):
    """在同一把锁内读取、修改并原子替换 JSON。"""
    with FileLock(path, timeout=timeout):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {} if default is None else default
        result = update_fn(data)
        if result is not None:
            data = result
        atomic_write_json(path, data)
        return data
