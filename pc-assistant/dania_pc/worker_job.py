"""Windows job object: kill this worker and descendants if the companion exits."""
import ctypes
from ctypes import wintypes


class WorkerJob:
    def __init__(self, pid):
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype=wintypes.HANDLE
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        class BASIC(ctypes.Structure):
            _fields_=[('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('LimitFlags',wintypes.DWORD),
                ('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
                ('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),
                ('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(name,ctypes.c_uint64) for name in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
        class EXTENDED(ctypes.Structure):
            _fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),
                ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        self.kernel=kernel;self.handle=kernel.CreateJobObjectW(None,None)
        if not self.handle: raise OSError('Cannot create worker job')
        process=None
        try:
            limits=EXTENDED();limits.BasicLimitInformation.LimitFlags=0x2000 # KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
                raise OSError('Cannot configure worker job')
            process=kernel.OpenProcess(0x0100|0x0001,False,pid) # SET_QUOTA | TERMINATE
            if not process or not kernel.AssignProcessToJobObject(self.handle,process):
                raise OSError('Cannot assign worker job')
        except Exception:
            self.close();raise
        finally:
            if process: kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle);self.handle=None
