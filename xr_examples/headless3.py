"""
pyopenxr headless example
using higher level 3 pyopenxr API convenience functions
"""

import time
import xr.api3

# Enumerate the required instance extensions
# XR_MND_HEADLESS_EXTENSION permits use without a graphics display
extensions = [xr.MND_HEADLESS_EXTENSION_NAME]
# We will use a TimeFetcher object, which requires certain extensions
extensions.extend(xr.api3.TimeFetcher.required_extensions())
# InstanceManager automatically destroys our OpenXR instance when we are done
with xr.api3.InstanceManager(create_info=xr.InstanceCreateInfo(
    enabled_extension_names=extensions,
)) as instance:
    system = xr.get_system(
        instance,
        # Presumably the form factor is irrelevant in headless mode...
        xr.SystemGetInfo(form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY),
    )
    with xr.api3.SessionManager(
        instance,
        xr.SessionCreateInfo(
            system_id=system,
            next=None,  # No GraphicsBinding structure is required here in HEADLESS mode
        )
    ) as session:
        with xr.api3.TwoControllers(instance, session) as two_controllers:
            xr.attach_session_action_sets(
                session=session,
                attach_info=xr.SessionActionSetsAttachInfo(
                    action_sets=[two_controllers.action_set],
                ),
            )
            # In headless mode we will need to get time values without frame_info.predicted_display_time
            time_fetcher = xr.api3.TimeFetcher(instance)
            # Set up event handling to track session state
            event_bus = xr.api3.EventBus()
            xr_event_generator = xr.api3.XrEventGenerator(instance)
            session_status = xr.api3.SessionStatus(
                session=session,
                event_source=event_bus,
                begin_info=xr.SessionBeginInfo(),
            )
            # Loop over the session frames
            for _ in range(20):  # Limit to 20 total frames for demo purposes
                xr_event_generator.poll_events(destination=event_bus)
                if session_status.state in [xr.SessionState.FOCUSED]:
                    # wait_frame()/begin_frame()/end_frame() are not required in headless mode
                    xr.wait_frame(session=session)  # wait_frame is optional here; it helps SteamVR show application name better
                    # Get controller poses
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
