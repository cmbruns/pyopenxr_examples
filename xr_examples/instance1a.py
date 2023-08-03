"""
example instance1a.py
Level 1 API construction of instance.

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

# Because we called "create_instance()", we should manually clean up.
# (see instance1b.py for the context manager approach)
xr.destroy_instance(instance)

