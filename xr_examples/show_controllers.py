"""
Show handheld motion controllers as cubes.
"""

from OpenGL import GL

import xr.api2


def main():
    with xr.api2.XrContext(
        instance_create_info=xr.InstanceCreateInfo(enabled_extension_names=[
            xr.KHR_OPENGL_ENABLE_EXTENSION_NAME,
            # xr.MND_HEADLESS_EXTENSION_NAME,
        ]),
    ) as context:
        instance, session = context.instance, context.session
        s = 0.5  # Length of cube edge = 50 cm
        floor_cube = xr.api2.ColorCubeRenderer(model_matrix=[
            s, 0, 0, 0,
            0, s, 0, 0,
            0, 0, s, 0,
            0, 0.5 * s, 0, 1,  # set cube flat on floor
        ])
        controller_cubes = [xr.api2.ColorCubeRenderer(), xr.api2.ColorCubeRenderer()]
        context.graphics_context.make_current()
        floor_cube.init_gl()
        for c in controller_cubes:
            c.init_gl()
        with xr.api2.TwoControllers(
            instance=instance,
            session=session,
        ) as two_controllers:
            xr.attach_session_action_sets(
                session=session,
                attach_info=xr.SessionActionSetsAttachInfo(
                    action_sets=[two_controllers.action_set],
                ),
            )
            for frame_index, frame in enumerate(context.frames()):
                if frame_index > 1000:
                    break
                for c in controller_cubes:
                    c.do_show = False
                if frame.session_state == xr.SessionState.FOCUSED:
                    # Get controller poses
                    for index, space_location in two_controllers.enumerate_active_controllers(
                        time=frame.frame_state.predicted_display_time,
                        reference_space=context.reference_space,
                    ):
                        if space_location.location_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT:
                            if index < 2:
                                controller_cubes[index].do_show = True
                                tx = xr.Matrix4x4f.create_translation_rotation_scale(
                                    translation=space_location.pose.position,
                                    rotation=space_location.pose.orientation,
                                    scale=[0.1],
                                )
                                controller_cubes[index].model_matrix = tx.as_numpy()
                if frame.frame_state.should_render:
                    for view in frame.views():
                        context.graphics_context.make_current()
                        GL.glClearColor(1, 0.7, 0.7, 1)  # pink
                        GL.glClearDepth(1.0)
                        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
                        render_context = xr.api2.RenderContext(view)
                        floor_cube.paint_gl(render_context)
                        for c in controller_cubes:
                            if c.do_show:
                                c.paint_gl(render_context)
            context.graphics_context.make_current()
            floor_cube.destroy_gl()
            for c in controller_cubes:
                c.destroy_gl()


if __name__ == "__main__":
    main()
