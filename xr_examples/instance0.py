"""
Level 0 API construction of instance.

Using the lowest level less-pythonic ctypes API.
"""

import ctypes
import xr
import xr.raw_functions as xrr

instance = xr.Instance()  # create an uninitialized Instance
# directly call the raw ctypes xrCreateInstance function
result = xrr.xrCreateInstance(
    xr.InstanceCreateInfo(
        enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME],
    ),
    ctypes.byref(instance),
)
# check the result of the function call
if result != xr.Result.SUCCESS:
    raise RuntimeError("something is wrong")

# manually clean up the instance when we are done with it
result = xrr.xrDestroyInstance(instance)
if result != xr.Result.SUCCESS:
    raise RuntimeError("something is wrong")
