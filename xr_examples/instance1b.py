"""
Level 1 API construction of instance.

A more pythonic version of the OpenXR API
"""

import xr

# Using the instance as a context manager via "with"
# automatically cleans up the instance object.
with xr.Instance(create_info=xr.InstanceCreateInfo(
    enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME],
)) as instance:
    pass
