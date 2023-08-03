"""
pyopenxr controller tracking example
using higher level 3 pyopenxr API convenience functions.

see also headless3.py, if your openxr runtime has the XR_MND_headless extension
"""

import time
import xr.api2

# Enumerate the required instance extensions
extensions = xr.api2.GLFWContext.required_extensions()
# Create several context managers to automatically handle clean up.
# Place them all on one logical line, to minimize indentation.
# InstanceManager automatically destroys our OpenXR instance when we are done
with xr.Instance(
        create_info=xr.InstanceCreateInfo(
            enabled_extension_names=extensions,
        )
) as instance, \
        xr.SystemId(
            instance=instance,
            get_info=xr.SystemGetInfo(
                form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY,
            )
        ) as system_id, \
        xr.api2.GLFWContext(
            instance=instance,
            system_id=system_id,
        ) as graphics, \
        xr.Session(
            instance=instance,
            create_info=xr.SessionCreateInfo(
                system_id=system_id,
                next=graphics.graphics_binding_pointer,
            )
        ) as session, \
        xr.api2.TwoControllers(
            instance=instance,
            session=session) as two_controllers, \
        xr.api2.SessionManager(
            system_id=system_id,
            instance=instance,
            session=session,
        ) as session_manager:
    xr.attach_session_action_sets(
        session=session,
        attach_info=xr.SessionActionSetsAttachInfo(
            action_sets=[two_controllers.action_set],
        ),
    )
    reference_space = xr.create_reference_space(session, xr.ReferenceSpaceCreateInfo(
        reference_space_type=xr.ReferenceSpaceType.STAGE,
    ))
    # Loop over the session frames
    for frame_index, frame in enumerate(session_manager.frames()):
        if frame_index > 20:
            break  # Limit to 20 total frames for demo purposes
        if frame.session_state == xr.SessionState.FOCUSED:
            # Get controller poses
            found_count = 0
            for index, space_location in two_controllers.enumerate_active_controllers(
                    time=frame.frame_state.predicted_display_time,
                    reference_space=reference_space,
            ):
                if space_location.location_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT:
                    print(f"Controller {index + 1}: {space_location.pose}")
                    found_count += 1
            if found_count == 0:
                print("no controllers active")
        time.sleep(0.500)  # Limit frame rate, since we are not rendering anything
