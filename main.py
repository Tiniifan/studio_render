import argparse
import math
import os
import sys


def _console_log():
    """The built studio_render.exe has no console (sys.stdout and sys.stderr are None): what it prints, its Python
    errors and faulthandler go to studio_render.log next to the exe."""
    if not getattr(sys, "frozen", False) or (sys.stdout is not None and sys.stderr is not None):
        return
    try:
        stream = open(os.path.join(os.path.dirname(sys.executable), "studio_render.log"), "w", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w")
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def parse_size(text):
    width, height = text.lower().split("x")
    return int(width), int(height)


def build_scene(args):
    import eleven
    from project import Project, ProjectError, apply_command_line

    project = Project(args.engine or "IE4")
    project.scene.install_missing = True
    for item in args.files:
        path, _, engine = item.partition("@")
        if engine:
            eleven.ensure_game(engine, True)
        with open(path, "rb") as stream:
            project.add(os.path.basename(path), stream.read(), engine=engine or None)
    try:
        apply_command_line(project, args.object, args.animate)
    except ProjectError as error:
        raise SystemExit(str(error))
    return project.scene


def screenshot(args):
    import moderngl
    import numpy as np
    from PIL import Image

    from renderer import Renderer
    from scene import View

    import eleven
    try:
        scene = build_scene(args)
    except eleven.EngineUnavailable as error:
        raise SystemExit(str(error))
    width, height = parse_size(args.size)
    ctx = moderngl.create_standalone_context()
    fbo = ctx.framebuffer(color_attachments=[ctx.renderbuffer((width, height), 4, samples=0)],
                          depth_attachment=ctx.depth_renderbuffer((width, height)))
    renderer = Renderer(ctx)
    renderer.draw_outlines = not args.no_outlines

    view = View()
    view.width, view.height = width, height
    view.frame_bounds(*scene.bounds())
    view.yaw = math.radians(args.yaw)
    view.pitch = math.radians(args.pitch)
    view.zoom(args.zoom)

    frames = [int(f) for f in args.frame.split(",")]
    for frame in frames:
        camera = scene.camera_at(frame) if args.game_camera else None
        if camera is not None:
            view.update_game(*camera, far=scene.game_camera_far)
        else:
            view.update_orbit()
        renderer.render(fbo, scene, view, float(frame))
        pixels = np.frombuffer(fbo.read(components=3), dtype=np.uint8).reshape(height, width, 3)[::-1]
        path = args.screenshot
        if len(frames) > 1:
            stem, extension = os.path.splitext(path)
            path = f"{stem}_{frame:03d}{extension}"
        Image.fromarray(pixels).save(path)
        print(path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="main.py", description="Standalone OpenGL viewer of Level-5 3DS files.")
    parser.add_argument("files", nargs="*", help="archives to open (.xc .xv .xk .pck), 'path@ENGINE' forces the engine of a file")
    parser.add_argument("--engine", help="game engine of the render data (IE4, IE5, IE6, IE4+IE5, YW1, ...); IE4 by default "
                        "on the command line, the preferred engine (Options > Default engine) in the window")
    parser.add_argument("--object", action="append", default=[], metavar="NAME=A,B,C",
                        help="group archives into a custom object, e.g. cp0008=cp0008a.xc,uaa0219.xc,ssa0219.xc")
    parser.add_argument("--animate", action="append", default=[], metavar="TARGET=ANIMATION",
                        help="animate a custom object or an archive with an armature, e.g. cp0008=whs0001_aa1.xc")
    parser.add_argument("--screenshot", metavar="PNG", help="render without a window and save the image")
    parser.add_argument("--frame", default="0", help="frame(s) of the screenshot, comma separated")
    parser.add_argument("--size", default="800x480", help="size of the screenshot")
    parser.add_argument("--game-camera", action="store_true", help="use the cameras of the archives")
    parser.add_argument("--yaw", type=float, default=0.0, help="orbit angle in degrees")
    parser.add_argument("--pitch", type=float, default=8.0, help="orbit elevation in degrees")
    parser.add_argument("--zoom", type=float, default=0.0, help="zoom steps (positive is closer)")
    parser.add_argument("--no-outlines", action="store_true")
    args = parser.parse_args(argv)

    if args.screenshot:
        screenshot(args)
        return 0

    from viewer import run
    return run(args)


if __name__ == "__main__":
    # The archives are read in worker processes (model.preload), the built exe starts them as copies of itself
    import multiprocessing
    multiprocessing.freeze_support()
    _console_log()
    sys.exit(main())
