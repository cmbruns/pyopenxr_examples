"""
pyopenxr headless example
using low level OpenXR API
"""

import ctypes
import time
import xr

# Create instance for headless use
instance = xr.create_instance(xr.InstanceCreateInfo(
    # XR_MND_HEADLESS_EXTENSION permits use without a graphics display
    enabled_extension_names=[
        xr.MND_HEADLESS_EXTENSION_NAME,
        # TODO: this time method is windows only
        xr.KHR_WIN32_CONVERT_PERFORMANCE_COUNTER_TIME_EXTENSION_NAME,
    ],
))
system = xr.get_system(
    instance,
    # Presumably the form factor is irrelevant in headless mode...
    xr.SystemGetInfo(form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY),
)
session = xr.create_session(
    instance,
    xr.SessionCreateInfo(
        system_id=system,
        next=None,  # No GraphicsBinding structure is required here in HEADLESS mode
    )
)

# Set up controller tracking, as one possible legitimate headless activity
action_set = xr.create_action_set(
    instance=instance,
    create_info=xr.ActionSetCreateInfo(
        action_set_name="action_set",
        localized_action_set_name="Action Set",
        priority=0,
    ),
)
controller_paths = (xr.Path * 2)(
    xr.string_to_path(instance, "/user/hand/left"),
    xr.string_to_path(instance, "/user/hand/right"),
)
controller_pose_action = xr.create_action(
    action_set=action_set,
    create_info=xr.ActionCreateInfo(
        action_type=xr.ActionType.POSE_INPUT,
        action_name="controller_pose",
        localized_action_name="Controller pose",
        count_subaction_paths=len(controller_paths),
        subaction_paths=controller_paths,
    ),
)
suggested_bindings = (xr.ActionSuggestedBinding * 2)(
    xr.ActionSuggestedBinding(
        action=controller_pose_action,
        binding=xr.string_to_path(
            instance=instance,
            path_string="/user/hand/left/input/grip/pose",
        ),
    ),
    xr.ActionSuggestedBinding(
        action=controller_pose_action,
        binding=xr.string_to_path(
            instance=instance,
            path_string="/user/hand/right/input/grip/pose",
        ),
    ),
)
xr.suggest_interaction_profile_bindings(
    instance=instance,
    suggested_bindings=xr.InteractionProfileSuggestedBinding(
        interaction_profile=xr.string_to_path(
            instance,
            "/interaction_profiles/khr/simple_controller",
        ),
        count_suggested_bindings=len(suggested_bindings),
        suggested_bindings=suggested_bindings,
    ),
)
xr.suggest_interaction_profile_bindings(
    instance=instance,
    suggested_bindings=xr.InteractionProfileSuggestedBinding(
        interaction_profile=xr.string_to_path(
            instance,
            "/interaction_profiles/htc/vive_controller",
        ),
        count_suggested_bindings=len(suggested_bindings),
        suggested_bindings=suggested_bindings,
    ),
)
xr.attach_session_action_sets(
    session=session,
    attach_info=xr.SessionActionSetsAttachInfo(
        action_sets=[action_set],
    ),
)
action_spaces = [
    xr.create_action_space(
        session=session,
        create_info=xr.ActionSpaceCreateInfo(
            action=controller_pose_action,
            subaction_path=controller_paths[0],
        ),
    ),
    xr.create_action_space(
        session=session,
        create_info=xr.ActionSpaceCreateInfo(
            action=controller_pose_action,
            subaction_path=controller_paths[1],
        ),
    ),
]
reference_space = xr.create_reference_space(
    session=session,
    create_info=xr.ReferenceSpaceCreateInfo(
        reference_space_type=xr.ReferenceSpaceType.STAGE,
    ),
)

pxrConvertWin32PerformanceCounterToTimeKHR = ctypes.cast(
    xr.get_instance_proc_addr(
        instance=instance,
        name="xrConvertWin32PerformanceCounterToTimeKHR",
    ),
    xr.PFN_xrConvertWin32PerformanceCounterToTimeKHR,
)


def xrConvertWin32PerformanceCounterToTimeKHR(instance: xr.Instance, performance_counter: int):
    pct = ctypes.c_longlong(performance_counter)
    xr_time = xr.Time()
    result = pxrConvertWin32PerformanceCounterToTimeKHR(
        instance,
        ctypes.pointer(pct),
        ctypes.byref(xr_time),
    )
    result = xr.check_result(result)
    if result.is_exception():
        raise result
    return xr_time


session_state = xr.SessionState.UNKNOWN
# Loop over session frames
for frame_index in range(30):  # Limit number of frames for demo purposes
    # Poll session state changed events
    while True:
        try:
            event_buffer = xr.poll_event(instance)
            event_type = xr.StructureType(event_buffer.type)
            if event_type == xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED:
                event = ctypes.cast(
                    ctypes.byref(event_buffer),
                    ctypes.POINTER(xr.EventDataSessionStateChanged)).contents
                session_state = xr.SessionState(event.state)
                print(f"OpenXR session state changed to xr.SessionState.{session_state.name}")
                if session_state == xr.SessionState.READY:
                    xr.begin_session(
                        session,
                        xr.SessionBeginInfo(
                            # TODO: zero should be allowed here...
                            primary_view_configuration_type=xr.ViewConfigurationType.PRIMARY_MONO,
                        ),
                    )
                elif session_state == xr.SessionState.STOPPING:
                    xr.destroy_session(session)
                    session = None
        except xr.EventUnavailable:
            break  # There is no event in the queue at this moment
    if session_state == xr.SessionState.FOCUSED:
        # wait_frame()/begin_frame()/end_frame() are not required in headless mode
        xr.wait_frame(session=session)  # Helps SteamVR show application name better
        # Perform per-frame activities here

        pc_time_now = int(time.perf_counter_ns() / 100)  # TODO: why 100?  Is this SteamVR specific?
        xr_time_now = xrConvertWin32PerformanceCounterToTimeKHR(instance, pc_time_now)

        active_action_set = xr.ActiveActionSet(
            action_set=action_set,
            subaction_path=xr.NULL_PATH,
        )
        xr.sync_actions(
            session=session,
            sync_info=xr.ActionsSyncInfo(
                active_action_sets=[active_action_set],
            ),
        )
        found_count = 0
        for index, space in enumerate(action_spaces):
            space_location = xr.locate_space(
                space=space,
                base_space=reference_space,
                time=xr_time_now,
            )
            if space_location.location_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT:
                print(f"Controller {index + 1}: {space_location.pose}")
                found_count += 1
        if found_count == 0:
            print("no controllers active")

        # Sleep periodically to avoid consuming all available system resources
        time.sleep(0.500)

# Clean up
system = xr.NULL_SYSTEM_ID
xr.destroy_action_set(action_set)
action_set = None
xr.destroy_instance(instance)
instance = None
