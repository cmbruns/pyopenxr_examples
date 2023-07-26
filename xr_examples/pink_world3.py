"""
pyopenxr example program pink_world2.py
This example renders a solid pink field to each eye.
"""

import abc
import time
from OpenGL import GL
import xr.api2


class FrameManager(object):
    """
    Context manager for a single OpenXR frame, that calls xr.end_frame() even when exceptions occur.
    """
    def __init__(self, session: xr.Session, graphics_context, swapchains, index):
        self.session = session
        self.graphics_context = graphics_context
        self.swapchains = swapchains
        self.state = None
        self.render_layers = []
        self.index = index

    def __enter__(self):
        self.state = xr.wait_frame(self.session)
        xr.begin_frame(self.session)
        self.render_layers = []
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        xr.end_frame(
            session=self.session,
            frame_end_info=xr.FrameEndInfo(
                display_time=self.state.predicted_display_time,
                environment_blend_mode=xr.EnvironmentBlendMode.OPAQUE,
                layers=self.render_layers,
            ),
        )

    def views(self):
        for view in self.swapchains.views(self.state, self.render_layers):
            yield view


def frame_loop():
    with xr.Instance(create_info=xr.InstanceCreateInfo(
            enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME],
    )) as instance:
        system_id = xr.get_system(
            instance=instance,
            get_info=xr.SystemGetInfo(
                form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY,
            ),
        )
        with xr.api2.GLFWContext(
                instance=instance,
                system_id=system_id,
        ) as gl_context:
            with xr.Session(
                    instance=instance,
                    create_info=xr.SessionCreateInfo(
                        system_id=system_id,
                        next=gl_context.graphics_binding_pointer,
                    ),
            ) as session:
                with xr.api2.XrSwapchains(
                        instance=instance,
                        system_id=system_id,
                        session=session,
                        context=gl_context,
                        view_configuration_type=xr.ViewConfigurationType.PRIMARY_STEREO,
                ) as swapchains:
                    # Set up event handling to track session state
                    event_bus = xr.api2.EventBus()
                    xr_event_generator = xr.api2.XrEventGenerator(instance)
                    session_status = xr.api2.SessionStatus(
                        session=session,
                        event_source=event_bus,
                        begin_info=xr.SessionBeginInfo(
                            primary_view_configuration_type=xr.ViewConfigurationType.PRIMARY_STEREO,
                        ),
                    )
                    # Loop over the session frames
                    frame_index = 0
                    while True:
                        frame_index += 1
                        xr_event_generator.poll_events(destination=event_bus)
                        if session_status.exit_frame_loop:
                            break
                        elif session_status.state == xr.SessionState.IDLE:
                            time.sleep(0.200)  # minimize resource consumption while idle
                        elif session_status.is_running:
                            with FrameManager(
                                session=session,
                                graphics_context=gl_context,
                                swapchains=swapchains,
                                index=frame_index,
                            ) as frame_manager:
                                yield frame_manager


def render_loop(renderables=()):
    for frame in frame_loop():
        if frame.state.should_render:
            if len(renderables) > 0:
                for view in frame.views():
                    frame.graphics_context.make_current()
                    for renderable in renderables:
                        renderable.render(view)
                    yield frame, view


class IGLRenderable(abc.ABC):
    def render(self, view):
        pass


class PinkWorld(IGLRenderable):
    def render(self, _view):
        GL.glClearColor(1, 0.7, 0.7, 1)  # pink
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)


def main():
    renderable = PinkWorld()
    for frame_index, frame in enumerate(frame_loop()):
        if frame_index > 2000:
            xr.request_exit_session(frame.session)
        for view in frame.views():
            renderable.render(view)


if __name__ == "__main__":
    main()
