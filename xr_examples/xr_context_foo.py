"""
Prototype for future high level api constructs in pyopenxr.
"""

import ctypes
import sys
import time
from typing import Generator

from OpenGL import GL

import xr.api2


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
            self.session_manager = xr.api2.SessionManager(
                instance=self.instance,
                system_id=self.system_id,
                session=self.session,
                is_headless=self.graphics_extension == xr.MND_HEADLESS_EXTENSION_NAME,
                swapchains=self.swapchains,
            ).__enter__()
            return self
        except BaseException:
            # Clean up partially constructed context manager chain
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
        if self.reference_space is not None:
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

    def frames(self) -> Generator[xr.api2.FrameManager, None, None]:
        """
        The OpenXR frame loop.
        """
        if self.instance is None:  # Called in non-context manager context
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
        s = 0.5  # Length of cube edge = 50 cm
        floor_cube = xr.api2.CubeRenderer(model_matrix=[
            s, 0, 0, 0,
            0, s, 0, 0,
            0, 0, s, 0,
            0, 0.5 * s, 0, 1,  # set cube flat on floor
        ])
        controller_cubes = [xr.api2.CubeRenderer(), xr.api2.CubeRenderer()]
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
            reference_space=context.reference_space,
        ) as two_controllers:
            xr.attach_session_action_sets(
                session=session,
                attach_info=xr.SessionActionSetsAttachInfo(
                    action_sets=[two_controllers.action_set, test_action_set],
                ),
            )
            for frame_index, frame in enumerate(context.frames()):
                if frame_index > 5000:
                    break
                for c in controller_cubes:
                    c.do_show = False
                if frame.session_state == xr.SessionState.FOCUSED:
                    # Get controller poses
                    found_count = 0
                    for index, space_location in two_controllers.enumerate_active_controllers(
                            frame.frame_state.predicted_display_time):
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
