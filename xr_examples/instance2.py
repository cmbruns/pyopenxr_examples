"""
Level 2 API construction of instance.

A more pythonic version of the OpenXR API
"""

import xr

# The output parameter (instance) is the return value.
# Any non-success result triggers an exception.
instance = xr.create_instance(
    xr.InstanceCreateInfo(
        enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME],
    )
)

xr.destroy_instance(instance)  # need to manually clean up allocated resources
