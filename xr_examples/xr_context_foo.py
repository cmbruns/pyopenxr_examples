"""
Prototype for future high level api constructs in pyopenxr.
"""

import abc
import ctypes
from typing import Generator

from OpenGL import GL

import xr.api2


class FrameManager(object):
    """
    Context manager for a single OpenXR frame:
      calls xr.end_frame() even when exceptions occur
    """
    def __init__(self, session_manager: "SessionManager") -> None:
        self.session_manager: "SessionManager" = session_manager
        self.frame_state: xr.FrameState = xr.FrameState()
        self.render_layers = []

    def __enter__(self) -> "FrameManager":
        """
        Begin context manager by calling xr.wait_frame() and xr.begin_frame()
        """
        session = self.session_manager.session
        self.frame_state = xr.wait_frame(session)
        if not self.session_manager.is_headless:
            xr.begin_frame(session)
            self.render_layers.clear()
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        """
        Finish context manager by calling xr.end_frame(), even if an exception occurred
        """
        if not self.session_manager.is_headless:
            xr.end_frame(
                session=self.session_manager.session,
                frame_end_info=xr.FrameEndInfo(
                    display_time=self.frame_state.predicted_display_time,
                    environment_blend_mode=xr.EnvironmentBlendMode.OPAQUE,
                    layers=self.render_layers,
                ),
            )

    def views(self) -> Generator[xr.View, None, None]:
        """
        Generates the views for the left and right eyes or whatever for use
        during rendering.
        """
        if self.session_manager.is_headless:
            return
        swapchains = self.session_manager.swapchains
        if swapchains is None:
            return
        for view in swapchains.views(self.frame_state, self.render_layers):
            yield view

    @property
    def session_state(self) -> xr.SessionState:
        return self.session_manager.session_state


class SessionManager(xr.api2.ISubscriber):
    """
    Context manager for the OpenXR session life cycle.
      Cleanly winds down the end of the session life cycle when exceptions occur,
      including StopIteration when the client calls break on the frame loop,
      and KeyboardInterrupt when Ctrl-C is pressed or when run is stopped in
      pycharm IDE.
    """
    def __init__(
            self,
            instance: xr.Instance,
            system_id: xr.SystemId,
            session: xr.Session,
            begin_info: xr.SessionBeginInfo = None,
            is_headless=False,
            swapchains=None,
    ) -> None:
        self.is_headless = is_headless
        self.instance = instance
        self.session = session
        if begin_info is None:
            view_configurations = xr.enumerate_view_configurations(instance=instance, system_id=system_id)
            view_configuration_type = xr.ViewConfigurationType.PRIMARY_STEREO
            if view_configuration_type not in view_configurations:
                view_configuration_type = view_configurations[0]
            begin_info = xr.SessionBeginInfo(
                primary_view_configuration_type=view_configuration_type,
            )
        self._begin_info = begin_info
        self.event_bus = xr.api2.EventBus()  # TODO: this event bus should be shared
        self.is_running = False
        self.exit_frame_loop = False
        self.session_state = xr.SessionState.UNKNOWN
        self.swapchains = swapchains
        self.event_bus.subscribe(
            event_key=xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED,
            subscriber=self,
        )

    def __enter__(self) -> "SessionManager":
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        """
        Wind down the session lifecycle when exiting.
        """
        try:
            # Indicate our intention to exit the session
            xr.request_exit_session(self.session)
        except xr.exception.SessionNotRunningError:
            pass  # Maybe someone already requested exit, or the session never started
        # In any case, run several iterations of the frame loop to wind things down
        for _ in range(20):
            self._poll_events()
            if self.exit_frame_loop:
                break
            if self.is_running:
                with FrameManager(self):
                    # No yielding here. Just do nothing and move on to the next frame.
                    # We are trying to wind down as quickly as possible.
                    time.sleep(0.050)  # quickly let other things happen if necessary.

    def frames(self) -> Generator[FrameManager, None, None]:
        """
        Generate frames of the OpenXR frame loop
        """
        while True:
            self._poll_events()
            if self.exit_frame_loop:
                break
            elif self.session_state == xr.SessionState.IDLE:
                time.sleep(0.200)  # minimize resource consumption while idle
            elif self.is_running:
                with FrameManager(self) as frame:
                    yield frame

    def handle_event(self, event_key, event_data) -> None:
        """
        Respond to OpenXR session state change events
        """
        if event_key != xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED:
            return  # Unexpected...
        event = ctypes.cast(
            ctypes.byref(event_data),
            ctypes.POINTER(xr.EventDataSessionStateChanged)).contents
        self.session_state = xr.SessionState(event.state)
        print(self.session_state.name)  # TODO: remove this debugging statement
        if self.session_state == xr.SessionState.READY:
            xr.begin_session(
                session=self.session,
                begin_info=self._begin_info,
            )
            self.is_running = True
        elif self.session_state == xr.SessionState.STOPPING:
            self.is_running = False
            xr.end_session(self.session)
        elif self.session_state == xr.SessionState.EXITING:
            self.exit_frame_loop = True

    def _poll_events(self) -> None:
        """
        Post all OpenXR events to the event bus.
        including events this particular class might not be interested in.
        So developers should use the same event bus to listen for other
        OpenXR event types if needed.
        """
        while True:
            try:
                event_buffer = xr.poll_event(self.instance)
                event_type = xr.StructureType(event_buffer.type)
                self.event_bus.post(event_key=event_type, event_data=event_buffer)
            except xr.EventUnavailable:
                break


