"""
Level 2 API construction of instance.

Object-oriented context manager automatically cleans up handles at the end.
"""

import xr.api2

# XrContext context manager creates an instance and lots of other stuff too.
with xr.api2.XrContext(
        instance_create_info=xr.InstanceCreateInfo(
            enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME],
        ),
) as context:
    instance = context.instance
    session = context.session
    # etc...
