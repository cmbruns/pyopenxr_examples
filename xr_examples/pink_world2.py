"""
pyopenxr example program pink_world2.py
This example renders a solid pink field to each eye.
"""

from OpenGL import GL
import xr.api2

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
                for frame_index in range(200):  # Limit to 20 total frames for demo purposes
                    xr_event_generator.poll_events(destination=event_bus)
                    if session_status.state in [
                        xr.SessionState.READY,
                        xr.SessionState.SYNCHRONIZED,
                        xr.SessionState.VISIBLE,
                        xr.SessionState.FOCUSED,
                    ]:
                        frame_state = xr.wait_frame(session)
                        xr.begin_frame(session)
                        render_layers = []
                        for view_index, view in swapchains.enumerate_views(frame_state, render_layers):
                            # Pink stuff here
                            gl_context.make_current()
                            GL.glClearColor(1, 0.7, 0.7, 1)  # pink
                            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                        xr.end_frame(
                            session=session,
                            frame_end_info=xr.FrameEndInfo(
                                display_time=frame_state.predicted_display_time,
                                environment_blend_mode=xr.EnvironmentBlendMode.OPAQUE,
                                layers=render_layers,
                            ),
                        )
                        # Pink stuff here
                        gl_context.make_current()
                        GL.glClearColor(1, 0.7, 0.7, 1)  # pink
                        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
