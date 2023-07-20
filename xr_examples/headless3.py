"""
pyopenxr headless example
using higher level 3 pyopenxr API
"""

import ctypes
import time
import xr.api3

# Enumerate the required instance extensions
# XR_MND_HEADLESS_EXTENSION permits use without a graphics display
extensions = [xr.MND_HEADLESS_EXTENSION_NAME]  # Permits use without a graphics display
extensions.extend(xr.api3.TimeFetcher.required_extensions())
# Create instance for headless use
instance = xr.create_instance(xr.InstanceCreateInfo(
    enabled_extension_names=extensions,
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

with xr.api3.TwoControllers(instance, session) as two_controllers:
    time_fetcher = xr.api3.TimeFetcher(instance)

    xr.attach_session_action_sets(
        session=session,
        attach_info=xr.SessionActionSetsAttachInfo(
            action_sets=[
                two_controllers.action_set,
            ],
        ),
    )

    session_state = xr.SessionState.UNKNOWN
    # Loop over session frames
    for frame_index in range(10):  # Limit number of frames for demo purposes
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

            time_now = time_fetcher.time_now()

            found_count = 0
            for index, space_location in two_controllers.enumerate_active_controllers(time_now):
                if space_location.location_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT:
                    print(f"Controller {index + 1}: {space_location.pose}")
                    found_count += 1
            if found_count == 0:
                print("no controllers active")

            # Sleep periodically to avoid consuming all available system resources
            time.sleep(0.500)

# Clean up
system = xr.NULL_SYSTEM_ID
action_set = None
xr.destroy_instance(instance)
instance = None
