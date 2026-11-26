"""Renders of a technique without a window: studio_render_cli headless (python cli.py headless ...).

    headless --capabilities
    headless frames <project.srp> --frames-dir <folder> (--count N [--seed S] | --frames 0,12,40)
                    [--failure-branch] [--size 400x240] <file>...
    headless mp4    <project.srp> --mp4 <out.mp4> [--failure <out_failure.mp4>] <file>...

The project (a template such as HissatsuScene) is opened and never saved, the files (the .pck and the .nut of a
technique) are imported in their order with the rules of the template and its automatic binding, and the frames are
drawn with the game cameras. No window, no session, no engine install: an engine that is not installed is an error.

stdout carries only JSON lines, one per step, flushed at each line ({"step": "import" | "frames" | "frame" |
"progress" | "exported" | "error", ...}); the logs go to stderr. Exit codes: 0 done, 2 usage, 3 refused or failed,
4 engine not installed, 10 internal error.
"""
import argparse
import contextlib
import io
import json
import os
import random
import sys
import traceback

FORMAT_VERSION = 1
OPERATIONS = ("frames", "mp4")
DEFAULT_SIZE = "400x240"
DEFAULT_COUNT = 10
FPS = 30
MP4_SIZE = (800, 480)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_ERROR = 3
EXIT_ENGINE = 4
EXIT_INTERNAL = 10
EXIT_CODES = {"USAGE": EXIT_USAGE, "ENGINE_NOT_INSTALLED": EXIT_ENGINE, "ENGINE_MISSING": EXIT_ENGINE,
              "INTERNAL_ERROR": EXIT_INTERNAL}


