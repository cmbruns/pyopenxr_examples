"""
pyopenxr headless example
using higher level 2 pyopenxr API convenience functions
"""

import time
import xr.api2

# Enumerate the required instance extensions
# XR_MND_HEADLESS_EXTENSION permits use without a graphics display
extensions = [xr.MND_HEADLESS_EXTENSION_NAME]
# We will use a TimeFetcher object, which requires certain extensions
# (because xr.wait_frame() can give unreliable times in headless mode)
extensions.extend(xr.api2.TimeFetcher.required_extensions())
# InstanceManager automatically destroys our OpenXR instance when we are done
with xr.api2.XrContext(instance_create_info=xr.InstanceCreateInfo(
    enabled_extension_names=extensions,
)) as context:
    instance, system_id, session = context.instance, context.system_id, context.session
    with xr.api2.TwoControllers(instance, session) as two_controllers:
        xr.attach_session_action_sets(
            session=session,
            attach_info=xr.SessionActionSetsAttachInfo(
                action_sets=[two_controllers.action_set],
            ),
        )
        # In headless mode we will need to get time values without frame_info.predicted_display_time
        time_fetcher = xr.api2.TimeFetcher(instance)
        # Set up event handling to track session state
        for frame_index, frame in enumerate(context.frames()):
            if frame_index > 20:
                break  # Short run for demo purposes
            if frame.session_state == xr.SessionState.FOCUSED:
                # Get controller poses
                found_count = 0
                time_now = time_fetcher.time_now()
                for index, space_location in two_controllers.enumerate_active_controllers(
                    time=time_now,  # predicted_display_time can be unusable in headless mode
                    reference_space=context.reference_space,
                ):
                    if space_location.location_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT:
                        print(f"Controller {index + 1}: {space_location.pose}")
                        found_count += 1
                if found_count == 0:
                    print("no controllers active")
                # Sleep periodically to avoid consuming all available system resources
                time.sleep(0.500)
