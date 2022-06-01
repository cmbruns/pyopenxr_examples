import ctypes
import enum
import logging
import math

import xr
from xr import raw_functions

from .graphics_plugins import Cube

logger = logging.getLogger("hello_xr.program")


class Math(object):
    class Pose(object):
        @staticmethod
        def identity():
            t = xr.Posef()
            assert t.orientation.w == 1
            return t

        @staticmethod
        def translation(vec3):
            t = Math.Pose.identity()
            t.position[:] = vec3[:]
            return t

        @staticmethod
        def rotate_ccw_about_y_axis(radians, translation):
            t = Math.Pose.identity()
            t.orientation.x = 0
            t.orientation.y = math.sin(radians * 0.5)
            t.orientation.z = math.cos(radians * 0.5)
            t.orientation.w = 0
            t.position[:] = translation[:]
            return t


class Side(enum.IntEnum):
    LEFT = 0
    RIGHT = 1


class SwapChain(ctypes.Structure):
    _fields_ = [
        ("handle", xr.SwapchainHandle),
        ("width", ctypes.c_int32),
        ("height", ctypes.c_int32),
    ]


class InputState(ctypes.Structure):
    def __init__(self):
        super().__init__()
        self.hand_scale[:] = [1, 1]

    _fields_ = [
        ("action_set", xr.ActionSetHandle),
        ("grab_action", xr.ActionHandle),
        ("pose_action", xr.ActionHandle),
        ("vibrate_action", xr.ActionHandle),
        ("quit_action", xr.ActionHandle),
        ("hand_subaction_path", xr.Path * len(Side)),
        ("hand_space", xr.SpaceHandle * len(Side)),
        ("hand_scale", ctypes.c_float * len(Side)),
        ("hand_active", xr.Bool32 * len(Side)),
    ]


