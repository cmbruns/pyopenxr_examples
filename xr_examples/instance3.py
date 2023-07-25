"""
Level 3 API construction of instance.

Object-oriented context manager automatically cleans up handles at the end.
"""

import xr

# Use "with" keyword to automatically clean up resources
with xr.Instance(
        create_info=xr.InstanceCreateInfo(
            enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME],
        ),
) as instance:
    pass
