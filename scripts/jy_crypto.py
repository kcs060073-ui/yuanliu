# -*- coding: utf-8 -*-
"""剪映草稿 JSON 加解密（Windows / macOS 统一入口）。

设计要点
--------
* **Windows 分支 = 历史实现，逐字不变**（videoeditor.dll + MSVC 字符串 ABI）。
* **macOS 分支 = 默认明文直通 + 一次性醒目警告**：
    - 剪映 6.0+ 起草稿默认加密，但 Mac 版加密库是 Mach-O，且 libc++ 的
      ``std::string`` 内存布局与 MSVC 不同（libc++ 为单一指针布局，MSVC 为
      ptr/size/capacity 联合体），直接复用 Windows 的 ``MsvcString`` 封送会
      崩溃或返回乱码。在没有真机逐字节验证前，**不**默认碰 Mach-O 库。
    - 因此 Mac 上默认「原样写、原样读」：若本机 剪映 恰好是明文版本（或旧版），
      草稿可直接被打开；若本机 剪映 是加密版本，草稿可能打不开——
      此时会以醒目警告提示用户，并给出验证命令与下一步。
    - ``decrypt`` 对「已是明文 JSON」的输入一律直通（无论平台），方便明文草稿直接读取。
* **彻底解法（TODO）**：在装了 剪映 的 Mac 上逆向 Mach-O 加解密库的真正符号名与
  字符串 ABI，按 libc++ 布局重写封送，再做回归。优先级低于「先让管线在 Mac 跑通」。
"""
import ctypes
import os
import sys

import platform_paths as _pp

DLL_DEC = "?decrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@AEBV34@0AEA_N@Z"
DLL_ENC = "?encrypt@EncryptUtils@lvve@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@AEBV34@@Z"
DLL_ENA = "?enable@EncryptUtils@lvve@@QEAAX_N@Z"

LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
LOAD_LIBRARY_SEARCH_APPLICATION_DIR = 0x00000200
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
LOAD_LIBRARY_SEARCH_USER_DIRS = 0x00000400

KEEPALIVE = []


class _StrUnion(ctypes.Union):
    _fields_ = [
        ("small", ctypes.c_char * 16),
        ("ptr", ctypes.c_void_p),
    ]


class MsvcString(ctypes.Structure):
    _fields_ = [
        ("u", _StrUnion),
        ("size", ctypes.c_ulonglong),
        ("capacity", ctypes.c_ulonglong),
    ]

    @classmethod
    def make(cls, data: bytes):
        self = cls()
        self.size = len(data)
        if len(data) < 16:
            self.u.small = data.ljust(16, b"\0")
            self.capacity = 15
        else:
            buf = ctypes.create_string_buffer(data, len(data) + 1)
            KEEPALIVE.append(buf)
            self.u.ptr = ctypes.addressof(buf)
            self.capacity = len(data)
        return self

    def to_bytes(self) -> bytes:
        if self.capacity < 16:
            # 短字符串内联在 union 的 small 缓冲（偏移 0），取 union 首地址即可
            return ctypes.string_at(ctypes.addressof(self.u), self.size)
        if self.u.ptr == 0:
            return b""
        return ctypes.string_at(self.u.ptr, self.size)


def _looks_plaintext(data: bytes) -> bool:
    """粗略判断输入是否已是明文 JSON（首字符为 { 或 [）。"""
    head = data.lstrip()[:1]
    return head in (b"{", b"[")


class _PlaintextCrypto:
    """明文直通：不加密不解密，原样返回。

    作为 macOS 默认与未知平台的兜底。首次 encrypt 时打印一次醒目警告，
    告知用户「当前未做真加密，加密版 剪映 可能打不开该草稿」。
    """

    _warned = False

    def __init__(self, install_dir: str = "", reason: str = ""):
        self._install_dir = install_dir
        self._reason = reason

    def decrypt(self, data: bytes) -> bytes:
        # 明文（或本就解不开的密文）原样返回；调用方按需解析。
        return data

    def encrypt(self, data: bytes) -> bytes:
        if not _PlaintextCrypto._warned:
            _PlaintextCrypto._warned = True
            sys.stderr.write(
                "\n[源流|Mac] ⚠️ 当前以「明文直通」写入剪映草稿（未调用 Mach-O 加密库）。\n"
                "    若你本机剪映版本的草稿是【加密】的，生成的草稿可能无法在剪映中打开。\n"
                "    验证方法（在 Mac 上）：\n"
                "      head -c 40 ~/Movies/JianyingPro/User\\ Data/Projects/com.lveditor.draft/*/draft_content.json\n"
                "    若输出是 { 开头的明文 → 没问题；若乱码 → 需要先在真机实现 Mac 加密层。\n"
                + (("    原因：" + self._reason + "\n") if self._reason else "")
                + "    详见 SKILL.md「跨平台支持状态」小节。\n\n"
            )
        return data


