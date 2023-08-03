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
        xr.api2.SessionManager(
            system_id=system_id,
            instance=instance,
            session=session,
            begin_info=xr.SessionBeginInfo(
                primary_view_configuration_type=xr.ViewConfigurationType.PRIMARY_STEREO,
            ),
        ) as session_manager:
    reference_space = xr.create_reference_space(
        session=session,
        create_info=xr.ReferenceSpaceCreateInfo(
            reference_space_type=xr.ReferenceSpaceType.STAGE,
            pose_in_reference_space=xr.Posef(),
        ),
    )
    # Loop over the session frames
    for frame_index, frame in enumerate(session_manager.frames()):
        if frame_index > 20:
            break  # Limit to 20 total frames for demo purposes
        if frame.session_state in [
            xr.SessionState.READY,
            xr.SessionState.FOCUSED,
        ]:
            view_state, views = xr.locate_views(
                session=session,
                view_locate_info=xr.ViewLocateInfo(
                    view_configuration_type=xr.ViewConfigurationType.PRIMARY_STEREO,
                    display_time=frame.frame_state.predicted_display_time,
                    space=reference_space,
                )
            )
            print(views[0].pose)  # Left eye view
            # Sleep periodically to avoid consuming all available system resources
            time.sleep(0.500)
