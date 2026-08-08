from __future__ import annotations

import ctypes
import json
import os
import queue
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

try:
    import certifi
except ImportError:  # Fallback vẫn dùng kho chứng chỉ mặc định của hệ thống.
    certifi = None
from tkinter import BOTH, DISABLED, END, LEFT, NORMAL, RIGHT, X, Y, Tk, StringVar, BooleanVar, filedialog, messagebox
from tkinter import ttk

APP_NAME = "Nexus"
APP_VERSION = "1.0.0"
PUBLISHER = "AnhTuan2003ml"
REPOSITORY = "AnhTuan2003ml/mkt_zalo"
PREFERRED_ASSET_NAME = "Nexus.zip"
LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
EXPECTED_EXE_NAME = "Nexus.exe"
USER_AGENT = f"{APP_NAME}-Installer/{APP_VERSION}"
CHUNK_SIZE = 1024 * 256


def resource_path(relative_path: str) -> Path:
    """Return a bundled PyInstaller resource path or a source-relative path."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / relative_path


def default_install_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Programs" / APP_NAME
    return Path.home() / APP_NAME


def desktop_directory() -> Path:
    """Resolve the current user's real Desktop folder on Windows."""
    if os.name == "nt":
        buffer = ctypes.create_unicode_buffer(260)
        # CSIDL_DESKTOPDIRECTORY = 0x0010, SHGFP_TYPE_CURRENT = 0
        result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer)
        if result == 0 and buffer.value:
            return Path(buffer.value)
    return Path.home() / "Desktop"