class _WindowsCrypto:
    """Windows 实现：调用 videoeditor.dll 的 EncryptUtils（MSVC ABI）。

    与历史版本逐字一致，仅在 __init__ 里把平台判定收敛出去，逻辑不动。
    """

    def __init__(self, install_dir: str):
        dll_path = os.path.join(install_dir, "videoeditor.dll")
        if not os.path.isfile(dll_path):
            raise FileNotFoundError(dll_path)
        self._install_dir = install_dir
        os.environ["PATH"] = install_dir + os.pathsep + os.environ.get("PATH", "")
        os.chdir(install_dir)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.SetDllDirectoryW(install_dir)
        k32.AddDllDirectory(install_dir)
        flags = (
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
            | LOAD_LIBRARY_SEARCH_APPLICATION_DIR
            | LOAD_LIBRARY_SEARCH_SYSTEM32
            | LOAD_LIBRARY_SEARCH_USER_DIRS
        )
        self._dll = ctypes.CDLL(dll_path, winmode=flags)
        self._dec = ctypes.WINFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(MsvcString),
            ctypes.POINTER(MsvcString), ctypes.POINTER(MsvcString),
            ctypes.POINTER(ctypes.c_bool),
        )((DLL_DEC, self._dll))
        self._enc = ctypes.WINFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(MsvcString),
            ctypes.POINTER(MsvcString),
        )((DLL_ENC, self._dll))
        self._ena = ctypes.WINFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool,
        )((DLL_ENA, self._dll))

    def decrypt(self, data: bytes) -> bytes:
        if _looks_plaintext(data):
            return data
        enc = MsvcString.make(data)
        params = MsvcString.make(b"{}")
        out = MsvcString()
        out.capacity = 15
        ok = ctypes.c_bool(False)
        self._dec(None, ctypes.byref(out), ctypes.byref(enc), ctypes.byref(params), ctypes.byref(ok))
        if not ok.value:
            raise RuntimeError("decrypt returned ok=False")
        result = out.to_bytes()
        # 2026-11-02：剪映 11.7.0.14652 起，decrypt 对任何输入（含它自己刚加密的内容）
        # 都返回 ok=True + 空串。此时静默返回 b'' 会让调用方写出 0 字节文件、
        # 把问题伪装成"草稿损坏"。这里显式报错，让上层给出可读结论。
        if not result and data:
            raise RuntimeError(
                "decrypt returned empty for %d-byte input（剪映 11.7+ 解密 API 不兼容，"
                "非草稿损坏）" % len(data))
        return result

    def encrypt(self, data: bytes) -> bytes:
        # DLL 偶发状态问题：enable 后 encrypt 仍可能原样返回明文（或空串）。
        # 这里校验结果：密文不应以 '{' 开头、不应等于输入、不应为空；失败则重试。
        import time
        for attempt in range(5):
            self._ena(None, ctypes.c_bool(True))
            plain = MsvcString.make(data)
            out = MsvcString()
            out.capacity = 15
            self._enc(None, ctypes.byref(out), ctypes.byref(plain))
            result = out.to_bytes()
            if result and result != data and not result.startswith(b"{"):
                return result
            time.sleep(0.05 * (attempt + 1))
        raise RuntimeError("encrypt failed: output looks plaintext/empty after retries")


class EncryptUtils:
    """跨平台门面：按平台选择具体实现。

    公开 API 不变：``EncryptUtils(install_dir).encrypt/decrypt(...)``。
    调用方（make_park_video / bgm_pool / arrange 子进程）无需感知平台。
    """

    def __init__(self, install_dir: str = ""):
        if _pp.is_windows():
            self._impl = _WindowsCrypto(install_dir)
        elif _pp.is_macos():
            # TODO（macOS 真机）：实现 Mach-O 加密层后，这里改为优先用真加密，
            # 失败再回退 _PlaintextCrypto。
            self._impl = _PlaintextCrypto(
                install_dir,
                reason="Mac 加密层尚未在真机实现（Mach-O + libc++ 字符串布局差异）",
            )
        else:
            self._impl = _PlaintextCrypto(install_dir, reason="非 Windows / macOS 平台，未实现加解密")

    def decrypt(self, data: bytes) -> bytes:
        return self._impl.decrypt(data)

    def encrypt(self, data: bytes) -> bytes:
        return self._impl.encrypt(data)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(64)
    mode, infile, outfile = sys.argv[1], os.path.abspath(sys.argv[2]), os.path.abspath(sys.argv[3])
    # P05 §7：默认安装目录动态探测（不再写死 D:\JianyingPro\<版本>）。
    # 这是给人工排查用的入口，写死盘符+版本号会让它在别的机器上直接报解密失败。
    install_dir = sys.argv[4] if len(sys.argv) > 4 else ''
    if not install_dir:
        try:
            import jianying_path
            install_dir = jianying_path.find_install_dir()
        except Exception:
            install_dir = ''
        if not install_dir:
            print('未找到剪映安装目录（找不到 %s）。'
                  '请把安装目录作为第 4 个参数传入。' % _pp.native_lib_name())
            sys.exit(1)
    eu = EncryptUtils(install_dir)
    with open(infile, "rb") as f:
        raw = f.read()
    try:
        if mode == "-d":
            result = eu.decrypt(raw)
        elif mode == "-e":
            result = eu.encrypt(raw)
        else:
            print("unknown mode", mode)
            sys.exit(64)
    except Exception as e:
        # 不写空文件：失败就明确失败，别让上层把 0 字节当成"解密成功"
        sys.stderr.write("加解密失败（%s）：%s\n" % (install_dir, e))
        sys.exit(2)
    with open(outfile, "wb") as f:
        f.write(result)
    print("written", outfile, len(result), "bytes")


if __name__ == "__main__":
    main()
