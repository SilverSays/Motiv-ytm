"""Los procesos hijos (``mpv``, ``yt-dlp``) mueren con la app.

En Windows, si la app termina de golpe (el Administrador de tareas, un fallo
de Python) sus hijos siguen vivos: ``mpv`` sonando sin ventana, o un
``yt-dlp`` que deja el ``.exe`` bloqueado e impide actualizarlo. Para evitarlo
cada hijo se mete en un *Job Object* con ``KILL_ON_JOB_CLOSE``: el sistema
cierra el job al morir el proceso de la app y mata con él a todo lo que tenga
dentro, también a los nietos que lancen después (``deno``).

La app no entra en el job: así lo que se abre desde ella con el sistema (la
carpeta de datos en el Explorador, el navegador) no se cierra al salir.

Fuera de Windows, o si el sistema lo rechaza, no hace nada: el cierre normal
ya termina a los hijos.
"""

from __future__ import annotations

import logging
import threading

from ytm_native.core import paths

log = logging.getLogger("ytmn.proc")

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100

_lock = threading.Lock()
_job = None          # HANDLE del job; se deja abierto mientras viva la app
_k32 = None
_unavailable = False


def _kernel32():
    global _k32
    if _k32 is not None:
        return _k32
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    )
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    k32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.OpenThread.restype = wintypes.HANDLE
    k32.CancelSynchronousIo.argtypes = (wintypes.HANDLE,)
    k32.CancelSynchronousIo.restype = wintypes.BOOL
    _k32 = k32
    return k32


def _create_job(k32):
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        err = ctypes.get_last_error()
        k32.CloseHandle(job)
        raise OSError(err, "SetInformationJobObject")
    return job


def adopt(pid: int) -> bool:
    """Ata el proceso ``pid`` a la vida de la app. Devuelve si se consiguió."""
    global _job, _unavailable
    if not paths.IS_WINDOWS or not pid or _unavailable:
        return False
    import ctypes

    with _lock:
        try:
            k32 = _kernel32()
            if _job is None:
                _job = _create_job(k32)
            handle = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid))
            if not handle:
                # El proceso ya terminó (o no hay permiso): nada que atar.
                log.debug("OpenProcess(%s) falló: %s", pid, ctypes.get_last_error())
                return False
            try:
                if not k32.AssignProcessToJobObject(_job, handle):
                    log.warning(
                        "no se pudo atar el proceso %s a la app (error %s)",
                        pid, ctypes.get_last_error(),
                    )
                    return False
            finally:
                k32.CloseHandle(handle)
            return True
        except (OSError, AttributeError):
            # Sin Job Objects en este sistema: se avisa una vez y se sigue sin ellos.
            log.warning("no hay Job Objects: los hijos no se atan a la app", exc_info=True)
            _unavailable = True
            return False


_THREAD_TERMINATE = 0x0001        # el permiso que pide CancelSynchronousIo


def cancel_blocking_read(thread: threading.Thread) -> bool:
    """Saca a ``thread`` de una lectura bloqueada en una tubería.

    Si un nieto (``deno``) heredó el extremo de escritura, la tubería no se
    cierra al salir ``yt-dlp`` y el hilo lector espera para siempre; mientras
    tanto ``close()`` sobre esa tubería también se bloquea, porque espera al
    lector. La lectura cancelada termina con ``OSError`` en ese hilo.
    Devuelve si el sistema aceptó la cancelación (fuera de Windows, nunca).
    """
    tid = getattr(thread, "native_id", None)
    if not paths.IS_WINDOWS or not tid or not thread.is_alive():
        return False
    import ctypes

    try:
        k32 = _kernel32()
        handle = k32.OpenThread(_THREAD_TERMINATE, False, int(tid))
        if not handle:
            return False
        try:
            # Falla con ERROR_NOT_FOUND si el hilo no estaba en una lectura.
            return bool(k32.CancelSynchronousIo(handle))
        finally:
            k32.CloseHandle(handle)
    except (OSError, AttributeError):
        log.debug("no se pudo cancelar la lectura del hilo %s", tid, exc_info=True)
        return False
