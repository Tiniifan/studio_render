"""H.264 .mp4 writer on the encoder of Windows (Media Foundation), called with ctypes.

Windows 10/11 ship the H.264 encoder and the MPEG-4 muxer: the build does not carry ffmpeg (about 100 MB). Elsewhere
(or on an N edition of Windows without the Media Feature Pack) the writer falls back on imageio + imageio-ffmpeg when
they are installed.

    with Mp4Writer(path, width, height, fps) as writer:
        writer.append(rgb)   # (height, width, 3) uint8, top row first
"""
import ctypes
import sys
import uuid

import numpy as np


class VideoError(RuntimeError):
    pass


def _guid(text):
    return (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(text).bytes_le)


MF_MT_MAJOR_TYPE = "48eba18e-f8c9-4687-bf11-0a74c9f96a8f"
MF_MT_SUBTYPE = "f7e34c9a-42e8-4714-b74b-cb29d72c35e5"
MF_MT_AVG_BITRATE = "20332624-fb0d-4d9e-bd0d-cbf6786c102e"
MF_MT_INTERLACE_MODE = "e2724bb8-e676-4806-b4b2-a8d6efb44ccd"
MF_MT_FRAME_SIZE = "1652c33d-d6b2-4012-b834-72030849a37d"
MF_MT_FRAME_RATE = "c459a2e8-3d2c-4e44-b132-fee5156c7bb0"
MF_MT_PIXEL_ASPECT_RATIO = "c6376a1e-8d0a-4027-be45-6d9a0ad39bb6"
MF_MT_DEFAULT_STRIDE = "644b4e48-1e02-4516-b0eb-c01ca9d49ac6"
MF_MT_MPEG2_PROFILE = "ad76a80b-2d5c-4e0b-b375-64e520137036"
MF_SINK_WRITER_DISABLE_THROTTLING = "08b845d8-2b74-4afe-9d53-be16d2d5ae4f"
MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS = "a634a91c-822b-41b9-a494-4de4643612b0"
MFMediaType_Video = "73646976-0000-0010-8000-00aa00389b71"
MFVideoFormat_H264 = "34363248-0000-0010-8000-00aa00389b71"
MFVideoFormat_RGB32 = "00000016-0000-0010-8000-00aa00389b71"

MF_VERSION = 0x00020070
MFVideoInterlace_Progressive = 2
eAVEncH264VProfile_High = 100

# vtable slots (IUnknown 0-2, IMFAttributes 3-32, IMFSample 33-46, IMFMediaBuffer 3-7, IMFSinkWriter 3-13)
RELEASE = 2
SET_UINT32, SET_UINT64, SET_GUID = 21, 22, 24
SET_SAMPLE_TIME, SET_SAMPLE_DURATION, ADD_BUFFER = 36, 38, 42
LOCK, UNLOCK, SET_CURRENT_LENGTH = 3, 4, 6
ADD_STREAM, SET_INPUT_MEDIA_TYPE, BEGIN_WRITING, WRITE_SAMPLE, FINALIZE = 3, 4, 5, 6, 11


def _call(pointer, slot, *arguments):
    """Calls the method of the COM object at the slot of its vtable; raises VideoError on a failed HRESULT."""
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    # the arguments are ctypes objects (values, arrays, pointers): their types are the prototype
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *(type(a) for a in arguments))
    result = prototype(vtable[slot])(pointer, *arguments)
    if result < 0:
        raise VideoError(f"Media Foundation error 0x{result & 0xFFFFFFFF:08X}")
    return result


def _release(pointer):
    if pointer:
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[RELEASE])(pointer)