class XrContext(object):
    """
    High level api2 pyopenxr class used to automatically initialize the
    session, state, system, etc., and run the frame loop.
    """
    def __init__(self, renderers=(), _actioners=(), graphics_extension=None):
        # TODO: take arguments for optional create_info objects
        # Choose instance extensions
        self.renderers = renderers
        available_extensions = xr.enumerate_instance_extension_properties()
        if graphics_extension is None:
            if len(renderers) == 0:
                # If there are no renderers, headless is POSSIBLE
                if xr.MND_HEADLESS_EXTENSION_NAME in available_extensions:
                    graphics_extension = xr.MND_HEADLESS_EXTENSION_NAME
        if graphics_extension is None:
            if xr.KHR_OPENGL_ENABLE_EXTENSION_NAME in available_extensions:
                graphics_extension = xr.KHR_OPENGL_ENABLE_EXTENSION_NAME
        assert graphics_extension is not None
        self.is_headless = graphics_extension == xr.MND_HEADLESS_EXTENSION_NAME
        extensions = set()
        extensions.add(graphics_extension)
        self.instance_create_info = xr.InstanceCreateInfo(
            enabled_extension_names=extensions,
        )
        self.instance = None
        self.system_id = None
        self.session = None
        self.graphics_context = None

    def frames(self) -> Generator[FrameManager, None, None]:
        """
        The OpenXR frame loop.
        """
        with xr.Instance(self.instance_create_info) as self.instance:
            self.system_id = xr.get_system(self.instance)
            if self.is_headless:
                for frame in self._headless_session_frames():
                    yield frame
            else:
                for frame in self._opengl_session_frames():
                    yield frame

    def _headless_session_frames(self) -> Generator[FrameManager, None, None]:
        """
        One fragment of the context manager chain; separated for easier dynamic composition.
        """
        with xr.Session(
                instance=self.instance,
                create_info=xr.SessionCreateInfo(
                    system_id=self.system_id,
                    next=None,
                ),
        ) as self.session:
            for frame in self._session_manager_frames():
                yield frame

    def _opengl_session_frames(self) -> Generator[FrameManager, None, None]:
        """
        One fragment of the context manager chain; separated for easier dynamic composition.
        """
        with xr.api2.GLFWContext(
                instance=self.instance,
                system_id=self.system_id,
        ) as self.graphics_context:
            with xr.Session(
                    instance=self.instance,
                    create_info=xr.SessionCreateInfo(
                        system_id=self.system_id,
                        next=self.graphics_context.graphics_binding_pointer,
                    ),
            ) as self.session:
                with xr.api2.XrSwapchains(
                        instance=self.instance,
                        system_id=self.system_id,
                        session=self.session,
                        context=self.graphics_context,
                        view_configuration_type=xr.ViewConfigurationType.PRIMARY_STEREO,
                ) as self.swapchains:
                    for frame in self._session_manager_frames():
                        yield frame

    def _session_manager_frames(self) -> Generator[FrameManager, None, None]:
        """
        One fragment of the context manager chain; separated for easier dynamic composition.
        """
        with SessionManager(
            instance=self.instance,
            system_id=self.system_id,
            session=self.session,
            is_headless=self.is_headless,
            swapchains=self.swapchains,
        ) as session_manager:
            for frame in session_manager.frames():
                yield frame

    def render(self):
        for renderer in self.renderers:
            renderer.render_scene()


class IRendererGL(abc.ABC):
    @abc.abstractmethod
    def render_scene(self):
        pass


class PinkWorld(IRendererGL):
    def render_scene(self):
        GL.glClearColor(1, 0.7, 0.7, 1)  # pink
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)


def main():
    """
    Things to test:
      * break in client but cleanly wind down session state
      * KeyboardInterrupt but cleanly wind down session state
      * close window and cleanly wind down session state
    """
    context = XrContext(
        graphics_extension=xr.KHR_OPENGL_ENABLE_EXTENSION_NAME,
        renderers=[PinkWorld()],
    )
    for frame_index, frame in enumerate(context.frames()):
        if frame_index > 500:
            break
        if frame.session_state == xr.SessionState.FOCUSED:
            pass  # TODO controllers
        if frame.frame_state.should_render:
            for view in frame.views():
                context.render()


if __name__ == "__main__":
    main()
