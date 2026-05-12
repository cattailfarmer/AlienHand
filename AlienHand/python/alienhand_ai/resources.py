from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
from time import perf_counter, time
from typing import Any


JsonDict = dict[str, Any]


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@dataclass(frozen=True)
class ResourceSnapshot:
    timestamp: float
    process: JsonDict
    system: JsonDict
    gpu: JsonDict
    network: JsonDict

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class _ProcessCpuSample:
    wall_time: float
    cpu_time: float


class ResourceMonitor:
    def __init__(self) -> None:
        self._last_process_cpu: dict[int, _ProcessCpuSample] = {}

    def sample(self, process_id: int | None = None, drive_path: str | Path | None = None) -> ResourceSnapshot:
        return ResourceSnapshot(
            timestamp=time(),
            process=self._sample_process(process_id),
            system=sample_system_resources(drive_path),
            gpu=sample_gpu_resources(),
            network=sample_network_resources(),
        )

    def _sample_process(self, process_id: int | None) -> JsonDict:
        if not process_id:
            return {"available": False, "reason": "no process id"}

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, process_id)
        if not handle:
            return {"available": False, "pid": process_id, "reason": "OpenProcess failed"}

        try:
            now = perf_counter()
            kernel_seconds, user_seconds = _process_times(handle)
            cpu_seconds = kernel_seconds + user_seconds
            previous = self._last_process_cpu.get(process_id)
            cpu_percent = None
            if previous and now > previous.wall_time:
                elapsed = now - previous.wall_time
                cpu_delta = cpu_seconds - previous.cpu_time
                cpu_percent = max(0.0, (cpu_delta / elapsed) * 100.0 / max(1, os.cpu_count() or 1))
            self._last_process_cpu[process_id] = _ProcessCpuSample(wall_time=now, cpu_time=cpu_seconds)

            payload: JsonDict = {
                "available": True,
                "pid": process_id,
                "image": _process_image_path(handle),
                "cpu_kernel_seconds": kernel_seconds,
                "cpu_user_seconds": user_seconds,
                "cpu_percent_since_last_sample": cpu_percent,
            }
            payload.update(_process_memory(handle))
            payload.update(_process_io(handle))
            return payload
        finally:
            kernel32.CloseHandle(handle)


def sample_system_resources(drive_path: str | Path | None = None) -> JsonDict:
    memory = MEMORYSTATUSEX()
    memory.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    payload: JsonDict = {"cpu_count": os.cpu_count() or 1}
    if kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        payload["memory"] = {
            "load_percent": int(memory.dwMemoryLoad),
            "total_physical_bytes": int(memory.ullTotalPhys),
            "available_physical_bytes": int(memory.ullAvailPhys),
            "total_pagefile_bytes": int(memory.ullTotalPageFile),
            "available_pagefile_bytes": int(memory.ullAvailPageFile),
            "total_virtual_bytes": int(memory.ullTotalVirtual),
            "available_virtual_bytes": int(memory.ullAvailVirtual),
        }
    else:
        payload["memory"] = {"available": False, "reason": "GlobalMemoryStatusEx failed"}

    if drive_path is not None:
        try:
            usage = shutil.disk_usage(Path(drive_path).anchor or drive_path)
            payload["drive"] = {
                "path": str(drive_path),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
            }
        except OSError as exc:
            payload["drive"] = {"available": False, "path": str(drive_path), "reason": str(exc)}
    return payload


def sample_gpu_resources() -> JsonDict:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        return {"available": False, "backend": "nvidia-smi", "reason": str(exc)}

    if result.returncode != 0:
        return {"available": False, "backend": "nvidia-smi", "reason": result.stderr.strip() or "non-zero exit"}

    gpus = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            continue
        name, utilization, memory_used, memory_total = parts
        gpus.append(
            {
                "name": name,
                "utilization_percent": _safe_int(utilization),
                "memory_used_mib": _safe_int(memory_used),
                "memory_total_mib": _safe_int(memory_total),
            }
        )
    return {"available": bool(gpus), "backend": "nvidia-smi", "gpus": gpus}


def sample_network_resources() -> JsonDict:
    try:
        import psutil  # type: ignore
    except ImportError:
        return {"available": False, "backend": "psutil", "reason": "psutil not installed"}

    counters = psutil.net_io_counters(pernic=True)
    return {
        "available": True,
        "backend": "psutil",
        "interfaces": {
            name: {
                "bytes_sent": value.bytes_sent,
                "bytes_recv": value.bytes_recv,
                "packets_sent": value.packets_sent,
                "packets_recv": value.packets_recv,
                "errin": value.errin,
                "errout": value.errout,
                "dropin": value.dropin,
                "dropout": value.dropout,
            }
            for name, value in counters.items()
        },
    }


def _process_times(handle: int) -> tuple[float, float]:
    creation = FILETIME()
    exit_time = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)):
        return 0.0, 0.0
    return _filetime_seconds(kernel), _filetime_seconds(user)


def _process_memory(handle: int) -> JsonDict:
    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return {"memory": {"available": False, "reason": "GetProcessMemoryInfo failed"}}
    return {
        "memory": {
            "working_set_bytes": int(counters.WorkingSetSize),
            "peak_working_set_bytes": int(counters.PeakWorkingSetSize),
            "private_bytes": int(counters.PrivateUsage),
            "pagefile_bytes": int(counters.PagefileUsage),
            "page_fault_count": int(counters.PageFaultCount),
        }
    }


def _process_io(handle: int) -> JsonDict:
    counters = IO_COUNTERS()
    if not kernel32.GetProcessIoCounters(handle, ctypes.byref(counters)):
        return {"io": {"available": False, "reason": "GetProcessIoCounters failed"}}
    return {
        "io": {
            "read_operation_count": int(counters.ReadOperationCount),
            "write_operation_count": int(counters.WriteOperationCount),
            "other_operation_count": int(counters.OtherOperationCount),
            "read_transfer_bytes": int(counters.ReadTransferCount),
            "write_transfer_bytes": int(counters.WriteTransferCount),
            "other_transfer_bytes": int(counters.OtherTransferCount),
        }
    }


def _process_image_path(handle: int) -> str | None:
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return buffer.value
    return None


def _filetime_seconds(value: FILETIME) -> float:
    ticks = (int(value.dwHighDateTime) << 32) + int(value.dwLowDateTime)
    return ticks / 10_000_000.0


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