class HeadlessError(Exception):
    """A refusal: {code, message} on the error line, its exit code from EXIT_CODES (3 by default)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise HeadlessError("USAGE", f"{self.prog}: {message}")


def spread_frames(last, count, rng):
    """count distinct frames of 0..last, one drawn at random in each of count equal slices (away from the slice
    edges): random, but never close together."""
    total = last + 1
    if total <= count:
        return list(range(total))
    frames = []
    for index in range(count):
        start, end = total * index / count, total * (index + 1) / count
        margin = (end - start) * 0.2
        frames.append(min(last, int(rng.uniform(start + margin, end - margin))))
    return sorted(set(frames))


def parse_frames(text):
    """[int] of '0,12,40'."""
    try:
        return [int(part) for part in text.split(",") if part.strip()]
    except ValueError:
        raise HeadlessError("USAGE", f"--frames takes frame numbers separated by commas, not {text!r}.")


def parse_size(text):
    """(width, height) of '400x240'."""
    try:
        width, height = (int(value) for value in text.lower().split("x"))
    except ValueError:
        raise HeadlessError("USAGE", f"--size is WIDTHxHEIGHT (400x240), not {text!r}.")
    if not (1 <= width <= 4096 and 1 <= height <= 4096):
        raise HeadlessError("USAGE", "The size goes from 1 to 4096 pixels.")
    return width, height


def build_parser(prog="studio_render_cli headless"):
    parser = _Parser(prog=prog, description="Renders of a technique without a window, as JSON lines on stdout.")
    parser.add_argument("--capabilities", action="store_true",
                        help="print what this studio_render can render without a window (one JSON line) and exit")
    commands = parser.add_subparsers(dest="operation", metavar="OPERATION", parser_class=_Parser)

    frames = commands.add_parser("frames", help="PNG images of frames of the technique, drawn with the game cameras")
    frames.add_argument("project", help="the project (.srp) the files are imported into; never saved")
    frames.add_argument("--frames-dir", required=True, help="folder of the images (frame_012.png...), made if missing")
    choice = frames.add_mutually_exclusive_group()
    choice.add_argument("--count", type=int, help=f"that many frames spread over the technique ({DEFAULT_COUNT} "
                                                  "by default)")
    choice.add_argument("--frames", help="these frames, comma separated (0,12,40)")
    frames.add_argument("--seed", type=int, help="seed of the random choice of --count")
    frames.add_argument("--failure-branch", action="store_true", help="the failure branch of the script "
                                                                      "(the success one by default)")
    frames.add_argument("--size", default=DEFAULT_SIZE, help=f"size of the images ({DEFAULT_SIZE} by default)")
    frames.add_argument("files", nargs="+", help="the files to import, in this order (the .pck, the .nut)")

    mp4 = commands.add_parser("mp4", help="the technique as an .mp4 (30 fps), drawn with the game cameras")
    mp4.add_argument("project", help="the project (.srp) the files are imported into; never saved")
    mp4.add_argument("--mp4", required=True, help="the video of the success branch (replaced when it exists)")
    mp4.add_argument("--failure", help="the video of the failure branch, written after the first one")
    mp4.add_argument("files", nargs="+", help="the files to import, in this order (the .pck, the .nut)")
    return parser


def capabilities():
    from version import VERSION
    return {"headless": FORMAT_VERSION, "operations": list(OPERATIONS), "version": VERSION}


class Emitter:
    """Writes the JSON lines on a stream, flushed at each line."""

    def __init__(self, stream):
        self.stream = stream

    def __call__(self, step, **data):
        self.stream.write(json.dumps(dict(step=step, **data), ensure_ascii=False) + "\n")
        self.stream.flush()


@contextlib.contextmanager
def json_stdout():
    """The stream of the JSON lines (the real stdout). Meanwhile everything else written to stdout, by print() or by
    native code and worker processes on file descriptor 1, goes to stderr."""
    out = sys.stdout
    err = sys.stderr if sys.stderr is not None else open(os.devnull, "w")
    try:
        fd = out.fileno()
        err_fd = err.fileno()
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        fd = None
    if fd is None:
        sys.stdout = err
        try:
            yield out
        finally:
            sys.stdout = out
        return
    out.flush()
    copy = os.dup(fd)
    os.dup2(err_fd, fd)
    stream = open(copy, "w", encoding="utf-8", errors="replace", closefd=False)
    sys.stdout = err
    try:
        yield stream
    finally:
        sys.stdout = out
        try:
            stream.flush()
        finally:
            os.dup2(copy, fd)
            os.close(copy)


class Studio:
    """A project opened for headless renders: read from a .srp, never saved; drawn in an OpenGL context of its own."""

    def __init__(self, path):
        import eleven
        import project as project_module
        from preferences import Preferences
        from project import Project, ProjectError, ProjectVersionError

        name = os.path.basename(path)
        if not os.path.isfile(path):
            raise HeadlessError("FILE_NOT_FOUND", f"{path} does not exist.")
        try:
            engine_id = Project.read_json(path).get("engine") or "IE4"
        except ProjectError as error:
            raise HeadlessError("PROJECT_INVALID", str(error))
        state = eleven.engine_state(engine_id)
        if state == "missing":
            raise HeadlessError("ENGINE_MISSING", f"{name} is not opened: its engine {engine_id} is not installed "
                                                  "and its zip is missing.")
        if state != "ready":
            raise HeadlessError("ENGINE_NOT_INSTALLED", f"{name} is not opened: its engine {engine_id} is not "
                                                       "installed (install it from studio_render).")
        self.preferences = Preferences()
        self.read_archives(project_module.project_archives(path))
        try:
            self.project = Project.load(path)
        except ProjectVersionError as error:
            raise HeadlessError("PROJECT_VERSION", str(error))
        except eleven.EngineUnavailable as error:
            raise HeadlessError("ENGINE_NOT_INSTALLED", str(error))
        except Exception as error:
            traceback.print_exc()
            raise HeadlessError("OPEN_FAILED", f"{name} could not be opened: {error}")
        self.project.path = None    # never written back
        self.project.success = True
        self._offscreen = None

    @staticmethod
    def read_archives(archives):
        """Reads the archives that are not cached yet in the worker processes, as the window does."""
        import model as model_module
        pending = [(name, data) for name, data in archives if not model_module.is_cached(data, name)]
        if pending:
            model_module.preload(pending)

    def import_files(self, paths):
        """Imports the files as the window does (template rules, a file of a rule replaces the one there, automatic
        binding); returns {added, replaced, not_taken, refused, failed, accepted, template}."""
        import assets as assets_module
        import eleven
        import project as project_module

        project = self.project
        files = []
        for path in paths:
            try:
                with open(path, "rb") as stream:
                    files.append((os.path.basename(path), path, stream.read(), None))
            except OSError as error:
                files.append((os.path.basename(path), path, None, error))
        archives = []
        for name, _, data, _ in files:
            if data is None or assets_module.is_script(name) or not project.import_rule(name)[0]:
                continue
            try:
                archives += project_module.archives_to_read(name, data)
            except Exception:
                pass
        self.read_archives(archives)

        added, replaced, refused, failed, not_taken = [], [], [], [], []
        for name, path, data, read_error in files:
            allowed, rule = project.import_rule(name)
            if not allowed:
                not_taken.append(name)
                continue
            if assets_module.is_script(name) and not self.preferences.squirrel:
                refused.append(name)
                continue
            if read_error is not None:
                failed.append(f"{name}: {read_error}")
                continue
            if not name.lower().endswith(assets_module.IMPORT_EXTENSIONS) and not assets_module.is_archive(data):
                failed.append(f"{name}: not a Level-5 archive, an image or a Squirrel script")
                continue
            refusal = project.rule_refusal(rule, name, data)
            if refusal:
                not_taken.append(f"{name}: {refusal}")
                continue
            source_path = path if assets_module.is_script(name) else None
            try:
                holder = project.holder(rule) if rule is not None else None
                if holder is not None:
                    asset, _ = project.replace(holder, name, data, source_path=source_path)
                    replaced.append({"old": holder.name, "new": asset.name})
                    if asset.kind == assets_module.SQUIRREL:
                        project.check_script(asset)
                elif assets_module.is_script(name):
                    asset = project.add(name, data, source_path=source_path)
                    project.check_script(asset)
                    added.append(asset.path)
                else:
                    asset = project.add(name, data)
                    if asset.error:
                        failed.append(asset.error)
                    if asset.is_package:
                        project.auto_bind()
                    added.append(asset.path)
            except eleven.EngineUnavailable as error:
                raise HeadlessError("ENGINE_NOT_INSTALLED", str(error))
            except Exception as error:
                traceback.print_exc()
                failed.append(f"{name}: {error}")
        template = project.template
        project.scene.revision += 1
        return {
            "added": added,
            "replaced": replaced,
            "not_taken": not_taken,
            "refused": refused,
            "failed": failed,
            "accepted": template.accepted() if template is not None and not_taken else None,
            "template": template.name if template is not None else None,
        }

    @property
    def last_frame(self):
        return max(self.project.scene.frame_count, self.project.layers_end(), 0)

    def set_success(self, success):
        """The technique plays as a success or a failure (the _success of its script)."""
        self.project.success = bool(success)
        self.project.scene.revision += 1

    def offscreen(self):
        if self._offscreen is None:
            from offscreen import Offscreen
            try:
                self._offscreen = Offscreen()
            except Exception as error:
                traceback.print_exc()
                raise HeadlessError("RENDER_FAILED", f"No OpenGL 3.3 context without a window: {error}")
        return self._offscreen

    def _view(self, frame, width, height):
        """The view of a frame: the game camera, or the free camera of the window where no game camera plays."""
        from scene import View
        scene = self.project.scene
        view = View()
        view.width, view.height = width, height
        pose = scene.camera_at(frame)
        if pose is not None:
            view.update_game(*pose, far=scene.game_camera_far)
        else:
            if scene.cameras:
                view.set_pose(*scene.cameras[0].at(0))
            else:
                view.look_down(100.0)
            view.update_orbit()
        return view

    def render(self, frame, width, height):
        """(height, width, 3) uint8 pixels of a frame, top row first, with the pictures of the timeline."""
        frame = int(max(0, min(self.last_frame, frame)))
        try:
            return self.offscreen().render(self.project.scene, self._view(frame, width, height), frame, width, height,
                                           layers=self.project.layers_at(frame))
        except HeadlessError:
            raise
        except Exception as error:
            traceback.print_exc()
            raise HeadlessError("RENDER_FAILED", f"Frame {frame} could not be drawn: {error}")

    def export_mp4(self, path, progress=None):
        """Writes every frame to an H.264 .mp4 at 30 fps (replaced when it exists); progress(done, total) after each
        frame. A failed export leaves no file."""
        from video import Mp4Writer
        if not os.path.isdir(os.path.dirname(path)):
            raise HeadlessError("INVALID_ARGUMENT", f"The folder of {path} does not exist.")
        width, height = MP4_SIZE
        last = self.last_frame
        total = last + 1
        writer = None
        try:
            writer = Mp4Writer(path, width, height, FPS)
            for frame in range(total):
                writer.append(self.render(frame, width, height))
                if progress is not None:
                    progress(frame + 1, total)
            writer.close()
            writer = None
        except BaseException as error:
            if writer is not None:
                with contextlib.suppress(Exception):
                    writer.close()
            if os.path.exists(path):
                with contextlib.suppress(OSError):
                    os.remove(path)
            if isinstance(error, Exception) and not isinstance(error, HeadlessError):
                traceback.print_exc()
                raise HeadlessError("EXPORT_FAILED", f"The video could not be written: {error}")
            raise
        finally:
            if self._offscreen is not None:
                self._offscreen.release_scene()
        return {"path": path, "frames": total, "start": 0, "end": last, "width": width, "height": height,
                "size": os.path.getsize(path), "temporary": False}

    def close(self):
        if self._offscreen is not None:
            with contextlib.suppress(Exception):
                self._offscreen.release()
                self._offscreen.ctx.release()
            self._offscreen = None


def open_studio(args, emit):
    """The project with the files imported; HeadlessError when a file is not taken."""
    studio = Studio(args.project)
    result = studio.import_files(args.files)
    emit("import", result=result)
    missing = [str(name) for key in ("not_taken", "refused", "failed") for name in result.get(key) or []]
    if missing:
        studio.close()
        raise HeadlessError("IMPORT_REFUSED", "Files not taken by the project: " + ", ".join(missing))
    return studio


def render_frames(args, emit):
    from PIL import Image
    width, height = parse_size(args.size)
    wanted = parse_frames(args.frames) if args.frames is not None else None
    count = DEFAULT_COUNT if args.count is None else args.count
    if wanted is None and count < 1:
        raise HeadlessError("USAGE", "--count is at least 1.")
    studio = open_studio(args, emit)
    try:
        studio.set_success(not args.failure_branch)
        last = studio.last_frame
        if wanted is not None:
            frames = sorted({min(last, max(0, frame)) for frame in wanted})
        else:
            frames = spread_frames(last, count, random.Random(args.seed))
        emit("frames", frames=frames, last=last)
        try:
            os.makedirs(args.frames_dir, exist_ok=True)
        except OSError as error:
            raise HeadlessError("INVALID_ARGUMENT", f"{args.frames_dir} cannot be made: {error}")
        for index, frame in enumerate(frames):
            pixels = studio.render(frame, width, height)
            path = os.path.join(args.frames_dir, f"frame_{frame:03d}.png")
            try:
                Image.fromarray(pixels).save(path)
            except OSError as error:
                raise HeadlessError("WRITE_FAILED", f"{path} could not be written: {error}")
            emit("frame", index=index, frame=frame, path=path)
    finally:
        studio.close()


def export_videos(args, emit):
    outputs = [(True, args.mp4)] + ([(False, args.failure)] if args.failure else [])
    outputs = [(success, path if path.lower().endswith(".mp4") else path + ".mp4") for success, path in outputs]
    studio = open_studio(args, emit)
    try:
        for success, path in outputs:
            branch = "success" if success else "failure"
            studio.set_success(success)

            def progress(done, total, branch=branch):
                emit("progress", branch=branch, done=done, total=total)

            result = studio.export_mp4(path, progress)
            emit("exported", branch=branch, result=result)
    finally:
        studio.close()


def _absolute(args):
    """Relative paths are of the current folder."""
    for name in ("project", "frames_dir", "mp4", "failure"):
        if getattr(args, name, None):
            setattr(args, name, os.path.abspath(getattr(args, name)))
    args.files = [os.path.abspath(path) for path in getattr(args, "files", [])]


def run(argv, stream, prog="studio_render_cli headless"):
    """Runs the command, writes its JSON lines on stream; returns the exit code."""
    emit = Emitter(stream)
    try:
        args = build_parser(prog).parse_args(argv)
        if args.capabilities:
            stream.write(json.dumps(capabilities()) + "\n")
            stream.flush()
            return EXIT_OK
        if args.operation is None:
            raise HeadlessError("USAGE", f"{prog}: give --capabilities, frames or mp4 (--help for the options).")
        _absolute(args)
        if args.operation == "frames":
            render_frames(args, emit)
        else:
            export_videos(args, emit)
        return EXIT_OK
    except HeadlessError as error:
        emit("error", code=error.code, message=error.message)
        return EXIT_CODES.get(error.code, EXIT_ERROR)
    except SystemExit as error:     # --help
        return error.code if isinstance(error.code, int) else EXIT_OK
    except Exception as error:
        traceback.print_exc()
        emit("error", code="INTERNAL_ERROR", message=f"{type(error).__name__}: {error}")
        return EXIT_INTERNAL


def main(argv=None, prog="studio_render_cli headless"):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "-h" in argv or "--help" in argv:
        # The help is text for a person: on stdout, not a JSON line
        try:
            build_parser(prog).parse_args(argv)
        except SystemExit as error:
            return error.code if isinstance(error.code, int) else EXIT_OK
        except HeadlessError as error:
            sys.stderr.write(error.message + "\n")
            return EXIT_USAGE
    with json_stdout() as stream:
        return run(argv, stream, prog)
