"""Small clean-room controller for the connected FIFINE D6.

The D6 exposes a vendor-defined HID interface (VID 0x3142, PID 0x0007)
with 512 data bytes plus a report-ID byte.  The command shapes here were
derived from device responses and are intentionally kept separate from the
installed FIFINE application.

Image notes:
* The tested key-image path accepts a 100x100 JPEG.
* Images are rotated 180 degrees before upload because the panel is mounted
  upside down relative to the host image coordinate system.
* The device receives each image as 512-byte data chunks.

This module uses only the Windows API and Pillow for image preparation.
"""

from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


VID = 0x3142
PID = 0x0007
REPORT_ID = 0
REPORT_DATA_SIZE = 512
REPORT_SIZE = REPORT_DATA_SIZE + 1
KEY_IMAGE_SIZE = (100, 100)
HEARTBEAT_PAYLOAD = b"CRT\0\0CONNECT\0\0\0"
USB_DEVICE_INTERFACE_GUID = "A5DCBF10-6530-11D2-901F-00C04FB951ED"
# The installed vendor library's lamp-control capability flag is false for
# the connected D6 (VID/PID 3142:0007, firmware V2.D6.00.002).
D6_RGB_SUPPORTED = False

# Input reports use raw hardware IDs that are vertically reversed relative to
# the physical labels: hardware 0x01–0x05 are the top row and 0x0B–0x0F are the
# bottom row. This map keeps logical/service labels aligned with D6 events.
KEY_TO_DEVICE_ID = {
    1: 0x0B,
    2: 0x0C,
    3: 0x0D,
    4: 0x0E,
    5: 0x0F,
    6: 0x06,
    7: 0x07,
    8: 0x08,
    9: 0x09,
    10: 0x0A,
    11: 0x01,
    12: 0x02,
    13: 0x03,
    14: 0x04,
    15: 0x05,
}
DEVICE_ID_TO_KEY = {value: key for key, value in KEY_TO_DEVICE_ID.items()}

# LCD image commands use direct 1–15 panel slots rather than the input report
# IDs. Keep this separate from KEY_TO_DEVICE_ID or artwork and actions land on
# different physical buttons.
KEY_TO_IMAGE_DEVICE_ID = {key: key for key in range(1, 16)}


class D6Error(RuntimeError):
    """Raised when the D6 transport rejects an operation."""


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", _GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class _WINUSB_INTERFACE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ubyte),
        ("DescriptorType", ctypes.c_ubyte),
        ("InterfaceNumber", ctypes.c_ubyte),
        ("AlternateSetting", ctypes.c_ubyte),
        ("NumEndpoints", ctypes.c_ubyte),
        ("InterfaceClass", ctypes.c_ubyte),
        ("InterfaceSubClass", ctypes.c_ubyte),
        ("InterfaceProtocol", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
    ]


class _WINUSB_PIPE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PipeType", ctypes.c_int),
        ("PipeId", ctypes.c_ubyte),
        ("MaximumPacketSize", wintypes.USHORT),
        ("Interval", ctypes.c_ubyte),
    ]


def _win_error(operation: str) -> D6Error:
    error = ctypes.get_last_error()
    return D6Error(f"{operation} failed with Windows error {error}")


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _setupapi():
    return ctypes.WinDLL("setupapi", use_last_error=True)


def _guid_from_string(value: str) -> _GUID:
    import uuid

    parsed = uuid.UUID(value)
    result = _GUID()
    ctypes.memmove(ctypes.byref(result), parsed.bytes_le, ctypes.sizeof(result))
    return result


