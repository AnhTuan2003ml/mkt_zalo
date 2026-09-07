"""Kiểm tra sau build: exe phải nhúng .env và đúng OpenSSL DLL của Python build.

Chạy tự động ở cuối build.bat:  python build_check.py dist\\Nexus.exe
Exit code != 0 nếu thiếu .env trong bundle hoặc DLL OpenSSL bị nhặt sai nguồn
(vd. Git mingw64) — lỗi này làm `import _ssl` fail trong exe, smtplib mất SSL
và không gửi được mã kích hoạt.
"""
import glob
import os
import sys

from PyInstaller.archive.readers import CArchiveReader


def main() -> int:
    exe_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("dist", "Nexus.exe")
    if not os.path.isfile(exe_path):
        print(f"[CHECK] Khong tim thay {exe_path}")
        return 1

    toc = CArchiveReader(exe_path).toc  # {name: (dpos, dlen, ulen, flag, typcd)}

    ok = True

    # 1) .env phai duoc nhung va cung kich thuoc voi .env goc
    if ".env" not in toc:
        print("[CHECK] FAIL: exe KHONG chua .env — kiem tra datas trong ZaloMemberTool.spec")
        ok = False
    elif os.path.isfile(".env"):
        bundled_size = toc[".env"][2]
        local_size = os.path.getsize(".env")
        if bundled_size != local_size:
            print(f"[CHECK] FAIL: .env trong exe ({bundled_size} bytes) khac .env goc ({local_size} bytes) — build lai")
            ok = False
        else:
            print(f"[CHECK] OK: .env da nhung ({bundled_size} bytes)")

    # 2) OpenSSL DLL trong exe phai dung ban cua Python build (so kich thuoc)
    ssl_dirs = [
        os.path.join(sys.base_prefix, "Library", "bin"),
        os.path.join(sys.base_prefix, "DLLs"),
        os.path.dirname(sys.executable),
    ]
    expected = {}
    for folder in ssl_dirs:
        if not os.path.isdir(folder):
            continue
        for pattern in ("libcrypto-3*.dll", "libssl-3*.dll"):
            for path in glob.glob(os.path.join(folder, pattern)):
                name = os.path.basename(path).lower()
                expected.setdefault(name, os.path.getsize(path))

    if expected:
        toc_lower = {name.lower(): entry for name, entry in toc.items()}
        for name, size in sorted(expected.items()):
            if name not in toc_lower:
                print(f"[CHECK] FAIL: exe thieu {name}")
                ok = False
            elif toc_lower[name][2] != size:
                print(
                    f"[CHECK] FAIL: {name} trong exe ({toc_lower[name][2]} bytes) KHAC ban cua Python build "
                    f"({size} bytes) — PyInstaller nhat DLL sai tu PATH (vd. Git mingw64)"
                )
                ok = False
            else:
                print(f"[CHECK] OK: {name} dung ban cua Python build ({size} bytes)")
    else:
        print("[CHECK] WARN: khong tim thay OpenSSL DLL trong Python build de so sanh")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
