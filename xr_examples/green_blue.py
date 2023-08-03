"""
pyopenxr example program green_blue.py

Different way to get similar display to the venerable gl_example.py
"""

from OpenGL import GL
import xr.api2


# ContextObject is a high level pythonic class meant to keep simple cases simple.
with xr.api2.XrContext(
    instance_create_info=xr.InstanceCreateInfo(
        enabled_extension_names=[
            # A graphics extension is mandatory (without a headless extension)
            xr.KHR_OPENGL_ENABLE_EXTENSION_NAME,
        ],
    ),
) as context:
    eye_colors = [
        (0, 1, 0, 1),  # Left eye green
        (0, 0, 1, 1),  # Right eye blue
        (1, 0, 0, 1),  # Third eye blind
    ]
    for frame_index, frame in enumerate(context.frames()):
        for view_index, view in enumerate(frame.views()):
            # set each eye to a different color (not working yet...)
            GL.glClearColor(*eye_colors[view_index])
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        if frame_index > 500:  # Don't run forever
            break