class OpenXRProgram(object):
    def __init__(self, options, platform_plugin, graphics_plugin):
        self.options = options
        self.platform_plugin = platform_plugin
        self.graphics_plugin = graphics_plugin
        self.instance = None
        self.session = None
        self.app_space = None
        self.form_factor = xr.FormFactor.HEAD_MOUNTED_DISPLAY
        self.view_config_type = xr.ViewConfigurationType.PRIMARY_STEREO
        self.environment_blend_mode = xr.EnvironmentBlendMode.OPAQUE
        self.system = None  # Higher level System class, not just ID

        self.config_views = []
        self.swapchains = []
        self.swapchain_images = {}
        self.views = []
        self.color_swapchain_format = -1

        self.visualized_spaces = []

        # Application's current lifecycle state according to the runtime
        self.session_state = xr.SessionState.UNKNOWN
        self.session_running = False

        self.event_data_buffer = xr.EventDataBuffer()
        self.input = InputState()
        self.projection_layer_views = (xr.CompositionLayerProjectionView * 2)(
            *([xr.CompositionLayerProjectionView()] * 2))
        self.projection_layer = xr.CompositionLayerProjection(view_count=2, views=self.projection_layer_views)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.input.action_set is not None:
            for hand in Side:
                if self.input.hand_space[hand] is not None:
                    xr.destroy_space(self.input.hand_space[hand])
                    self.input.hand_space[hand] = None
            xr.destroy_action_set(self.input.action_set)
            self.input.action_set = None
        for swapchain in self.swapchains:
            xr.destroy_swapchain(swapchain.handle)
            self.swapchains[:] = []
        for visualized_space in self.visualized_spaces:
            xr.destroy_space(visualized_space)
            self.visualized_spaces[:] = []
        if self.app_space is not None:
            xr.destroy_space(self.app_space)
            self.app_space = None
        if self.session.handle is not None:
            xr.destroy_session(self.session.handle)
            self.session.handle = None
        if self.instance is not None:
            self.instance.destroy()
        self.instance = None

    def create_instance(self):
        self.log_layers_and_extensions()
        self.create_instance_internal()
        self.log_instance_info()

    def create_instance_internal(self):
        extensions = []
        extensions.extend(self.platform_plugin.get_instance_extensions())
        extensions.extend(self.graphics_plugin.get_instance_extensions())
        self.instance = xr.Instance(extensions)

    def create_swapchains(self):
        assert self.session.handle is not None
        assert len(self.swapchains) == 0
        assert len(self.config_views) == 0
        # Read graphics properties for preferred swapchain length and logging.
        system_properties = xr.get_system_properties(self.instance.handle, self.system.id)
        # Log system properties
        logger.info("System Properties: "
                    f"Name={system_properties.system_name.decode()} "
                    f"VendorId={system_properties.vendor_id}")
        logger.info("System Graphics Properties: "
                    f"MaxWidth={system_properties.graphics_properties.max_swapchain_image_width} "
                    f"MaxHeight={system_properties.graphics_properties.max_swapchain_image_height} "
                    f"MaxLayers={system_properties.graphics_properties.max_layer_count}")
        logger.info("System Tracking Properties: "
                    f"OrientationTracking={bool(system_properties.tracking_properties.orientation_tracking)} "
                    f"PositionTracking={bool(system_properties.tracking_properties.position_tracking)}")
        # Note: No other view configurations exist at the time this (C++) code was written. If this
        # condition is not met, the project will need to be audited to see how support should be
        # added.
        if not self.view_config_type == xr.ViewConfigurationType.PRIMARY_STEREO:
            raise RuntimeError("Unsupported view configuration type")
        # Query and cache view configuration views.
        self.config_views = xr.enumerate_view_configuration_views(
            instance=self.instance.handle,
            system_id=self.system.id,
            view_configuration_type=self.view_config_type,
        )
        # Create and cache view buffer for xrLocateViews later.
        view_count = len(self.config_views)
        assert view_count == 2
        self.views = (xr.View * view_count)(*([xr.View()] * view_count))
        # Create the swapchain and get the images.
        if view_count > 0:
            # Select a swapchain format.
            swapchain_formats = xr.enumerate_swapchain_formats(self.session.handle)
            self.color_swapchain_format = self.graphics_plugin.select_color_swapchain_format(swapchain_formats)
            formats_string = ""
            for sc_format in swapchain_formats:
                selected = sc_format == self.color_swapchain_format
                formats_string += " "
                if selected:
                    formats_string += "["
                    formats_string += f"{str(self.color_swapchain_format)}({sc_format})"
                    formats_string += "]"
                else:
                    formats_string += str(sc_format)
            logger.debug(f"Swapchain Formats: {formats_string}")
            # Create a swapchain for each view.
            for i, vp in enumerate(self.config_views):
                logger.info("Creating swapchain for "
                            f"view {i} with dimensions "
                            f"Width={vp.recommended_image_rect_width} "
                            f"Height={vp.recommended_image_rect_height} "
                            f"SampleCount={vp.recommended_swapchain_sample_count}")
                # Create the swapchain.
                swapchain_create_info = xr.SwapchainCreateInfo(
                    array_size=1,
                    format=self.color_swapchain_format,
                    width=vp.recommended_image_rect_width,
                    height=vp.recommended_image_rect_height,
                    mip_count=1,
                    face_count=1,
                    sample_count=self.graphics_plugin.get_supported_swapchain_sample_count(vp),
                    usage_flags=xr.SwapchainUsageFlags.SAMPLED_BIT | xr.SwapchainUsageFlags.COLOR_ATTACHMENT_BIT,
                )
                swapchain = SwapChain(
                    xr.create_swapchain(
                        session=self.session.handle,
                        create_info=swapchain_create_info,
                    ),
                    swapchain_create_info.width,
                    swapchain_create_info.height,
                )
                self.swapchains.append(swapchain)
                # Use the two call paradigm manually so we can allocate swapchains correctly
                # First call of two, gets the number of swapchain images
                image_count = ctypes.c_uint32()
                result = xr.check_result(raw_functions.xrEnumerateSwapchainImages(
                    swapchain.handle,
                    0,
                    ctypes.byref(image_count),
                    None,
                ))
                if result.is_exception():
                    raise result
                swapchain_images = self.graphics_plugin.allocate_swapchain_image_structs(
                    image_count.value,
                    swapchain_create_info,
                )
                result = xr.check_result(raw_functions.xrEnumerateSwapchainImages(
                    swapchain.handle,
                    image_count,
                    ctypes.byref(image_count),
                    swapchain_images[0],
                ))
                if result.is_exception():
                    raise result
                self.swapchain_images[ctypes.addressof(swapchain.handle)] = swapchain_images

    def create_visualized_spaces(self):
        assert self.session is not None
        assert self.session.handle is not None
        visualized_spaces = [
            "ViewFront", "Local", "Stage", "StageLeft", "StageRight",
            "StageLeftRotated", "StageRightRotated",
        ]
        for visualized_space in visualized_spaces:
            try:
                space = xr.create_reference_space(
                    session=self.session.handle,
                    create_info=self.get_xr_reference_space_create_info(visualized_space)
                )
                self.visualized_spaces.append(space)
            except xr.XrException as exc:
                logger.warning(f"Failed to create reference space {visualized_space} with error {exc}")

    @staticmethod
    def get_xr_reference_space_create_info(reference_space_type_string):
        identity = xr.Posef()
        assert identity.orientation.w == 1
        create_info = xr.ReferenceSpaceCreateInfo(
            pose_in_reference_space=identity,
        )
        if reference_space_type_string.lower() == "View".lower():
            create_info.reference_space_type = xr.ReferenceSpaceType.VIEW
        elif reference_space_type_string.lower() == "ViewFront".lower():
            # Render head-locked 2m in front of device.
            create_info.pose_in_reference_space = Math.Pose.translation(vec3=[0, 0, -2])
            create_info.reference_space_type = xr.ReferenceSpaceType.VIEW
        elif reference_space_type_string.lower() == "Local".lower():
            create_info.reference_space_type = xr.ReferenceSpaceType.LOCAL
        elif reference_space_type_string.lower() == "Stage".lower():
            create_info.reference_space_type = xr.ReferenceSpaceType.STAGE
        elif reference_space_type_string.lower() == "StageLeft".lower():
            create_info.reference_space_type = xr.ReferenceSpaceType.STAGE
            create_info.pose_in_reference_space = Math.Pose.rotate_ccw_about_y_axis(0, [-2, 0, -2])
        elif reference_space_type_string.lower() == "StageRight".lower():
            create_info.reference_space_type = xr.ReferenceSpaceType.STAGE
            create_info.pose_in_reference_space = Math.Pose.rotate_ccw_about_y_axis(0, [2, 0, -2])
        elif reference_space_type_string.lower() == "StageLeftRotated".lower():
            create_info.reference_space_type = xr.ReferenceSpaceType.STAGE
            create_info.pose_in_reference_space = Math.Pose.rotate_ccw_about_y_axis(math.pi / 3, [-2, 0.5, -2])
        elif reference_space_type_string.lower() == "StageRightRotated".lower():
            create_info.reference_space_type = xr.ReferenceSpaceType.STAGE
            create_info.pose_in_reference_space = Math.Pose.rotate_ccw_about_y_axis(-math.pi / 3, [2, 0.5, -2])
        else:
            raise ValueError(f"Unknown reference space type '{reference_space_type_string}'")
        return create_info

    def handle_session_state_changed_event(self, state_changed_event, exit_render_loop, request_restart):
        # TODO: avoid this ugly cast
        event = ctypes.cast(ctypes.byref(state_changed_event), ctypes.POINTER(xr.EventDataSessionStateChanged)).contents
        old_state = self.session_state
        self.session_state = xr.SessionState(event.state)
        logger.info(f"XrEventDataSessionStateChanged: "
                    f"state {str(old_state)}->{str(self.session_state)} "
                    f"session={hex(ctypes.addressof(self.session.handle))} time={event.time}")
        # TODO: The session handles don't match in python but they do in C++...
        if False and event.session is not None and event.session != self.session.handle:
            a1 = self.session.handle
            a2 = event.session
            a3 = xr.NULL_HANDLE
            logger.error(f"XrEventDataSessionStateChanged for unknown session")
            return exit_render_loop, request_restart

        if self.session_state == xr.SessionState.READY:
            assert self.session is not None
            xr.begin_session(
                session=self.session.handle,
                begin_info=xr.SessionBeginInfo(
                    primary_view_configuration_type=self.view_config_type,
                ),
            )
            self.session_running = True
        elif self.session_state == xr.SessionState.STOPPING:
            assert self.session.handle is not None
            self.session_running = False
            xr.end_session(self.session.handle)
        elif self.session_state == xr.SessionState.EXITING:
            exit_render_loop = True
            # Do not attempt to restart because user closed this session.
            request_restart = False
        elif self.session_state == xr.SessionState.LOSS_PENDING:
            exit_render_loop = True
            # Poll for a new instance.
            request_restart = True
        return exit_render_loop, request_restart

    def initialize_actions(self):
        # Create an action set.
        action_set_info = xr.ActionSetCreateInfo(
            action_set_name="gameplay",
            localized_action_set_name="Gameplay",
            priority=0,
        )
        self.input.action_set = xr.create_action_set(self.instance.handle, action_set_info)
        # Get the XrPath for the left and right hands - we will use them as subaction paths.
        self.input.hand_subaction_path[Side.LEFT] = xr.string_to_path(
            self.instance.handle,
            "/user/hand/left")
        self.input.hand_subaction_path[Side.RIGHT] = xr.string_to_path(
            self.instance.handle,
            "/user/hand/right")
        # Create actions
        # Create an input action for grabbing objects with the left and right hands.
        self.input.grab_action = xr.create_action(
            action_set=self.input.action_set,
            create_info=xr.ActionCreateInfo(
                action_type=xr.ActionType.FLOAT_INPUT,
                action_name="grab_object",
                localized_action_name="Grab Object",
                count_subaction_paths=len(self.input.hand_subaction_path),
                subaction_paths=self.input.hand_subaction_path,
            ),
        )
        # Create an input action getting the left and right hand poses.
        self.input.pose_action = xr.create_action(
            action_set=self.input.action_set,
            create_info=xr.ActionCreateInfo(
                action_type=xr.ActionType.POSE_INPUT,
                action_name="hand_pose",
                localized_action_name="Hand Pose",
                count_subaction_paths=len(self.input.hand_subaction_path),
                subaction_paths=self.input.hand_subaction_path,
            ),
        )
        # Create output actions for vibrating the left and right controller.
        self.input.vibrate_action = xr.create_action(
            action_set=self.input.action_set,
            create_info=xr.ActionCreateInfo(
                action_type=xr.ActionType.VIBRATION_OUTPUT,
                action_name="vibrate_hand",
                localized_action_name="Vibrate Hand",
                count_subaction_paths=len(self.input.hand_subaction_path),
                subaction_paths=self.input.hand_subaction_path,
            ),
        )
        # Create input actions for quitting the session using the left and right controller.
        # Since it doesn't matter which hand did this, we do not specify subaction paths for it.
        # We will just suggest bindings for both hands, where possible.
        self.input.quit_action = xr.create_action(
            action_set=self.input.action_set,
            create_info=xr.ActionCreateInfo(
                action_type=xr.ActionType.BOOLEAN_INPUT,
                action_name="quit_session",
                localized_action_name="Quit Session",
                count_subaction_paths=0,
                subaction_paths=None,
            ),
        )
        select_path = [
            xr.string_to_path(self.instance.handle, "/user/hand/left/input/select/click"),
            xr.string_to_path(self.instance.handle, "/user/hand/right/input/select/click")]
        squeeze_value_path = [
            xr.string_to_path(self.instance.handle, "/user/hand/left/input/squeeze/value"),
            xr.string_to_path(self.instance.handle, "/user/hand/right/input/squeeze/value")]
        squeeze_force_path = [
            xr.string_to_path(self.instance.handle, "/user/hand/left/input/squeeze/force"),
            xr.string_to_path(self.instance.handle, "/user/hand/right/input/squeeze/force")]
        squeeze_click_path = [
            xr.string_to_path(self.instance.handle, "/user/hand/left/input/squeeze/click"),
            xr.string_to_path(self.instance.handle, "/user/hand/right/input/squeeze/click")]
        pose_path = [
            xr.string_to_path(self.instance.handle, "/user/hand/left/input/grip/pose"),
            xr.string_to_path(self.instance.handle, "/user/hand/right/input/grip/pose")]
        haptic_path = [
            xr.string_to_path(self.instance.handle, "/user/hand/left/output/haptic"),
            xr.string_to_path(self.instance.handle, "/user/hand/right/output/haptic")]
        menu_click_path = [
            xr.string_to_path(self.instance.handle, "/user/hand/left/input/menu/click"),
            xr.string_to_path(self.instance.handle, "/user/hand/right/input/menu/click")]
        b_click_path = [
            xr.string_to_path(self.instance.handle, "/user/hand/left/input/b/click"),
            xr.string_to_path(self.instance.handle, "/user/hand/right/input/b/click")]
        trigger_value_path = [
            xr.string_to_path(self.instance.handle, "/user/hand/left/input/trigger/value"),
            xr.string_to_path(self.instance.handle, "/user/hand/right/input/trigger/value")]
        # Suggest bindings for KHR Simple.
        khr_bindings = [
            # Fall back to a click input for the grab action.
            xr.ActionSuggestedBinding(self.input.grab_action, select_path[Side.LEFT]),
            xr.ActionSuggestedBinding(self.input.grab_action, select_path[Side.RIGHT]),
            xr.ActionSuggestedBinding(self.input.pose_action, pose_path[Side.LEFT]),
            xr.ActionSuggestedBinding(self.input.pose_action, pose_path[Side.RIGHT]),
            xr.ActionSuggestedBinding(self.input.quit_action, menu_click_path[Side.LEFT]),
            xr.ActionSuggestedBinding(self.input.quit_action, menu_click_path[Side.RIGHT]),
            xr.ActionSuggestedBinding(self.input.vibrate_action, haptic_path[Side.LEFT]),
            xr.ActionSuggestedBinding(self.input.vibrate_action, haptic_path[Side.RIGHT]),
        ]
        xr.suggest_interaction_profile_bindings(
            instance=self.instance.handle,
            suggested_bindings=xr.InteractionProfileSuggestedBinding(
                interaction_profile=xr.string_to_path(
                    self.instance.handle,
                    "/interaction_profiles/khr/simple_controller",
                ),
                count_suggested_bindings=len(khr_bindings),
                suggested_bindings=(xr.ActionSuggestedBinding * len(khr_bindings))(*khr_bindings),
            ),
        )
        # Suggest bindings for the Vive Controller.
        vive_bindings = [
            xr.ActionSuggestedBinding(self.input.grab_action, trigger_value_path[Side.LEFT]),
            xr.ActionSuggestedBinding(self.input.grab_action, trigger_value_path[Side.RIGHT]),
            xr.ActionSuggestedBinding(self.input.pose_action, pose_path[Side.LEFT]),
            xr.ActionSuggestedBinding(self.input.pose_action, pose_path[Side.RIGHT]),
            xr.ActionSuggestedBinding(self.input.quit_action, menu_click_path[Side.LEFT]),
            xr.ActionSuggestedBinding(self.input.quit_action, menu_click_path[Side.RIGHT]),
            xr.ActionSuggestedBinding(self.input.vibrate_action, haptic_path[Side.LEFT]),
            xr.ActionSuggestedBinding(self.input.vibrate_action, haptic_path[Side.RIGHT]),
        ]
        xr.suggest_interaction_profile_bindings(
            instance=self.instance.handle,
            suggested_bindings=xr.InteractionProfileSuggestedBinding(
                interaction_profile=xr.string_to_path(
                    self.instance.handle,
                    "/interaction_profiles/htc/vive_controller",
                ),
                count_suggested_bindings=len(vive_bindings),
                suggested_bindings=(xr.ActionSuggestedBinding * len(vive_bindings))(*vive_bindings),
            ),
        )
        # TODO the other controller types in openxr_programs.cpp
        info = xr.ActionSpaceCreateInfo(
            action=self.input.pose_action,
            # pose_in_action_space # w already defaults to 1 in python...
            subaction_path=self.input.hand_subaction_path[Side.LEFT],
        )
        self.input.hand_space[Side.LEFT] = xr.create_action_space(
            session=self.session.handle,
            create_info=xr.ActionSpaceCreateInfo(
                action=self.input.pose_action,
                # pose_in_action_space # w already defaults to 1 in python...
                subaction_path=self.input.hand_subaction_path[Side.LEFT],
            ),
        )
        self.input.hand_space[Side.RIGHT] = xr.create_action_space(
            session=self.session.handle,
            create_info=xr.ActionSpaceCreateInfo(
                action=self.input.pose_action,
                # pose_in_action_space # w already defaults to 1 in python...
                subaction_path=self.input.hand_subaction_path[Side.RIGHT],
            ),
        )
        xr.attach_session_action_sets(
            session=self.session.handle,
            attach_info=xr.SessionActionSetsAttachInfo(
                count_action_sets=1,
                action_sets=(xr.ActionSetHandle * 1)(self.input.action_set),
            ),
        )

    def initialize_session(self):
        assert self.instance is not None
        assert self.instance.handle != xr.NULL_HANDLE
        assert self.session is None
        logger.debug(f"Creating session...")
        self.session = xr.Session(
            system=self.system,
            graphics_binding=self.graphics_plugin.graphics_binding,
        )
        self.log_reference_spaces()
        self.initialize_actions()
        self.create_visualized_spaces()
        self.app_space = xr.create_reference_space(
            session=self.session.handle,
            create_info=self.get_xr_reference_space_create_info(self.options.space),
        )

    def initialize_system(self):
        assert self.instance is not None
        assert self.system is None
        form_factor = get_xr_form_factor(self.options.formfactor)
        self.view_config_type = get_xr_view_configuration_type(self.options.viewconfig)
        self.environment_blend_mode = get_xr_environment_blend_mode(self.options.blendmode)
        self.system = xr.System(instance=self.instance, form_factor=form_factor,
                                view_configuration_type=self.view_config_type)
        logger.debug(f"Using system {hex(self.system.id.value)} for form factor {str(form_factor)}")
        assert self.instance.handle is not None
        assert self.system.id is not None
        self.log_view_configurations()
        # The graphics API can initialize the graphics device now that the systemId and instance
        # handle are available.
        self.graphics_plugin.initialize_device(self.instance.handle, self.system.id)

    def log_action_source_name(self, action: xr.ActionHandle, action_name: str):
        paths = xr.enumerate_bound_sources_for_action(
            session=self.session.handle,
            enumerate_info=xr.BoundSourcesForActionEnumerateInfo(
                action=action,
            ),
        )
        source_name = ""
        for path in paths:
            all_flags = xr.INPUT_SOURCE_LOCALIZED_NAME_USER_PATH_BIT \
                        | xr.INPUT_SOURCE_LOCALIZED_NAME_INTERACTION_PROFILE_BIT \
                        | xr.INPUT_SOURCE_LOCALIZED_NAME_COMPONENT_BIT
            grab_source = xr.get_input_source_localized_name(
                session=self.session.handle,
                get_info=xr.InputSourceLocalizedNameGetInfo(
                    source_path=path,
                    which_components=all_flags,
                ),
            )
            if len(source_name) > 0:
                source_name += " and "
            source_name += f"'{grab_source}'"
        logger.info(f"{action_name} is bound to {source_name if len(source_name) > 0 else 'nothing'}")

    def log_environment_blend_mode(self, view_config_type):
        assert self.instance.handle is not None
        assert self.system.id is not None
        blend_modes = xr.enumerate_environment_blend_modes(self.instance.handle, self.system.id, view_config_type)
        logger.info(f"Available Environment Blend Mode count : ({len(blend_modes)})")
        blend_mode_found = False
        for mode_value in blend_modes:
            mode = xr.EnvironmentBlendMode(mode_value)
            blend_mode_match = mode == self.environment_blend_mode
            logger.info(f"Environment Blend Mode ({str(mode)}) : {'(Selected)' if blend_mode_match else ''}")
            blend_mode_found |= blend_mode_match
        assert blend_mode_found

    def log_instance_info(self):
        instance_properties = self.instance.get_properties()
        logger.info(
            f"Instance RuntimeName={instance_properties.runtime_name.decode()} RuntimeVersion={xr.Version(instance_properties.runtime_version)}")

    def log_layers_and_extensions(self):
        # Log non-layer extensions
        self._log_extensions(layer_name=None)
        # Log layers and any of their extensions
        layer_properties = xr.enumerate_api_layer_properties()
        logger.info(f"Available Layers: ({len(layer_properties)})")
        for layer in layer_properties:
            logger.debug(
                f"  Name={layer.layer_name.decode()} SpecVersion={self.xr_version_string()} LayerVersion={layer.layer_version} Description={layer.description}")

    def log_reference_spaces(self):
        assert self.session is not None
        assert self.session.handle != xr.NULL_HANDLE
        spaces = xr.enumerate_reference_spaces(self.session.handle)
        logger.info(f"Available reference spaces: {len(spaces)}")
        for space in spaces:
            logger.debug(f"  Name: {str(xr.ReferenceSpaceType(space))}")

    def log_view_configurations(self):
        assert self.instance.handle is not None
        assert self.system.id is not None
        view_config_types = xr.enumerate_view_configurations(self.instance.handle, self.system.id)
        logger.debug(f"Available View Configuration Types: ({len(view_config_types)})")
        for view_config_type_value in view_config_types:
            view_config_type = xr.ViewConfigurationType(view_config_type_value)
            logger.debug(
                f"  View Configuration Type: {str(view_config_type)} {'(Selected)' if view_config_type == self.view_config_type else ''}")
            view_config_properties = xr.get_view_configuration_properties(
                instance=self.instance.handle,
                system_id=self.system.id,
                view_configuration_type=view_config_type,
            )
            logger.debug(f"  View configuration FovMutable={bool(view_config_properties.fov_mutable)}")
            configuration_views = xr.enumerate_view_configuration_views(self.instance.handle, self.system.id,
                                                                        view_config_type)
            if configuration_views is None or len(configuration_views) < 1:
                logger.error(f"Empty view configuration type")
            else:
                for i, view in enumerate(configuration_views):
                    logger.debug(
                        f"    View [{i}]: Recommended Width={view.recommended_image_rect_width} Height={view.recommended_image_rect_height} SampleCount={view.recommended_swapchain_sample_count}")
                    logger.debug(
                        f"    View [{i}]:     Maximum Width={view.max_image_rect_width} Height={view.max_image_rect_height} SampleCount={view.max_swapchain_sample_count}")
            self.log_environment_blend_mode(view_config_type)

    @staticmethod
    def _log_extensions(layer_name, indent: int = 0):
        """Write out extension properties for a given layer."""
        extension_properties = xr.enumerate_instance_extension_properties(layer_name)
        indent_str = " " * indent
        logger.debug(f"{indent_str}Available Extensions ({len(extension_properties)})")
        for extension in extension_properties:
            logger.debug(
                f"{indent_str}  Name={extension.extension_name.decode()} SpecVersion={extension.extension_version}")

    def poll_actions(self):
        self.input.hand_active[:] = [xr.FALSE, xr.FALSE]
        # Sync actions
        active_action_set = xr.ActiveActionSet(self.input.action_set, xr.NULL_PATH)
        xr.sync_actions(
            self.session.handle,
            xr.ActionsSyncInfo(
                count_active_action_sets=1,
                active_action_sets=(xr.ActiveActionSet * 1)(active_action_set)
            ),
        )
        # Get pose and grab action state and start haptic vibrate when hand is 90% squeezed.
        for hand in Side:
            grab_value = xr.get_action_state_float(
                self.session.handle,
                xr.ActionStateGetInfo(
                    action=self.input.grab_action,
                    subaction_path=self.input.hand_subaction_path[hand],
                ),
            )
            if grab_value.is_active:
                # Scale the rendered hand by 1.0f (open) to 0.5f (fully squeezed).
                self.input.hand_scale[hand] = 1 - 0.5 * grab_value.current_state
                if grab_value.current_state > 0.9:
                    xr.apply_haptic_feedback(
                        session=self.session.handle,
                        haptic_action_info=xr.HapticActionInfo(
                            action=self.input.vibrate_action,
                            subaction_path=self.input.hand_subaction_path[hand],
                        ),
                        vibration=xr.HapticVibration(
                            amplitude=0.5,
                            duration=-1,  # TODO: XR_MIN_HAPTIC_DURATION is undefined for some reason
                            frequency=xr.FREQUENCY_UNSPECIFIED,
                        ),
                    )
            pose_state = xr.get_action_state_pose(
                session=self.session.handle,
                get_info=xr.ActionStateGetInfo(
                    action=self.input.pose_action,
                    subaction_path=self.input.hand_subaction_path[hand],
                ),
            )
            self.input.hand_active[hand] = pose_state.is_active
        # There were no subaction paths specified for the quit action, because we don't care which hand did it.
        quit_value = xr.get_action_state_boolean(
            session=self.session.handle,
            get_info=xr.ActionStateGetInfo(
                action=self.input.quit_action,
                subaction_path=xr.NULL_PATH,
            ),
        )
        if quit_value.is_active and quit_value.changed_since_last_sync and quit_value.current_state:
            xr.request_exit_session(self.session.handle)

    def poll_events(self):
        exit_render_loop = False
        request_restart = False
        # Process all pending messages.
        while True:
            try:
                event = xr.poll_event(self.instance.handle)
                assert event is not None
                event_type = xr.StructureType(event.type)
                if event_type == xr.StructureType.EVENT_DATA_EVENTS_LOST:
                    logger.warning(f"{event} events lost.")
                if event_type == xr.StructureType.EVENT_DATA_INSTANCE_LOSS_PENDING:
                    logger.warning(f"XrEventDataInstanceLossPending by {event.loss_time}")
                    return True, True
                elif event_type == xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED:
                    exit_render_loop, request_restart = self.handle_session_state_changed_event(
                        event, exit_render_loop, request_restart)
                elif event_type == xr.StructureType.EVENT_DATA_INTERACTION_PROFILE_CHANGED:
                    self.log_action_source_name(self.input.grab_action, "Grab")
                    self.log_action_source_name(self.input.quit_action, "Quit")
                    self.log_action_source_name(self.input.pose_action, "Pose")
                    self.log_action_source_name(self.input.vibrate_action, "Vibrate")
                elif event_type == xr.StructureType.EVENT_DATA_REFERENCE_SPACE_CHANGE_PENDING:
                    logger.debug(f"Ignoring event type {str(event_type)}")
                else:
                    logger.debug(f"Ignoring event type {str(event_type)}")
            except xr.EventUnavailable:
                break  # no more events in queue
        return exit_render_loop, request_restart

    def render_frame(self):
        assert self.session is not None
        assert self.session.handle != xr.NULL_HANDLE
        frame_state = xr.wait_frame(
            session=self.session.handle,
            frame_wait_info=xr.FrameWaitInfo(),
        )
        xr.begin_frame(self.session.handle, xr.FrameBeginInfo())
        projection_layer = None
        projection_layer_ptr = None
        projection_layer_count = 0
        if frame_state.should_render:
            self.render_layer(frame_state.predicted_display_time, self.projection_layer_views)
        p_layer_projection = ctypes.cast(
            ctypes.byref(self.projection_layer),
            ctypes.POINTER(xr.CompositionLayerBaseHeader))
        xr.end_frame(
            session=self.session.handle,
            frame_end_info=xr.FrameEndInfo(
                display_time=frame_state.predicted_display_time,
                environment_blend_mode=self.environment_blend_mode,
                layer_count=projection_layer_count,
                layers=ctypes.pointer(p_layer_projection),
            ),
        )

    def render_layer(self, predicted_display_time, projection_layer_views):
        view_capacity_input = len(self.views)
        view_state, self.views = xr.locate_views(
            session=self.session.handle,
            view_locate_info=xr.ViewLocateInfo(
                view_configuration_type=self.view_config_type,
                display_time=predicted_display_time,
                space=self.app_space,
            ),
        )
        view_count_output = len(self.views)
        vsf = view_state.view_state_flags
        if (vsf & xr.VIEW_STATE_POSITION_VALID_BIT == 0
                or vsf & xr.VIEW_STATE_ORIENTATION_VALID_BIT == 0
        ):
            return None  # There are no valid tracking poses for the views.
        assert view_count_output == view_capacity_input
        assert view_count_output == len(self.config_views)
        assert view_count_output == len(self.swapchains)
        # For each locatable space that we want to visualize, render a 25cm cube.
        cubes = []
        for visualized_space in self.visualized_spaces:
            space_location = xr.locate_space(
                space=visualized_space,
                base_space=self.app_space,
                time=predicted_display_time,
            )
            loc_flags = space_location.location_flags
            if (loc_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT != 0
                    and loc_flags & xr.SPACE_LOCATION_ORIENTATION_VALID_BIT != 0
            ):
                cubes.append(Cube(space_location.pose, xr.Vector3f(0.25, 0.25, 0.25)))
        # Render a 10cm cube scaled by grabAction for each hand. Note renderHand will only be
        # true when the application has focus.
        for hand in Side:
            space_location = xr.locate_space(
                space=self.input.hand_space[hand],
                base_space=self.app_space,
                time=predicted_display_time,
            )
            loc_flags = space_location.location_flags
            if (loc_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT != 0
                    and loc_flags & xr.SPACE_LOCATION_ORIENTATION_VALID_BIT != 0
            ):
                scale = 0.1 * self.input.hand_scale[hand]
                cubes.append(Cube(space_location.pose, xr.Vector3f(scale, scale, scale)))
        # Render view to the appropriate part of the swapchain image.
        for i, view_swap_chain in enumerate(self.swapchains):
            swapchain_image_index = xr.acquire_swapchain_image(
                swapchain=view_swap_chain.handle,
                acquire_info=xr.SwapchainImageAcquireInfo(),
            )
            xr.wait_swapchain_image(
                swapchain=view_swap_chain.handle,
                wait_info=xr.SwapchainImageWaitInfo(timeout=xr.INFINITE_DURATION),
            )
            projection_layer_views[i].pose = self.views[i].pose
            projection_layer_views[i].fov = self.views[i].fov
            x = self.views[i].fov
            y = projection_layer_views[i].fov
            projection_layer_views[i].sub_image.image_rect.offset[:] = [0, 0]
            projection_layer_views[i].sub_image.image_rect.extent[:] = [
                view_swap_chain.width, view_swap_chain.height, ]
            swap_chain_image_ptr = self.swapchain_images[ctypes.addressof(view_swap_chain.handle)][swapchain_image_index]
            self.graphics_plugin.render_view(
                projection_layer_views[i],
                swap_chain_image_ptr,
                self.color_swapchain_format,
                cubes,
            )
            xr.release_swapchain_image(view_swap_chain.handle, xr.SwapchainImageReleaseInfo())

    @staticmethod
    def xr_version_string():
        return xr.XR_CURRENT_API_VERSION


def get_xr_environment_blend_mode(environment_blend_mode_string):
    return {
        "Opaque": xr.EnvironmentBlendMode.OPAQUE,
        "Additive": xr.EnvironmentBlendMode.ADDITIVE,
        "AlphaBlend": xr.EnvironmentBlendMode.ALPHA_BLEND,
    }[environment_blend_mode_string]


def get_xr_form_factor(form_factor_string):
    if form_factor_string == "Hmd":
        return xr.FormFactor.HEAD_MOUNTED_DISPLAY
    elif form_factor_string == "Handheld":
        return xr.FormFactor.HANDHELD_DISPLAY
    raise ValueError


def get_xr_view_configuration_type(view_configuration_string):
    if view_configuration_string == "Mono":
        return xr.ViewConfigurationType.PRIMARY_MONO
    elif view_configuration_string == "Stereo":
        return xr.ViewConfigurationType.PRIMARY_STEREO
    raise ValueError
