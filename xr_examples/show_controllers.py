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
        #
        test_action_set = xr.ActionSet(instance, xr.ActionSetCreateInfo(
            action_set_name="vibrate_action_set",
            localized_action_set_name="Vibrate Action Set",
            priority=0,
        ))
        subaction_paths = [
            xr.string_to_path(instance, "/user/hand/left"),
            xr.string_to_path(instance, "/user/hand/right"),
        ]
        grab_action = xr.Action(test_action_set, xr.ActionCreateInfo(
            action_name="grab_object",
            localized_action_name="Grab object",
            action_type=xr.ActionType.FLOAT_INPUT,
            subaction_paths=subaction_paths,
        ))
        vibrate_action = xr.Action(test_action_set, xr.ActionCreateInfo(
            action_name="vibrate_hand",
            localized_action_name="Vibrate hand",
            action_type=xr.ActionType.VIBRATION_OUTPUT,
            subaction_paths=subaction_paths,
        ))
        trigger_value_path = [
            xr.string_to_path(instance, "/user/hand/left/input/trigger/value"),
            xr.string_to_path(instance, "/user/hand/right/input/trigger/value")]
        haptic_path = [
            xr.string_to_path(instance, "/user/hand/left/output/haptic"),
            xr.string_to_path(instance, "/user/hand/right/output/haptic")]
        vive_bindings = [
            xr.ActionSuggestedBinding(grab_action, trigger_value_path[0]),
            xr.ActionSuggestedBinding(grab_action, trigger_value_path[1]),
            xr.ActionSuggestedBinding(vibrate_action, haptic_path[0]),
            xr.ActionSuggestedBinding(vibrate_action, haptic_path[1]),
        ]
        xr.suggest_interaction_profile_bindings(
            instance=instance,
            suggested_bindings=xr.InteractionProfileSuggestedBinding(
                interaction_profile=xr.string_to_path(
                    instance,
                    "/interaction_profiles/htc/vive_controller",
                ),
                count_suggested_bindings=len(vive_bindings),
                suggested_bindings=(xr.ActionSuggestedBinding * len(vive_bindings))(*vive_bindings),
            ),
        )
        #
        with xr.api2.TwoControllers(
            instance=instance,
            session=session,
        ) as two_controllers:
            xr.attach_session_action_sets(
                session=session,
                attach_info=xr.SessionActionSetsAttachInfo(
                    action_sets=[two_controllers.action_set, test_action_set],
                ),
            )
            for frame_index, frame in enumerate(context.frames()):
                if frame_index > 1000:
                    break
                for c in controller_cubes:
                    c.do_show = False
                if frame.session_state == xr.SessionState.FOCUSED:
                    # Get controller poses
                    found_count = 0
                    for index, space_location in two_controllers.enumerate_active_controllers(
                        time=frame.frame_state.predicted_display_time,
                        reference_space=context.reference_space,
                    ):
                        if space_location.location_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT:
                            print(f"Controller {index + 1}: {space_location.pose}")
                            found_count += 1
                            if index < 2:
                                controller_cubes[index].do_show = True
                                tx = xr.Matrix4x4f.create_translation_rotation_scale(
                                    translation=space_location.pose.position,
                                    rotation=space_location.pose.orientation,
                                    scale=[0.1],
                                )
                                controller_cubes[index].model_matrix = tx.as_numpy()
                    if found_count == 0:
                        print("no controllers active")
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
