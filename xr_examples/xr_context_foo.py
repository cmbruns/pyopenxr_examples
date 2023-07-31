"""
Prototype for future high level api constructs in pyopenxr.
"""

import ctypes
import sys
import time
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
    def __init__(
            self,
            instance_create_info: xr.InstanceCreateInfo = None,
            system_get_info: xr.SystemGetInfo = None,
            session_create_info: xr.SessionCreateInfo = None,
            reference_space_type: xr.ReferenceSpaceType = xr.ReferenceSpaceType.STAGE,
    ) -> None:
        # TODO: take arguments for optional create_info objects
        if instance_create_info is None:
            instance_create_info = xr.InstanceCreateInfo(
                enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME],
            )
        enabled = [n.decode() for n in instance_create_info.enabled_extension_names]
        if xr.KHR_OPENGL_ENABLE_EXTENSION_NAME in enabled:
            self.graphics_extension = xr.KHR_OPENGL_ENABLE_EXTENSION_NAME
        elif xr.MND_HEADLESS_EXTENSION_NAME in enabled:
            self.graphics_extension = xr.MND_HEADLESS_EXTENSION_NAME
        else:
            raise NotImplementedError
        self.instance_create_info = instance_create_info
        if system_get_info is None:
            system_get_info = xr.SystemGetInfo()
        self.system_get_info = system_get_info
        if session_create_info is None:
            session_create_info = xr.SessionCreateInfo()
        self.reference_space_type = reference_space_type
        self.session_create_info = session_create_info
        self.instance = None
        self.system_id = None
        self.graphics_context = None
        self.session = None
        self.reference_space = None
        self.swapchains = None
        self.session_manager = None

    def __enter__(self):
        try:
            # Chain many dependent context managers
            self.instance = xr.Instance(self.instance_create_info).__enter__()
            self.system_id = xr.get_system(self.instance, self.system_get_info)
            if self.graphics_extension == xr.MND_HEADLESS_EXTENSION_NAME:
                graphics_binding_pointer = None
            elif self.graphics_extension == xr.KHR_OPENGL_ENABLE_EXTENSION_NAME:
                self.graphics_context = xr.api2.GLFWContext(
                    instance=self.instance,
                    system_id=self.system_id,
                ).__enter__()
                graphics_binding_pointer = self.graphics_context.graphics_binding_pointer
            else:
                raise NotImplementedError  # More graphics contexts!
            self.session_create_info.system_id = self.system_id
            self.session_create_info.next = graphics_binding_pointer
            self.session = xr.Session(
                instance=self.instance,
                create_info=self.session_create_info,
            ).__enter__()
            self.reference_space = xr.create_reference_space(
                session=self.session,
                create_info=xr.ReferenceSpaceCreateInfo(
                    reference_space_type=self.reference_space_type,
                ),
            )
            if self.graphics_extension == xr.MND_HEADLESS_EXTENSION_NAME:
                self.swapchains = None
            elif self.graphics_extension == xr.KHR_OPENGL_ENABLE_EXTENSION_NAME:
                self.swapchains = xr.api2.XrSwapchains(
                    instance=self.instance,
                    system_id=self.system_id,
                    session=self.session,
                    context=self.graphics_context,
                    view_configuration_type=xr.ViewConfigurationType.PRIMARY_STEREO,
                    reference_space=self.reference_space,
                ).__enter__()
            else:
                raise NotImplementedError  # More graphics contexts!
            self.session_manager = SessionManager(
                instance=self.instance,
                system_id=self.system_id,
                session=self.session,
                is_headless=self.graphics_extension == xr.MND_HEADLESS_EXTENSION_NAME,
                swapchains=self.swapchains,
            ).__enter__()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Unwind chained context managers in reverse order of creation
        if self.session_manager is not None:
            self.session_manager.__exit__(exc_type, exc_val, exc_tb)
            self.session_manager = None
        if self.swapchains is not None:
            self.swapchains.__exit__(exc_type, exc_val, exc_tb)
            self.swapchains = None
        if self.reference_space is None:
            xr.destroy_space(space=self.reference_space)
            self.reference_space = None
        if self.session is not None:
            self.session.__exit__(exc_type, exc_val, exc_tb)
            self.session = None
        if self.graphics_context is not None:
            self.graphics_context.__exit__(exc_type, exc_val, exc_tb)
            self.graphics_context = None
        self.system_id = None
        if self.instance is not None:
            self.instance.__exit__(exc_type, exc_val, exc_tb)
            self.instance = None

    def frames(self) -> Generator[FrameManager, None, None]:
        """
        The OpenXR frame loop.
        """
        if self.session_manager is None:
            with self:
                for frame in self.session_manager.frames():
                    yield frame
        else:
            for frame in self.session_manager.frames():
                yield frame


def main():
    with XrContext(
        instance_create_info=xr.InstanceCreateInfo(enabled_extension_names=[
            xr.KHR_OPENGL_ENABLE_EXTENSION_NAME,
            # xr.MND_HEADLESS_EXTENSION_NAME,
        ]),
    ) as context:
        instance, session = context.instance, context.session
        with xr.api2.TwoControllers(
            instance=instance,
            session=session,
            reference_space=context.reference_space,
        ) as two_controllers:
            xr.attach_session_action_sets(
                session=session,
                attach_info=xr.SessionActionSetsAttachInfo(
                    action_sets=[two_controllers.action_set],
                ),
            )
            for frame_index, frame in enumerate(context.frames()):
                if frame_index > 500:
                    break
                if frame.session_state == xr.SessionState.FOCUSED:
                    # Get controller poses
                    found_count = 0
                    for index, space_location in two_controllers.enumerate_active_controllers(
                            frame.frame_state.predicted_display_time):
                        if space_location.location_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT:
                            print(f"Controller {index + 1}: {space_location.pose}")
                            found_count += 1
                    if found_count == 0:
                        print("no controllers active")
                if frame.frame_state.should_render:
                    for _view in frame.views():
                        GL.glClearColor(1, 0.7, 0.7, 1)  # pink
                        GL.glClear(GL.GL_COLOR_BUFFER_BIT)


if __name__ == "__main__":
    main()