def _find_interface_paths(interface_guid: _GUID, *, match_vid_pid: bool = True) -> list[str]:
    """Enumerate present device-interface paths for one interface class."""

    setup = _setupapi()
    setup.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.LPCWSTR,
        wintypes.HWND,
        wintypes.DWORD,
    ]
    setup.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setup.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ]
    setup.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    setup.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setup.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    setup.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    DIGCF_PRESENT = 0x00000002
    DIGCF_DEVICEINTERFACE = 0x00000010
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    info_set = setup.SetupDiGetClassDevsW(
        ctypes.byref(interface_guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if info_set in (None, INVALID_HANDLE_VALUE):
        raise _win_error("SetupDiGetClassDevsW")

    paths: list[str] = []
    try:
        index = 0
        while True:
            interface = _SP_DEVICE_INTERFACE_DATA()
            interface.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
            if not setup.SetupDiEnumDeviceInterfaces(
                info_set, None, ctypes.byref(interface_guid), index, ctypes.byref(interface)
            ):
                break
            required = wintypes.DWORD()
            setup.SetupDiGetDeviceInterfaceDetailW(
                info_set,
                ctypes.byref(interface),
                None,
                0,
                ctypes.byref(required),
                None,
            )
            detail = ctypes.create_string_buffer(required.value)
            ctypes.cast(detail, ctypes.POINTER(wintypes.DWORD))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            )
            if setup.SetupDiGetDeviceInterfaceDetailW(
                info_set,
                ctypes.byref(interface),
                detail,
                required.value,
                ctypes.byref(required),
                None,
            ):
                path = ctypes.wstring_at(ctypes.addressof(detail) + 4)
                lowered = path.lower()
                if not match_vid_pid or (
                    f"vid_{VID:04x}" in lowered and f"pid_{PID:04x}" in lowered
                ):
                    paths.append(path)
            index += 1
    finally:
        setup.SetupDiDestroyDeviceInfoList(info_set)
    return paths


def find_device_paths() -> list[str]:
    """Return present HID interface paths matching the D6 VID/PID."""

    hid = ctypes.WinDLL("hid", use_last_error=True)
    setup = _setupapi()
    hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(_GUID)]
    hid.HidD_GetHidGuid.restype = None
    hid_guid = _GUID()
    hid.HidD_GetHidGuid(ctypes.byref(hid_guid))

    setup.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.LPCWSTR,
        wintypes.HWND,
        wintypes.DWORD,
    ]
    setup.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setup.SetupDiEnumDeviceInterfaces.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
    ]
    setup.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    setup.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setup.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    setup.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    DIGCF_PRESENT = 0x00000002
    DIGCF_DEVICEINTERFACE = 0x00000010
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    info_set = setup.SetupDiGetClassDevsW(
        ctypes.byref(hid_guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if info_set in (None, INVALID_HANDLE_VALUE):
        raise _win_error("SetupDiGetClassDevsW")

    paths: list[str] = []
    try:
        index = 0
        while True:
            interface = _SP_DEVICE_INTERFACE_DATA()
            interface.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
            if not setup.SetupDiEnumDeviceInterfaces(
                info_set, None, ctypes.byref(hid_guid), index, ctypes.byref(interface)
            ):
                break
            required = wintypes.DWORD()
            setup.SetupDiGetDeviceInterfaceDetailW(
                info_set,
                ctypes.byref(interface),
                None,
                0,
                ctypes.byref(required),
                None,
            )
            detail = ctypes.create_string_buffer(required.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA.cbSize is 8 on 64-bit Windows
            # and 6 on 32-bit Windows; the device path follows that header.
            ctypes.cast(detail, ctypes.POINTER(wintypes.DWORD))[0] = (
                8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            )
            if setup.SetupDiGetDeviceInterfaceDetailW(
                info_set,
                ctypes.byref(interface),
                detail,
                required.value,
                ctypes.byref(required),
                None,
            ):
                # The flexible DevicePath member starts immediately after the
                # DWORD cbSize field. The header value is 8 on 64-bit Windows,
                # but the string itself is still at byte offset 4.
                path = ctypes.wstring_at(ctypes.addressof(detail) + 4)
                lowered = path.lower()
                if f"vid_{VID:04x}" in lowered and f"pid_{PID:04x}" in lowered:
                    paths.append(path)
            index += 1
    finally:
        setup.SetupDiDestroyDeviceInfoList(info_set)
    return paths


def find_usb_device_paths() -> list[str]:
    """Return USB interface paths matching the D6 VID/PID.

    This is the path a WinUSB-backed device would expose. The D6 currently
    enumerates through HidUsb, so this is primarily used for transport probing
    and for machines with a compatible WinUSB driver binding.
    """

    return _find_interface_paths(_guid_from_string(USB_DEVICE_INTERFACE_GUID))


class D6Controller:
    """A direct controller for one D6 device.

    HID is the verified transport for the connected D6. WinUSB is available
    for compatible driver bindings, and ``auto`` tries it before falling back
    to HID. The default remains HID so a driver change is never required.
    """

    def __init__(self, device_path: str | None = None, *, transport: str = "hid"):
        self._kernel = _kernel32()
        self._configure_api()
        requested_transport = transport.strip().lower()
        if requested_transport not in {"hid", "winusb", "auto"}:
            raise ValueError("transport must be hid, winusb, or auto")
        self.transport = requested_transport
        self.transport_note: str | None = None
        self.handle = None
        self.winusb_handle = None
        self.input_pipe: int | None = None
        self.output_pipe: int | None = None
        self.winusb_error: str | None = None

        if requested_transport in {"winusb", "auto"}:
            try:
                self._open_winusb(device_path)
                return
            except Exception as exc:
                self.winusb_error = str(exc)
                self._close_winusb()
                if requested_transport == "winusb":
                    raise
                self.transport_note = f"WinUSB unavailable; using HID ({exc})"

        self._open_hid(device_path)

    def _open_hid(self, device_path: str | None) -> None:
        if device_path is None:
            paths = find_device_paths()
            if not paths:
                raise D6Error(f"No HID device found for VID {VID:04x}, PID {PID:04x}")
            device_path = paths[0]
        self.transport = "hid"
        self.device_path = device_path
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        FILE_FLAG_OVERLAPPED = 0x40000000
        self.handle = self._kernel.CreateFileW(
            device_path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            None,
        )
        if self.handle in (None, ctypes.c_void_p(-1).value):
            raise _win_error("CreateFileW")
        # The vendor library performs the same HID queue initialization.
        if not self._hid.HidD_SetNumInputBuffers(self.handle, 64):
            self.transport_note = "HID input buffer configuration was rejected"

    def _open_winusb(self, device_path: str | None) -> None:
        paths = [device_path] if device_path else find_usb_device_paths()
        if not paths:
            raise D6Error(f"No USB interface found for VID {VID:04x}, PID {PID:04x}")
        path = paths[0]
        GENERIC_READ = 0x80000000
        GENERIC_WRITE = 0x40000000
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        OPEN_EXISTING = 3
        FILE_FLAG_OVERLAPPED = 0x40000000
        handle = self._kernel.CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle in (None, ctypes.c_void_p(-1).value):
            raise _win_error("CreateFileW(WinUSB)")
        interface_handle = wintypes.HANDLE()
        try:
            if not self._winusb.WinUsb_Initialize(handle, ctypes.byref(interface_handle)):
                raise _win_error("WinUsb_Initialize")
            descriptor = _WINUSB_INTERFACE_DESCRIPTOR()
            if not self._winusb.WinUsb_QueryInterfaceSettings(
                interface_handle, 0, ctypes.byref(descriptor)
            ):
                raise _win_error("WinUsb_QueryInterfaceSettings")
            for index in range(descriptor.NumEndpoints):
                pipe = _WINUSB_PIPE_INFORMATION()
                if not self._winusb.WinUsb_QueryPipe(
                    interface_handle, 0, index, ctypes.byref(pipe)
                ):
                    raise _win_error("WinUsb_QueryPipe")
                if pipe.PipeId & 0x80 and self.input_pipe is None:
                    self.input_pipe = pipe.PipeId
                elif not pipe.PipeId & 0x80 and self.output_pipe is None:
                    self.output_pipe = pipe.PipeId
            if self.input_pipe is None or self.output_pipe is None:
                raise D6Error(
                    "WinUSB interface has no usable input/output pipes "
                    f"(in={self.input_pipe}, out={self.output_pipe})"
                )
            self.handle = handle
            self.winusb_handle = interface_handle
            self.device_path = path
            self.transport = "winusb"
            handle = None
            interface_handle = None
        finally:
            if interface_handle:
                self._winusb.WinUsb_Free(interface_handle)
            if handle not in (None, ctypes.c_void_p(-1).value):
                self._kernel.CloseHandle(handle)

    def _configure_api(self) -> None:
        k = self._kernel
        self._hid = ctypes.WinDLL("hid", use_last_error=True)
        self._hid.HidD_SetNumInputBuffers.argtypes = [wintypes.HANDLE, wintypes.ULONG]
        self._hid.HidD_SetNumInputBuffers.restype = wintypes.BOOL
        self._winusb = ctypes.WinDLL("winusb", use_last_error=True)
        self._winusb.WinUsb_Initialize.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self._winusb.WinUsb_Initialize.restype = wintypes.BOOL
        self._winusb.WinUsb_QueryInterfaceSettings.argtypes = [
            wintypes.HANDLE,
            ctypes.c_ubyte,
            ctypes.POINTER(_WINUSB_INTERFACE_DESCRIPTOR),
        ]
        self._winusb.WinUsb_QueryInterfaceSettings.restype = wintypes.BOOL
        self._winusb.WinUsb_QueryPipe.argtypes = [
            wintypes.HANDLE,
            ctypes.c_ubyte,
            ctypes.c_ubyte,
            ctypes.POINTER(_WINUSB_PIPE_INFORMATION),
        ]
        self._winusb.WinUsb_QueryPipe.restype = wintypes.BOOL
        self._winusb.WinUsb_ReadPipe.argtypes = [
            wintypes.HANDLE,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
            ctypes.POINTER(_OVERLAPPED),
        ]
        self._winusb.WinUsb_ReadPipe.restype = wintypes.BOOL
        self._winusb.WinUsb_WritePipe.argtypes = [
            wintypes.HANDLE,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
            ctypes.POINTER(_OVERLAPPED),
        ]
        self._winusb.WinUsb_WritePipe.restype = wintypes.BOOL
        self._winusb.WinUsb_AbortPipe.argtypes = [wintypes.HANDLE, ctypes.c_ubyte]
        self._winusb.WinUsb_AbortPipe.restype = wintypes.BOOL
        self._winusb.WinUsb_Free.argtypes = [wintypes.HANDLE]
        self._winusb.WinUsb_Free.restype = wintypes.BOOL
        k.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateEventW.restype = wintypes.HANDLE
        k.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_OVERLAPPED),
        ]
        k.WriteFile.restype = wintypes.BOOL
        k.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(_OVERLAPPED),
        ]
        k.ReadFile.restype = wintypes.BOOL
        k.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_OVERLAPPED),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        k.GetOverlappedResult.restype = wintypes.BOOL
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
        k.CancelIoEx.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL

    def close(self) -> None:
        self._close_winusb()
        if getattr(self, "handle", None) not in (None, ctypes.c_void_p(-1).value):
            self._kernel.CloseHandle(self.handle)
            self.handle = None

    def _close_winusb(self) -> None:
        interface_handle = getattr(self, "winusb_handle", None)
        if interface_handle:
            self._winusb.WinUsb_Free(interface_handle)
            self.winusb_handle = None
        if (
            getattr(self, "handle", None) not in (None, ctypes.c_void_p(-1).value)
            and getattr(self, "transport", None) == "winusb"
        ):
            self._kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> "D6Controller":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _frame(payload: bytes) -> bytes:
        if len(payload) > REPORT_DATA_SIZE:
            raise D6Error(f"Payload is {len(payload)} bytes; maximum is {REPORT_DATA_SIZE}")
        return bytes([REPORT_ID]) + payload.ljust(REPORT_DATA_SIZE, b"\0")

    def _winusb_transfer(self, data: bytes, *, write: bool, timeout_ms: int) -> bytes | None:
        if not self.winusb_handle:
            raise D6Error("WinUSB interface is not initialized")
        pipe_id = self.output_pipe if write else self.input_pipe
        if pipe_id is None:
            raise D6Error("WinUSB endpoint is not available")
        event = self._kernel.CreateEventW(None, True, False, None)
        if not event:
            raise _win_error("CreateEventW")
        overlapped = _OVERLAPPED()
        overlapped.hEvent = event
        buffer = ctypes.create_string_buffer(data if write else REPORT_SIZE)
        count = wintypes.ULONG()
        try:
            function = self._winusb.WinUsb_WritePipe if write else self._winusb.WinUsb_ReadPipe
            ok = function(
                self.winusb_handle,
                pipe_id,
                buffer,
                len(data) if write else REPORT_SIZE,
                ctypes.byref(count),
                ctypes.byref(overlapped),
            )
            error = ctypes.get_last_error()
            if not ok and error != 997:  # ERROR_IO_PENDING
                raise _win_error("WinUsb_WritePipe" if write else "WinUsb_ReadPipe")
            if not ok:
                wait_result = self._kernel.WaitForSingleObject(event, timeout_ms)
                if wait_result != 0:
                    self._winusb.WinUsb_AbortPipe(self.winusb_handle, pipe_id)
                    if write:
                        raise D6Error(f"WinUSB write timed out after {timeout_ms} ms")
                    return None
                if not self._kernel.GetOverlappedResult(
                    self.handle, ctypes.byref(overlapped), ctypes.byref(count), False
                ):
                    raise _win_error("GetOverlappedResult")
            if write:
                if count.value != len(data):
                    raise D6Error(f"Short WinUSB write: {count.value}/{len(data)} bytes")
                return None
            return bytes(buffer.raw[: count.value])
        finally:
            self._kernel.CloseHandle(event)

    def _transfer(self, data: bytes, *, write: bool, timeout_ms: int) -> bytes | None:
        if self.transport == "winusb":
            return self._winusb_transfer(data, write=write, timeout_ms=timeout_ms)
        event = self._kernel.CreateEventW(None, True, False, None)
        if not event:
            raise _win_error("CreateEventW")
        overlapped = _OVERLAPPED()
        overlapped.hEvent = event
        buffer = ctypes.create_string_buffer(data if write else REPORT_SIZE)
        count = wintypes.DWORD()
        try:
            if write:
                ok = self._kernel.WriteFile(
                    self.handle, buffer, len(data), ctypes.byref(count), ctypes.byref(overlapped)
                )
            else:
                ok = self._kernel.ReadFile(
                    self.handle, buffer, REPORT_SIZE, ctypes.byref(count), ctypes.byref(overlapped)
                )
            error = ctypes.get_last_error()
            if not ok and error != 997:  # ERROR_IO_PENDING
                raise _win_error("WriteFile" if write else "ReadFile")
            if not ok:
                wait_result = self._kernel.WaitForSingleObject(event, timeout_ms)
                if wait_result != 0:
                    self._kernel.CancelIoEx(self.handle, ctypes.byref(overlapped))
                    if write:
                        raise D6Error(f"HID write timed out after {timeout_ms} ms")
                    return None
                if not self._kernel.GetOverlappedResult(
                    self.handle, ctypes.byref(overlapped), ctypes.byref(count), False
                ):
                    raise _win_error("GetOverlappedResult")
            if write:
                if count.value != len(data):
                    raise D6Error(f"Short HID write: {count.value}/{len(data)} bytes")
                return None
            return bytes(buffer.raw[: count.value])
        finally:
            self._kernel.CloseHandle(event)

    def write_payload(self, payload: bytes) -> None:
        self._transfer(self._frame(payload), write=True, timeout_ms=5000)

    def read_report(self, timeout_ms: int = 1000) -> bytes | None:
        return self._transfer(b"", write=False, timeout_ms=timeout_ms)

    def wake_screen(self) -> None:
        self.write_payload(b"CRT\0\0DIS\0\0")

    def refresh(self) -> None:
        self.write_payload(b"CRT\0\0STP\0\0")

    def heartbeat(self) -> None:
        """Send the vendor-compatible keepalive frame."""

        self.write_payload(HEARTBEAT_PAYLOAD)

    @staticmethod
    def capabilities() -> dict[str, bool]:
        """Return capabilities verified for the connected D6 model."""

        return {
            "button_events": True,
            "key_images": True,
            "brightness": True,
            "heartbeat": True,
            "rgb": D6_RGB_SUPPORTED,
        }

    def send_qucmd(self, a: int, b: int, c: int, d: int, e: int) -> None:
        """Send the vendor library's five-byte QUCMD control frame.

        The D6 vendor library exposes this as a generic command with five
        unsigned-byte parameters (named operateA/B/C/D/F in its symbols). The
        meaning of those values is device/firmware-specific; this method keeps
        the raw primitive available without pretending that RGB semantics are
        confirmed yet.
        """

        values = (a, b, c, d, e)
        if any(not 0 <= value <= 0xFF for value in values):
            raise ValueError("QUCMD parameters must be bytes between 0 and 255")
        self.write_payload(b"CRT\0\0QUCMD" + bytes(values))

    def set_brightness(self, value: int) -> None:
        if not 0 <= value <= 100:
            raise ValueError("brightness must be between 0 and 100")
        self.write_payload(b"CRT\0\0LIG\0\0" + bytes([value]))

    def clear_screen(self, target: int = 0xFF) -> None:
        if not 0 <= target <= 0xFF:
            raise ValueError("target must be a byte")
        self.write_payload(b"CRT\0\0CLE\0\0\0" + bytes([target]))
        self.refresh()

    @staticmethod
    def key_device_id(key: int) -> int:
        try:
            return KEY_TO_DEVICE_ID[key]
        except KeyError as exc:
            raise ValueError("key must be between 1 and 15") from exc

    @staticmethod
    def image_device_id(key: int) -> int:
        try:
            return KEY_TO_IMAGE_DEVICE_ID[key]
        except KeyError as exc:
            raise ValueError("key must be between 1 and 15") from exc

    @staticmethod
    def prepare_key_image(image: str | Path) -> bytes:
        try:
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise D6Error("Pillow is required for LCD image uploads") from exc
        with Image.open(image) as source:
            prepared = ImageOps.fit(source.convert("RGB"), KEY_IMAGE_SIZE)
            prepared = prepared.rotate(180)
            output = io.BytesIO()
            prepared.save(output, format="JPEG", quality=100, optimize=False)
            return output.getvalue()

    def set_key_image(self, key: int, image: str | Path | bytes) -> None:
        """Upload a 100x100 JPEG to one key and refresh the panel."""

        key_id = self.image_device_id(key)
        jpeg = self.prepare_key_image(image) if not isinstance(image, bytes) else image
        if len(jpeg) > 0xFFFF:
            raise D6Error(f"Key image is {len(jpeg)} bytes; maximum is 65535 bytes")
        # The BAT image header uses a two-byte big-endian length. A four-byte
        # little-endian length may still receive an ACK from the HID transport,
        # but the D6 will not commit the image to its LCD memory.
        announce = b"CRT\0\0BAT\0\0" + struct.pack(">H", len(jpeg)) + bytes([key_id])
        self.write_payload(announce)
        for offset in range(0, len(jpeg), REPORT_DATA_SIZE):
            self.write_payload(jpeg[offset : offset + REPORT_DATA_SIZE])
        self.refresh()

    def clear_key_image(self, key: int) -> None:
        self.write_payload(b"CRT\0\0CLE\0\0\0" + bytes([self.image_device_id(key)]))
        self.refresh()

    def apply_profile(
        self,
        profile: dict,
        *,
        scene: str | None = None,
        page: str | None = None,
        root: str | Path | None = None,
    ) -> int:
        """Apply one profile scene/page and return the number of images sent.

        A profile is host-side state. Its ``action`` objects are retained for
        a future action dispatcher; this transport method only applies the
        selected page's LCD artwork and optional brightness. Both the compact
        legacy ``keys`` shape and the explicit ``scenes``/``pages`` shape are
        accepted.
        """

        base = Path(root) if root is not None else Path.cwd()
        device = profile.get("device", {})
        scenes = profile.get("scenes")
        selected = profile
        if scenes:
            scene_name = scene or next(iter(scenes))
            try:
                selected = scenes[scene_name]
            except KeyError as exc:
                raise ValueError(f"unknown scene: {scene_name}") from exc
            pages = selected.get("pages")
            if pages:
                page_name = page or selected.get("active_page") or next(iter(pages))
                try:
                    selected = pages[page_name]
                except KeyError as exc:
                    raise ValueError(f"unknown page in scene {scene_name}: {page_name}") from exc

        brightness = selected.get("brightness", device.get("brightness"))
        # Pages are complete layouts. Clear stale artwork from the previous
        # page before writing the selected page's images.
        self.clear_screen()
        if brightness is not None:
            self.set_brightness(int(brightness))

        keys = selected.get("keys", selected)
        sent = 0
        for raw_key, definition in sorted(keys.items(), key=lambda item: int(item[0])):
            image = definition.get("image") if isinstance(definition, dict) else None
            if image:
                self.set_key_image(int(raw_key), base / image)
                sent += 1
        return sent

    @staticmethod
    def decode_key_report(report: bytes) -> tuple[int, bool] | None:
        """Decode the compatible key report, if *report* represents one.

        Windows returns the report-ID byte; hidapi-style callers often omit
        it. The protocol's key ID/state positions are therefore data[9]/data[10].
        """

        data = report[1:] if len(report) == REPORT_SIZE else report
        if len(data) <= 10:
            return None
        # Both key events and write acknowledgements use ACK/OK framing. The
        # vendor-compatible protocol reserves hardware ID 0xFF for a write
        # confirmation; IDs 1-15 are physical key events.
        if data[:3] != b"ACK" or data[5:7] != b"OK":
            return None
        key_id, state = data[9], data[10]
        if key_id == 0xFF:
            return None
        if key_id not in DEVICE_ID_TO_KEY or state not in (0, 1):
            return None
        return DEVICE_ID_TO_KEY[key_id], bool(state)

    def listen(self, seconds: float = 30.0) -> Iterable[tuple[float, bytes, tuple[int, bool] | None]]:
        """Yield timestamp, raw report, and decoded key event for each report."""

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            report = self.read_report(min(1000, max(1, int((deadline - time.monotonic()) * 1000))))
            if report:
                yield time.time(), report, self.decode_key_report(report)


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Direct FIFINE D6 controller")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info")
    sub.add_parser("transport-info")
    sub.add_parser("heartbeat")
    brightness = sub.add_parser("brightness")
    brightness.add_argument("value", type=int)
    qucmd = sub.add_parser("qucmd")
    qucmd.add_argument("values", type=lambda value: int(value, 0), nargs=5)
    image = sub.add_parser("image")
    image.add_argument("key", type=int)
    image.add_argument("path", type=Path)
    clear = sub.add_parser("clear-key")
    clear.add_argument("key", type=int)
    scene = sub.add_parser("scene")
    scene.add_argument("profile", type=Path)
    scene.add_argument("--scene")
    scene.add_argument("--page")
    listen = sub.add_parser("listen")
    listen.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()

    if args.command == "info":
        for path in find_device_paths():
            print(path)
        print(f"capabilities={D6Controller.capabilities()}")
        return 0
    if args.command == "transport-info":
        print(f"hid_paths={find_device_paths()}")
        print(f"usb_paths={find_usb_device_paths()}")
        for path in find_usb_device_paths():
            try:
                with D6Controller(path, transport="winusb") as controller:
                    print(
                        f"winusb=available path={path} in=0x{controller.input_pipe:02x} "
                        f"out=0x{controller.output_pipe:02x}"
                    )
            except Exception as exc:
                print(f"winusb=unavailable path={path} error={exc}")
        return 0
    with D6Controller(transport=os.environ.get("D6_TRANSPORT", "hid")) as controller:
        if args.command == "heartbeat":
            controller.heartbeat()
        elif args.command == "brightness":
            controller.set_brightness(args.value)
        elif args.command == "qucmd":
            controller.send_qucmd(*args.values)
        elif args.command == "image":
            controller.set_key_image(args.key, args.path)
        elif args.command == "clear-key":
            controller.clear_key_image(args.key)
        elif args.command == "scene":
            with args.profile.open("r", encoding="utf-8") as stream:
                profile = json.load(stream)
            count = controller.apply_profile(
                profile,
                scene=args.scene,
                page=args.page,
                root=args.profile.parent,
            )
            print(f"applied {count} key image(s)")
        elif args.command == "listen":
            for timestamp, report, event in controller.listen(args.seconds):
                print(f"{timestamp:.3f} report={report[:32].hex()} event={event}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
