"""

"""

import argparse
import logging
import platform
import sys
import threading
import time

import xr

from xr_examples.hello_xr.graphics_plugin import IGraphicsPlugin
from xr_examples.hello_xr.platform_plugin import IPlatformPlugin
from xr_examples.hello_xr.openxr_program import OpenXRProgram
from xr_examples.hello_xr.graphics_plugin_opengl import OpenGLGraphicsPlugin
from xr_examples.hello_xr.platform_plugin_win32 import Win32PlatformPlugin
from xr_examples.hello_xr.platform_plugin_xlib import XlibPlatformPlugin

key_press_event = threading.Event()
logger = logging.getLogger("hello_xr.main")


def create_graphics_plugin(options: argparse.Namespace) -> IGraphicsPlugin:
    """Create a graphics plugin for the graphics API specified in the options."""
    graphics_plugin_map = {
        "OpenGL": OpenGLGraphicsPlugin,
    }
    if options.graphics not in graphics_plugin_map:
        raise NotImplementedError
    return graphics_plugin_map[options.graphics]()


def create_platform_plugin(_options: argparse.Namespace) -> IPlatformPlugin:
    if platform.system() == "Windows":
        return Win32PlatformPlugin()
    elif platform.system() == "Linux":
        return XlibPlatformPlugin()
    raise NotImplementedError


def poll_keyboard():
    logger.info("Press any key to shutdown...")
    try:
        sys.stdin.read(1)
        key_press_event.set()
        logger.debug("A key was pressed")
    finally:
        pass


def main():
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d %(module)s %(levelname)s: %(message)s",
        datefmt='%m/%d/%y %I:%M:%S',
        level=logging.INFO,
    )
    options = update_options_from_command_line()
    if options.blendmode == "Opaque":
        background_clear_color = (0.184313729, 0.309803933, 0.309803933, 1.0)  # SlateGrey
    elif options.blendmode == "Additive":
        background_clear_color = (0, 0, 0, 1)  # Black
    elif options.blendmode == "AlphaBlend":
        background_clear_color = (0, 0, 0, 0)  # TransparentBlack
    else:
        raise NotImplementedError

    # Install keyboard handler to exit on keypress
    threading.Thread(target=poll_keyboard, daemon=True).start()

    request_restart = False
    while True:
        # Create platform-specific implementation.
        platform_plugin = create_platform_plugin(options)
        # Create graphics API implementation.
        with create_graphics_plugin(options) as graphics_plugin, \
             OpenXRProgram(options, platform_plugin, graphics_plugin) as program:
            graphics_plugin.set_background_clear_color(background_clear_color)
            program.create_instance()
            program.initialize_system()
            program.initialize_session()
            program.create_swapchains()
            while not key_press_event.is_set():
                # glfw notices when you click the close button
                exit_render_loop = graphics_plugin.poll_events()
                if exit_render_loop:
                    break
                exit_render_loop, request_restart = program.poll_events()
                if exit_render_loop:
                    break
                if program.session_running:
                    try:
                        program.poll_actions()
                    except xr.exception.SessionNotFocused:
                        # TODO: C++ code does not need this conditional. Why does python?
                        pass
                    program.render_frame()
                else:
                    # Throttle loop since xrWaitFrame won't be called.
                    time.sleep(0.250)
            if key_press_event.is_set() or not request_restart:
                break


def update_options_from_command_line() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--graphics", "-g", default="OpenGL",
        choices=["D3D11", "D3D12", "OpenGLES", "OpenGL", "Vulkan2", "Vulkan"],
    )
    parser.add_argument(
        "--formfactor", "-ff", default="Hmd",
        choices=["Hmd", "Handheld"],
    )
    parser.add_argument(
        "--viewconfig", "-vc", default="Stereo",
        choices=["Mono", "Stereo"],
    )
    parser.add_argument(
        "--blendmode", "-bm", default="Opaque",
        choices=["Opaque", "Additive", "AlphaBlend"],
    )
    parser.add_argument(
        "--space", "-s", default="Local",
        choices=["View", "Local", "Stage"],
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
    )
    options = parser.parse_args()
    if options.verbose:
        logging.getLogger("hello_xr").setLevel(logging.DEBUG)
    return options


if __name__ == "__main__":
    main()
