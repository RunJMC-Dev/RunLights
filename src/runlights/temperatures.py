"""Background, read-only CPU/GPU temperature acquisition."""
from __future__ import annotations
import atexit
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[2]
BRIDGE = Path(__file__).with_name("temperature_bridge.ps1")

def _find_libre_hardware_monitor_dll() -> Path | None:
    """Locate an installed or portable LibreHardwareMonitor library."""
    candidates = [
        SCRIPT_DIR / "LibreHardwareMonitorLib.dll",
        SCRIPT_DIR / "LibreHardwareMonitor" / "LibreHardwareMonitorLib.dll",
    ]
    executable = shutil.which("LibreHardwareMonitor")
    if executable:
        candidates.append(Path(executable).resolve().parent / "LibreHardwareMonitorLib.dll")

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        package_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if package_root.exists():
            candidates.extend(
                package_root.glob(
                    "LibreHardwareMonitor.LibreHardwareMonitor*/**/LibreHardwareMonitorLib.dll"
                )
            )

    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            candidates.extend(
                [
                    Path(base) / "LibreHardwareMonitor" / "LibreHardwareMonitorLib.dll",
                    Path(base) / "Libre Hardware Monitor" / "LibreHardwareMonitorLib.dll",
                ]
            )

    return next((path for path in candidates if path.is_file()), None)


def _run_hidden(command: list[str], timeout: int = 8) -> str:
    """Run a command without flashing a console window on Windows."""
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _hardware_monitor_temperatures() -> tuple[float | None, float | None, str | None]:
    """Read temperatures exported to WMI by a supported hardware monitor."""
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return None, None, None

    # Prefer the modern provider, while retaining compatibility with OHM.
    for namespace, provider_name in (
        (r"root\LibreHardwareMonitor", "LibreHardwareMonitor"),
        (r"root\OpenHardwareMonitor", "OpenHardwareMonitor"),
    ):
        script = (
            "$ErrorActionPreference='Stop';"
            f"Get-CimInstance -Namespace '{namespace}' -ClassName Sensor | "
            "Where-Object {$_.SensorType -eq 'Temperature'} | "
            "Select-Object Name,Value,Parent | ConvertTo-Json -Compress"
        )
        raw = _run_hidden([powershell, "-NoProfile", "-Command", script])
        if not raw:
            continue

        try:
            sensors = json.loads(raw)
            if isinstance(sensors, dict):
                sensors = [sensors]
        except (json.JSONDecodeError, TypeError):
            continue

        cpu_values: list[float] = []
        gpu_values: list[float] = []
        preferred_cpu: list[float] = []
        preferred_gpu: list[float] = []

        for sensor in sensors:
            try:
                value = float(sensor.get("Value"))
            except (TypeError, ValueError, AttributeError):
                continue
            description = " ".join(
                str(sensor.get(key, "")) for key in ("Name", "Parent")
            ).lower()
            name = str(sensor.get("Name", "")).lower()

            if any(word in description for word in ("gpu", "nvidia", "radeon")):
                gpu_values.append(value)
                if "core" in name:
                    preferred_gpu.append(value)
            elif any(word in description for word in ("cpu", "package", "core")):
                cpu_values.append(value)
                if "package" in name or "tctl" in name or "tdie" in name:
                    preferred_cpu.append(value)

        cpu = max(preferred_cpu or cpu_values, default=None)
        gpu = max(preferred_gpu, default=None)
        if cpu is not None or gpu is not None:
            return cpu, gpu, provider_name

    return None, None, None


