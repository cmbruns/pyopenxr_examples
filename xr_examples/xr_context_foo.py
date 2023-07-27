"""
Prototype for high level api constructs in pyopenxr.
"""

import ctypes
import xr.api2
import time


class FrameManager(object):
    """
    Context manager for a single OpenXR frame:
      calls xr.end_frame() even when exceptions occur
    """
    def __init__(self, session_manager):
        self.session_manager = session_manager
        self.frame_state = None
        self.render_layers = []

    def __enter__(self):
        session = self.session_manager.session
        self.frame_state = xr.wait_frame(session)
        if not self.session_manager.is_headless:
            xr.begin_frame(session)
            self.render_layers.clear()
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        if not self.session_manager.is_headless:
            xr.end_frame(
                session=self.session_manager.session,
                frame_end_info=xr.FrameEndInfo(
                    display_time=self.frame_state.predicted_display_time,
                    environment_blend_mode=xr.EnvironmentBlendMode.OPAQUE,
                    layers=self.render_layers,
                ),
            )

    def views(self):
        if self.session_manager.is_headless:
            return
        swapchains = self.session_manager.swapchains
        if swapchains is None:
            return
        for view in swapchains.views(self.frame_state, self.render_layers):
            yield view


class SessionManager(xr.api2.ISubscriber):
    """
    Context manager for the OpenXR session life cycle.
      Cleanly winds down the end of the session life cycle when exceptions occur.
      Including StopIteration when the client calls break on the frame loop.
    """
    def __init__(self, instance, session, begin_info: xr.SessionBeginInfo = None, is_headless=False):
        self.is_headless = is_headless
        self.instance = instance
        self.session = session
        if begin_info is None:
            begin_info = xr.SessionBeginInfo()
        self._begin_info = begin_info
        self.event_bus = xr.api2.EventBus()
        self.is_running = False
        self.exit_frame_loop = False
        self.session_state = xr.SessionState.UNKNOWN
        self.event_bus.subscribe(
            event_key=xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED,
            subscriber=self,
        )

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb):
        # Try to wind down session lifecycle if client said "break"
        try:
            xr.request_exit_session(self.session)
        except xr.exception.SessionNotRunningError:
            pass  # Maybe someone already requested exit
        for _ in range(20):
            self._poll_events()
            if self.exit_frame_loop:
                break
            if self.is_running:
                with FrameManager(self) as frame:
                    time.sleep(0.050)

    def frames(self):
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
        :param event_key: event type
        :param event_data: event buffer
        :return:
        """
        if event_key == xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED:
            event = ctypes.cast(
                ctypes.byref(event_data),
                ctypes.POINTER(xr.EventDataSessionStateChanged)).contents
            self.session_state = xr.SessionState(event.state)
            print(self.session_state.name)
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
        while True:
            try:
                event_buffer = xr.poll_event(self.instance)
                event_type = xr.StructureType(event_buffer.type)
                self.event_bus.post(event_key=event_type, event_data=event_buffer)
            except xr.EventUnavailable:
                break


class XrContext(object):
    def __init__(self, renderers=[], actioners=[]):
        # Choose instance extensions
        available_extensions = xr.enumerate_instance_extension_properties()
        extensions = []
        graphics_extension = None
        self.is_headless = False
        if len(renderers) == 0:
            if xr.MND_HEADLESS_EXTENSION_NAME in available_extensions:
                graphics_extension = xr.MND_HEADLESS_EXTENSION_NAME
                self.is_headless = True
        if graphics_extension is None and xr.KHR_OPENGL_ENABLE_EXTENSION_NAME in available_extensions:
            graphics_extension = xr.KHR_OPENGL_ENABLE_EXTENSION_NAME
        assert graphics_extension is not None
        extensions.append(graphics_extension)
        if graphics_extension == xr.MND_HEADLESS_EXTENSION_NAME:
            extensions.extend(xr.api2.TimeFetcher.required_extensions())
        self.instance_create_info = xr.InstanceCreateInfo(
            enabled_extension_names=extensions,
        )

    def frames(self):
        with xr.Instance(self.instance_create_info) as instance:
            system_id = xr.get_system(instance)
            with xr.Session(
                    instance=instance,
                    create_info=xr.SessionCreateInfo(
                        system_id=system_id,
                        next=None,  # TODO
                    ),
            ) as session:
                with SessionManager(
                        instance=instance,
                        session=session,
                        is_headless=self.is_headless
                ) as session_manager:
                    for frame in session_manager.frames():
                        yield frame


def main():
    """
    Things to test:
      * break in client but cleanly wind down session state
      * KeyboardInterrupt but cleanly wind down session state
      * close window and cleanly wind down session state
    """
    for frame_index, frame in enumerate(XrContext().frames()):
        time.sleep(0.500)
        print(frame_index)
        if frame_index > 10:
            break


if __name__ == "__main__":
    main()