def ensure_writable_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".nexus_write_test_{os.getpid()}"
    try:
        probe.write_bytes(b"")
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def create_verified_ssl_context() -> ssl.SSLContext:
    """Create a verified TLS context, preferring certifi's bundled CA store."""
    if certifi is not None:
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except (OSError, ssl.SSLError):
            pass
    return ssl.create_default_context()


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def native_https_download(url: str, destination: Path, headers: dict[str, str]) -> None:
    """Download with Windows native TLS as a fallback; certificate checks stay enabled."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    errors: list[str] = []
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        command = [
            curl,
            "--location",
            "--fail",
            "--silent",
            "--show-error",
            "--retry",
            "3",
            "--retry-delay",
            "1",
            "--connect-timeout",
            "30",
            "--max-time",
            "600",
            "--output",
            str(destination),
        ]
        for key, value in headers.items():
            command.extend(["--header", f"{key}: {value}"])
        command.append(url)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                creationflags=creation_flags,
                timeout=660,
            )
            if completed.returncode == 0 and destination.is_file() and destination.stat().st_size > 0:
                return
            errors.append((completed.stderr or completed.stdout or "curl tải thất bại").strip())
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
        destination.unlink(missing_ok=True)

    header_lines = ["$headers = @{}"]
    for key, value in headers.items():
        header_lines.append(
            f"$headers[{powershell_quote(key)}] = {powershell_quote(value)}"
        )
    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12",
            *header_lines,
            (
                "Invoke-WebRequest -UseBasicParsing -MaximumRedirection 10 "
                f"-Uri {powershell_quote(url)} -Headers $headers "
                f"-OutFile {powershell_quote(str(destination))}"
            ),
        ]
    )
    for shell_name in ("powershell.exe", "pwsh.exe"):
        shell = shutil.which(shell_name)
        if not shell:
            continue
        try:
            completed = subprocess.run(
                [
                    shell,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                check=False,
                capture_output=True,
                text=True,
                creationflags=creation_flags,
                timeout=660,
            )
            if completed.returncode == 0 and destination.is_file() and destination.stat().st_size > 0:
                return
            errors.append((completed.stderr or completed.stdout or f"{shell_name} tải thất bại").strip())
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
        destination.unlink(missing_ok=True)

    detail = next((item for item in reversed(errors) if item), "Không có công cụ tải HTTPS dự phòng.")
    raise RuntimeError(
        "Không thể kết nối HTTPS an toàn tới GitHub. "
        "Hãy kiểm tra ngày giờ Windows, cập nhật chứng chỉ gốc hoặc thử mạng khác. "
        f"Chi tiết: {detail}"
    )


def request_json(url: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
            context=create_verified_ssl_context(),
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, ssl.SSLError, OSError):
        with tempfile.TemporaryDirectory(prefix="nexus_github_api_") as temporary:
            response_file = Path(temporary) / "latest_release.json"
            native_https_download(url, response_file, headers)
            raw = response_file.read_bytes()

    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("GitHub trả về dữ liệu release không hợp lệ.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub trả về định dạng release không hợp lệ.")
    return payload

def normalize_version(tag_name: str) -> str:
    version = str(tag_name or "").strip().lstrip("vV")
    return version or APP_VERSION


def resolve_latest_release() -> dict[str, str]:
    """Resolve the newest published GitHub release and its installable ZIP asset."""
    release = request_json(LATEST_RELEASE_API_URL)
    tag_name = str(release.get("tag_name") or "").strip()
    if not tag_name:
        raise RuntimeError("Release mới nhất không có tag phiên bản.")

    assets = release.get("assets") or []
    selected = next(
        (
            asset
            for asset in assets
            if str(asset.get("name") or "").lower() == PREFERRED_ASSET_NAME.lower()
            and asset.get("browser_download_url")
        ),
        None,
    )
    if selected is None:
        selected = next(
            (
                asset
                for asset in assets
                if str(asset.get("name") or "").lower().endswith(".zip")
                and asset.get("browser_download_url")
            ),
            None,
        )
    if selected is None:
        raise RuntimeError(
            f"Release {tag_name} không có asset {PREFERRED_ASSET_NAME} hoặc tệp ZIP cài đặt."
        )

    return {
        "tag": tag_name,
        "version": normalize_version(tag_name),
        "asset_name": str(selected.get("name") or PREFERRED_ASSET_NAME),
        "download_url": str(selected["browser_download_url"]),
    }


def write_installed_version(install_dir: Path, version: str) -> None:
    """Keep the installed VERSION file synchronized with the downloaded release."""
    version_file = install_dir / "VERSION"
    temporary = install_dir / ".VERSION.tmp"
    temporary.write_text(normalize_version(version) + "\n", encoding="utf-8")
    os.replace(temporary, version_file)


def download_archive(url: str, destination: Path, progress_callback) -> None:
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": USER_AGENT,
    }
    request = urllib.request.Request(url, headers=headers)
    destination.unlink(missing_ok=True)
    try:
        with urllib.request.urlopen(
            request,
            timeout=60,
            context=create_verified_ssl_context(),
        ) as response:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with destination.open("wb") as output:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    progress_callback(downloaded, total)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, ssl.SSLError, OSError):
        destination.unlink(missing_ok=True)
        progress_callback(0, 0)
        native_https_download(url, destination, headers)
        size = destination.stat().st_size if destination.exists() else 0
        progress_callback(size, size)

    if not destination.exists() or destination.stat().st_size == 0:
        raise RuntimeError("Tệp tải xuống trống hoặc không hợp lệ.")

def safe_relative_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise RuntimeError(f"Tệp ZIP chứa đường dẫn tuyệt đối không an toàn: {name}")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts or any(part == ".." for part in parts):
        raise RuntimeError(f"Tệp ZIP chứa đường dẫn không an toàn: {name}")
    if ":" in parts[0]:
        raise RuntimeError(f"Tệp ZIP chứa ổ đĩa không an toàn: {name}")
    return parts


def archive_root_prefix(infos: list[zipfile.ZipInfo]) -> str | None:
    """Strip a single wrapper directory when every archive entry is inside it."""
    file_parts = [safe_relative_parts(info.filename) for info in infos if not info.is_dir()]
    if not file_parts or any(len(parts) < 2 for parts in file_parts):
        return None
    first = file_parts[0][0]
    return first if all(parts[0] == first for parts in file_parts) else None


def extract_archive(archive: Path, install_dir: Path, progress_callback) -> None:
    try:
        with zipfile.ZipFile(archive, "r") as source:
            infos = source.infolist()
            if not infos:
                raise RuntimeError("Tệp ZIP không chứa dữ liệu cài đặt.")

            root_prefix = archive_root_prefix(infos)
            files = [info for info in infos if not info.is_dir()]
            total_files = max(len(files), 1)
            completed = 0

            for info in infos:
                parts = list(safe_relative_parts(info.filename))
                if root_prefix and parts and parts[0] == root_prefix:
                    parts = parts[1:]
                if not parts:
                    continue

                target = install_dir.joinpath(*parts)
                resolved_target = target.resolve()
                resolved_root = install_dir.resolve()
                try:
                    resolved_target.relative_to(resolved_root)
                except ValueError as exc:
                    raise RuntimeError(f"Đường dẫn giải nén không an toàn: {info.filename}") from exc

                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(info, "r") as source_file, target.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file, length=CHUNK_SIZE)

                completed += 1
                progress_callback(completed, total_files)
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Tệp tải về không phải ZIP hợp lệ.") from exc


def find_application_executable(install_dir: Path) -> Path:
    expected = install_dir / EXPECTED_EXE_NAME
    if expected.is_file():
        return expected

    exact_matches = [
        path for path in install_dir.rglob("*.exe")
        if path.name.lower() == EXPECTED_EXE_NAME.lower()
    ]
    if exact_matches:
        return min(exact_matches, key=lambda path: len(path.parts))

    nexus_matches = [
        path for path in install_dir.rglob("*.exe")
        if APP_NAME.lower() in path.stem.lower()
        and "setup" not in path.stem.lower()
        and "install" not in path.stem.lower()
    ]
    if nexus_matches:
        return min(nexus_matches, key=lambda path: len(path.parts))

    executables = [
        path for path in install_dir.rglob("*.exe")
        if "setup" not in path.stem.lower() and "install" not in path.stem.lower()
    ]
    if executables:
        return min(executables, key=lambda path: len(path.parts))

    raise RuntimeError(
        f"Đã giải nén nhưng không tìm thấy {EXPECTED_EXE_NAME} để tạo shortcut."
    )


def create_desktop_shortcut(executable: Path) -> Path:
    desktop = desktop_directory()
    desktop.mkdir(parents=True, exist_ok=True)
    shortcut = desktop / f"{APP_NAME}.lnk"

    script = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$shell = New-Object -ComObject WScript.Shell",
            f"$shortcut = $shell.CreateShortcut({powershell_quote(str(shortcut))})",
            f"$shortcut.TargetPath = {powershell_quote(str(executable))}",
            f"$shortcut.WorkingDirectory = {powershell_quote(str(executable.parent))}",
            f"$shortcut.IconLocation = {powershell_quote(str(executable) + ',0')}",
            f"$shortcut.Description = {powershell_quote(f'Mở {APP_NAME}')}",
            "$shortcut.Save()",
        ]
    )

    last_error = ""
    for shell_name in ("powershell.exe", "pwsh.exe"):
        try:
            completed = subprocess.run(
                [
                    shell_name,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                check=False,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=30,
            )
            if completed.returncode == 0 and shortcut.exists():
                return shortcut
            last_error = (completed.stderr or completed.stdout or "").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            last_error = str(exc)

    raise RuntimeError(f"Không thể tạo shortcut ngoài Desktop. {last_error}".strip())


class InstallerApp:
    BG = "#F5F7FB"
    CARD = "#FFFFFF"
    TEXT = "#172033"
    MUTED = "#667085"
    BORDER = "#D9E0EA"
    PRIMARY = "#2563EB"
    PRIMARY_HOVER = "#1D4ED8"
    SUCCESS = "#15803D"
    ERROR = "#B42318"

    def __init__(self) -> None:
        self.root = Tk()
        self.root.title(f"Cài đặt {APP_NAME} {APP_VERSION}")
        self.root.geometry("660x420")
        self.root.minsize(620, 390)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.install_dir = StringVar(value=str(default_install_dir()))
        self.status = StringVar(value="Sẵn sàng cài đặt")
        self.progress_text = StringVar(value="")
        self.busy = False
        self.installed_executable: Path | None = None
        self.events: queue.Queue[tuple] = queue.Queue()

        self.configure_styles()
        self.set_window_icon()
        self.build_ui()
        self.root.after(100, self.process_events)

    def configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Root.TFrame", background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure(
            "Title.TLabel",
            background=self.CARD,
            foreground=self.TEXT,
            font=("Segoe UI", 20, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background=self.CARD,
            foreground=self.MUTED,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Field.TLabel",
            background=self.CARD,
            foreground=self.TEXT,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background=self.CARD,
            foreground=self.MUTED,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Primary.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(22, 11),
            background=self.PRIMARY,
            foreground="#FFFFFF",
            borderwidth=0,
            focusthickness=0,
        )
        style.map(
            "Primary.TButton",
            background=[("active", self.PRIMARY_HOVER), ("disabled", "#A7B7D8")],
            foreground=[("disabled", "#EEF2F8")],
        )
        style.configure(
            "Secondary.TButton",
            font=("Segoe UI", 10),
            padding=(16, 10),
            background="#EEF2F7",
            foreground=self.TEXT,
            borderwidth=0,
        )
        style.map("Secondary.TButton", background=[("active", "#E2E8F0")])
        style.configure(
            "Install.Horizontal.TProgressbar",
            troughcolor="#E9EEF5",
            background=self.PRIMARY,
            bordercolor="#E9EEF5",
            lightcolor=self.PRIMARY,
            darkcolor=self.PRIMARY,
            thickness=10,
        )

    def set_window_icon(self) -> None:
        icon = resource_path("static/ico/app.ico")
        if icon.exists():
            try:
                self.root.iconbitmap(default=str(icon))
            except Exception:
                pass

    def build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="Root.TFrame", padding=24)
        outer.pack(fill=BOTH, expand=True)

        card = ttk.Frame(outer, style="Card.TFrame", padding=30)
        card.pack(fill=BOTH, expand=True)
        card.columnconfigure(0, weight=1)

        title = ttk.Label(card, text=f"Cài đặt {APP_NAME}", style="Title.TLabel")
        title.grid(row=0, column=0, sticky="w")

        subtitle = ttk.Label(
            card,
            text=(
                f"Bộ cài {APP_VERSION} · Luôn tải release mới nhất từ GitHub\n"
                "Chọn nơi lưu ứng dụng. Shortcut Desktop sẽ được tạo tự động."
            ),
            style="Subtitle.TLabel",
            justify=LEFT,
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(6, 26))

        ttk.Label(card, text="Thư mục cài đặt", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 8)
        )

        path_row = ttk.Frame(card, style="Card.TFrame")
        path_row.grid(row=3, column=0, sticky="ew")
        path_row.columnconfigure(0, weight=1)

        self.path_entry = ttk.Entry(path_row, textvariable=self.install_dir, font=("Segoe UI", 10))
        self.path_entry.grid(row=0, column=0, sticky="ew", ipady=8)

        self.browse_button = ttk.Button(
            path_row,
            text="Chọn thư mục",
            style="Secondary.TButton",
            command=self.choose_directory,
        )
        self.browse_button.grid(row=0, column=1, padx=(10, 0))

        progress_block = ttk.Frame(card, style="Card.TFrame")
        progress_block.grid(row=4, column=0, sticky="ew", pady=(26, 0))
        progress_block.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(
            progress_block,
            mode="determinate",
            maximum=100,
            style="Install.Horizontal.TProgressbar",
        )
        self.progress.grid(row=0, column=0, sticky="ew")

        status_row = ttk.Frame(progress_block, style="Card.TFrame")
        status_row.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        status_row.columnconfigure(0, weight=1)

        self.status_label = ttk.Label(status_row, textvariable=self.status, style="Status.TLabel")
        self.status_label.grid(row=0, column=0, sticky="w")
        ttk.Label(status_row, textvariable=self.progress_text, style="Status.TLabel").grid(
            row=0, column=1, sticky="e"
        )

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=5, column=0, sticky="ew", pady=(30, 0))
        actions.columnconfigure(0, weight=1)

        self.close_button = ttk.Button(
            actions,
            text="Đóng",
            style="Secondary.TButton",
            command=self.on_close,
        )
        self.close_button.grid(row=0, column=1, padx=(0, 10))

        self.primary_button = ttk.Button(
            actions,
            text="Cài đặt",
            style="Primary.TButton",
            command=self.primary_action,
        )
        self.primary_button.grid(row=0, column=2)

    def choose_directory(self) -> None:
        initial = Path(self.install_dir.get()).expanduser()
        selected = filedialog.askdirectory(
            title=f"Chọn thư mục cài đặt {APP_NAME}",
            initialdir=str(initial if initial.exists() else initial.parent),
            mustexist=False,
        )
        if selected:
            self.install_dir.set(selected)

    def primary_action(self) -> None:
        if self.installed_executable:
            self.launch_installed_app()
            return
        self.start_installation()

    def start_installation(self) -> None:
        raw_path = self.install_dir.get().strip().strip('"')
        if not raw_path:
            messagebox.showwarning("Thiếu thư mục", "Hãy chọn thư mục cài đặt.")
            return

        install_dir = Path(os.path.expandvars(raw_path)).expanduser()
        self.install_dir.set(str(install_dir))
        self.set_busy(True)
        self.progress.configure(value=0, mode="determinate")
        self.progress_text.set("0%")
        self.set_status("Đang chuẩn bị cài đặt…")

        worker = threading.Thread(
            target=self.install_worker,
            args=(install_dir,),
            name="NexusInstallerWorker",
            daemon=True,
        )
        worker.start()

    def install_worker(self, install_dir: Path) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="nexus_installer_"))
        archive = temp_dir / "nexus_latest_release.zip"
        try:
            ensure_writable_directory(install_dir)
            self.events.put(("status", "Đang kiểm tra release mới nhất trên GitHub…"))
            release = resolve_latest_release()

            self.events.put(
                (
                    "status",
                    f"Đang tải {release['asset_name']} · {release['tag']}…",
                )
            )
            download_archive(
                release["download_url"],
                archive,
                lambda current, total: self.events.put(
                    ("download_progress", current, total)
                ),
            )

            self.events.put(("status", "Đang giải nén vào thư mục đã chọn…"))
            extract_archive(
                archive,
                install_dir,
                lambda current, total: self.events.put(
                    ("extract_progress", current, total)
                ),
            )
            write_installed_version(install_dir, release["version"])

            executable = find_application_executable(install_dir)
            self.events.put(("status", "Đang tạo shortcut ngoài Desktop…"))
            shortcut = create_desktop_shortcut(executable)
            self.events.put(
                (
                    "success",
                    str(executable),
                    str(shortcut),
                    release["version"],
                )
            )
        except urllib.error.HTTPError as exc:
            detail = f"GitHub trả về lỗi HTTP {exc.code}."
            if exc.code == 404:
                detail += " Repo chưa có release công khai hoặc release mới nhất không tồn tại."
            elif exc.code == 403:
                detail += " GitHub có thể đang giới hạn lượt truy cập API; hãy thử lại sau."
            self.events.put(("error", detail))
        except urllib.error.URLError as exc:
            self.events.put(
                (
                    "error",
                    "Không thể kết nối GitHub qua HTTPS. "
                    "Hãy kiểm tra Internet, ngày giờ Windows và chứng chỉ hệ thống. "
                    f"Chi tiết: {exc.reason}",
                )
            )
        except PermissionError:
            self.events.put(
                (
                    "error",
                    "Không thể ghi đè tệp. Hãy đóng Nexus nếu đang chạy hoặc chọn thư mục khác.",
                )
            )
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "status":
                    self.set_status(event[1])
                elif kind == "download_progress":
                    current, total = event[1], event[2]
                    if total > 0:
                        percent = min(70, int(current * 70 / total))
                        self.progress.configure(mode="determinate", value=percent)
                        self.progress_text.set(f"{int(current * 100 / total)}% tải xuống")
                    else:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                        self.progress_text.set(f"{current / (1024 * 1024):.1f} MB")
                elif kind == "extract_progress":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                    current, total = event[1], event[2]
                    percent = 70 + int(current * 25 / max(total, 1))
                    self.progress.configure(value=min(percent, 95))
                    self.progress_text.set(f"{int(current * 100 / max(total, 1))}% giải nén")
                elif kind == "success":
                    self.handle_success(Path(event[1]), Path(event[2]), event[3])
                elif kind == "error":
                    self.handle_error(event[1])
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self.process_events)

    def set_status(self, text: str, color: str | None = None) -> None:
        self.status.set(text)
        self.status_label.configure(foreground=color or self.MUTED)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = DISABLED if busy else NORMAL
        self.path_entry.configure(state=state)
        self.browse_button.configure(state=state)
        self.close_button.configure(state=state)
        self.primary_button.configure(state=DISABLED if busy else NORMAL)
        if busy:
            self.primary_button.configure(text="Đang cài đặt…")
        elif not self.installed_executable:
            self.primary_button.configure(text="Cài đặt")

    def handle_success(self, executable: Path, shortcut: Path, version: str) -> None:
        self.installed_executable = executable
        self.set_busy(False)
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.progress_text.set("Hoàn tất")
        self.set_status(
            f"Cài đặt thành công · Phiên bản {normalize_version(version)}",
            self.SUCCESS,
        )
        self.primary_button.configure(text=f"Mở {APP_NAME}", state=NORMAL)
        self.close_button.configure(state=NORMAL)
        messagebox.showinfo(
            "Cài đặt thành công",
            f"{APP_NAME} {normalize_version(version)} đã được cài vào:\n"
            f"{executable.parent}\n\nShortcut đã tạo tại:\n{shortcut}",
        )

    def handle_error(self, error: str) -> None:
        self.installed_executable = None
        self.set_busy(False)
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.progress_text.set("")
        self.set_status("Cài đặt chưa hoàn tất", self.ERROR)
        messagebox.showerror("Không thể cài đặt", error or "Đã xảy ra lỗi không xác định.")

    def launch_installed_app(self) -> None:
        executable = self.installed_executable
        if not executable or not executable.exists():
            messagebox.showerror("Không tìm thấy ứng dụng", f"Không tìm thấy {EXPECTED_EXE_NAME}.")
            self.installed_executable = None
            self.primary_button.configure(text="Cài đặt")
            return
        try:
            subprocess.Popen(
                [str(executable)],
                cwd=str(executable.parent),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            self.root.destroy()
        except OSError as exc:
            messagebox.showerror("Không thể mở ứng dụng", str(exc))

    def on_close(self) -> None:
        if self.busy:
            messagebox.showinfo(
                "Đang cài đặt",
                "Hãy chờ quá trình tải và giải nén hoàn tất trước khi đóng.",
            )
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    if os.name != "nt":
        raise SystemExit("Nexus Installer chỉ hỗ trợ Windows.")
    InstallerApp().run()