def _nvidia_temperature() -> float | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        common_path = Path(
            os.environ.get("ProgramFiles", r"C:\Program Files")
        ) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
        executable = str(common_path) if common_path.exists() else None
    if not executable:
        return None

    output = _run_hidden(
        [
            executable,
            "--query-gpu=temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    values = []
    for line in output.splitlines():
        try:
            values.append(float(line.strip()))
        except ValueError:
            pass
    return max(values, default=None)



@dataclass(frozen=True)
class Reading:
    cpu: float | None = None
    gpu: float | None = None
    source: str = "Unavailable"
    updated_at: float = 0.0


def valid_temperature(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and -20 <= value <= 150 else None
    except (TypeError, ValueError):
        return None


class SensorBridge:
    """One hidden helper; bounded replies keep failed drivers off the UI thread."""
    def __init__(self):
        self.process = None
        self.responses = queue.Queue()

    def start(self):
        dll = _find_libre_hardware_monitor_dll()
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if not dll or not shell:
            raise OSError("LibreHardwareMonitor library or PowerShell unavailable")
        self.responses = queue.Queue()
        self.process = subprocess.Popen(
            [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BRIDGE), "-DllPath", str(dll)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        def read_lines(process, responses):
            try:
                for line in process.stdout:
                    responses.put(line)
            finally:
                responses.put(None)
        threading.Thread(target=read_lines, args=(self.process, self.responses), daemon=True).start()
        self.response()

    def response(self):
        try:
            line = self.responses.get(timeout=8)
        except queue.Empty as exc:
            raise TimeoutError("Sensor helper timed out") from exc
        if line is None:
            raise OSError("Sensor helper exited")
        response = json.loads(line.lstrip("\ufeff"))
        if not response.get("ok"):
            raise OSError(response.get("error", "Sensor helper failed"))
        return response

    def read(self):
        if self.process is None or self.process.poll() is not None:
            self.close()
            self.start()
        self.process.stdin.write('scan\n')
        self.process.stdin.flush()
        response = self.response()
        return response.get("cpu"), response.get("gpu")

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()  # EOF lets the helper close its hardware handles.
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=2)
        finally:
            if process.stdout:
                process.stdout.close()


class TemperatureService:
    def __init__(self):
        self._reading = Reading()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._poll, daemon=True, name="temperatures")
            self._thread.start()

    def snapshot(self):
        reading = self._reading
        return reading if time.monotonic() - reading.updated_at <= 10 else Reading()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)

    def _poll(self):
        bridge = SensorBridge()
        retry = {"direct": 0.0, "wmi": 0.0, "nvidia": 0.0}
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                cpu = gpu = None
                sources = []
                if started >= retry["direct"]:
                    try:
                        cpu, gpu = map(valid_temperature, bridge.read())
                        if cpu is not None or gpu is not None:
                            sources.append("LibreHardwareMonitor direct")
                        else:
                            retry["direct"] = started + 60
                    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError):
                        bridge.close()
                        retry["direct"] = started + 60
                if (cpu is None or gpu is None) and started >= retry["wmi"]:
                    try:
                        wc, wg, source = _hardware_monitor_temperatures()
                        wc, wg = valid_temperature(wc), valid_temperature(wg)
                        if (cpu is None and wc is not None) or (gpu is None and wg is not None):
                            sources.append(source or "WMI")
                        cpu = cpu if cpu is not None else wc
                        gpu = gpu if gpu is not None else wg
                        if wc is None and wg is None:
                            retry["wmi"] = started + 60
                    except (OSError, ValueError, subprocess.SubprocessError):
                        retry["wmi"] = started + 60
                if gpu is None and started >= retry["nvidia"]:
                    try:
                        gpu = valid_temperature(_nvidia_temperature())
                        if gpu is not None:
                            sources.append("nvidia-smi")
                        else:
                            retry["nvidia"] = started + 60
                    except (OSError, subprocess.SubprocessError):
                        retry["nvidia"] = started + 60
                # Timestamp the beginning of acquisition, not the end of slow fallbacks.
                self._reading = Reading(cpu, gpu, " + ".join(sources) or "Unavailable", started)
                self._stop.wait(max(0.1, 2 - (time.monotonic() - started)))
        finally:
            bridge.close()


temperatures = TemperatureService()
atexit.register(temperatures.close)