class _MediaFoundationWriter:
    def __init__(self, path, width, height, fps, bitrate):
        try:
            self.mf = ctypes.WinDLL("mfplat")
            self.mfreadwrite = ctypes.WinDLL("mfreadwrite")
        except OSError as error:
            raise VideoError(f"Media Foundation is not available: {error}") from error
        ole32 = ctypes.WinDLL("ole32")
        ole32.CoInitializeEx(None, 2)  # the thread of Qt is already initialised: S_FALSE / RPC_E_CHANGED_MODE are fine
        self._check(self.mf.MFStartup(MF_VERSION, 0))
        self.width, self.height, self.fps = width, height, fps
        self.writer = ctypes.c_void_p()
        self.frames = 0
        self.stream = ctypes.c_ulong()
        attributes, output, source = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        try:
            self._check(self.mf.MFCreateAttributes(ctypes.byref(attributes), 2))
            self._uint32(attributes, MF_SINK_WRITER_DISABLE_THROTTLING, 1)
            self._uint32(attributes, MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS, 1)
            self._check(self.mfreadwrite.MFCreateSinkWriterFromURL(ctypes.c_wchar_p(path), None, attributes,
                                                                   ctypes.byref(self.writer)))

            self._check(self.mf.MFCreateMediaType(ctypes.byref(output)))
            self._video_type(output, MFVideoFormat_H264)
            self._uint32(output, MF_MT_AVG_BITRATE, bitrate)
            self._uint32(output, MF_MT_MPEG2_PROFILE, eAVEncH264VProfile_High)
            _call(self.writer, ADD_STREAM, output, ctypes.pointer(self.stream))

            self._check(self.mf.MFCreateMediaType(ctypes.byref(source)))
            self._video_type(source, MFVideoFormat_RGB32)
            self._uint32(source, MF_MT_DEFAULT_STRIDE, width * 4)  # positive: top row first
            _call(self.writer, SET_INPUT_MEDIA_TYPE, self.stream, source, ctypes.c_void_p())
            _call(self.writer, BEGIN_WRITING)
        except Exception:
            self._shutdown()
            raise
        finally:
            for pointer in (attributes, output, source):
                _release(pointer)

    @staticmethod
    def _check(result):
        if result < 0:
            raise VideoError(f"Media Foundation error 0x{result & 0xFFFFFFFF:08X}")

    @staticmethod
    def _uint32(target, key, value):
        _call(target, SET_UINT32, _guid(key), ctypes.c_uint32(value))

    @staticmethod
    def _uint64(target, key, high, low):
        _call(target, SET_UINT64, _guid(key), ctypes.c_uint64((high << 32) | low))

    def _video_type(self, media_type, subtype):
        _call(media_type, SET_GUID, _guid(MF_MT_MAJOR_TYPE), _guid(MFMediaType_Video))
        _call(media_type, SET_GUID, _guid(MF_MT_SUBTYPE), _guid(subtype))
        self._uint32(media_type, MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive)
        self._uint64(media_type, MF_MT_FRAME_SIZE, self.width, self.height)
        self._uint64(media_type, MF_MT_FRAME_RATE, self.fps, 1)
        self._uint64(media_type, MF_MT_PIXEL_ASPECT_RATIO, 1, 1)

    def append(self, rgb):
        bgrx = np.empty((self.height, self.width, 4), dtype=np.uint8)
        bgrx[..., 0], bgrx[..., 1], bgrx[..., 2], bgrx[..., 3] = rgb[..., 2], rgb[..., 1], rgb[..., 0], 255
        size = bgrx.nbytes
        buffer, sample = ctypes.c_void_p(), ctypes.c_void_p()
        try:
            self._check(self.mf.MFCreateMemoryBuffer(ctypes.c_ulong(size), ctypes.byref(buffer)))
            data = ctypes.c_void_p()
            _call(buffer, LOCK, ctypes.pointer(data), ctypes.c_void_p(), ctypes.c_void_p())
            ctypes.memmove(data, bgrx.ctypes.data, size)
            _call(buffer, UNLOCK)
            _call(buffer, SET_CURRENT_LENGTH, ctypes.c_ulong(size))
            self._check(self.mf.MFCreateSample(ctypes.byref(sample)))
            _call(sample, ADD_BUFFER, buffer)
            start = self.frames * 10_000_000 // self.fps  # 100 ns units
            end = (self.frames + 1) * 10_000_000 // self.fps
            _call(sample, SET_SAMPLE_TIME, ctypes.c_longlong(start))
            _call(sample, SET_SAMPLE_DURATION, ctypes.c_longlong(end - start))
            _call(self.writer, WRITE_SAMPLE, self.stream, sample)
            self.frames += 1
        finally:
            _release(sample)
            _release(buffer)

    def close(self):
        try:
            if self.writer and self.frames:
                _call(self.writer, FINALIZE)
        finally:
            self._shutdown()

    def _shutdown(self):
        if self.writer:
            _release(self.writer)
            self.writer = ctypes.c_void_p()
            self.mf.MFShutdown()


class _ImageioWriter:
    def __init__(self, path, width, height, fps, bitrate):
        try:
            import imageio
        except ImportError as error:
            raise VideoError("The .mp4 export needs imageio and imageio-ffmpeg here (pip install imageio "
                             "imageio-ffmpeg).") from error
        self.writer = imageio.get_writer(path, format="FFMPEG", fps=fps, codec="libx264", quality=8,
                                         pixelformat="yuv420p", macro_block_size=16)

    def append(self, rgb):
        self.writer.append_data(rgb)

    def close(self):
        self.writer.close()


class Mp4Writer:
    def __init__(self, path, width, height, fps, bitrate=None):
        if width % 2 or height % 2:
            raise VideoError("The size of the video must be even.")
        bitrate = bitrate or max(2_000_000, int(width * height * fps * 0.2))
        self._writer = None
        if sys.platform != "win32":
            self._writer = _ImageioWriter(path, width, height, fps, bitrate)
            return
        try:
            self._writer = _MediaFoundationWriter(path, width, height, fps, bitrate)
        except VideoError as error:
            try:
                self._writer = _ImageioWriter(path, width, height, fps, bitrate)
            except VideoError:
                raise VideoError(f"{error}. On an N edition of Windows, install the Media Feature Pack.") from error

    def append(self, rgb):
        self._writer.append(np.ascontiguousarray(rgb, dtype=np.uint8))

    def close(self):
        if self._writer is not None:
            writer, self._writer = self._writer, None
            writer.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
