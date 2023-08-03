"""
pyopenxr example program color_cube2.py
This example renders one big cube.
"""

from OpenGL import GL
import xr.api2


with xr.api2.XrContext(
    instance_create_info=xr.InstanceCreateInfo(
        enabled_extension_names=[
            # A graphics extension is mandatory (without a headless extension)
            xr.KHR_OPENGL_ENABLE_EXTENSION_NAME,
        ],
    ),
    reference_space_type=xr.ReferenceSpaceType.STAGE,  # for well-defined floor level
) as context:
    context.graphics_context.make_current()
    with xr.api2.ColorCubeRenderer(model_matrix=[
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0.5, 0, 1,  # 0.5 meters up(Y): set cube base flat on floor
    ]) as floor_cube:
        for frame_index, frame in enumerate(context.frames()):
            if frame_index > 5000:
                break  # Keep demo short
            if frame.frame_state.should_render:
                for view in frame.views():
                    context.graphics_context.make_current()
                    GL.glClearColor(1, 0.7, 0.7, 1)  # pink
                    GL.glClearDepth(1.0)
                    GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
                    render_context = xr.api2.RenderContext(view)
                    floor_cube.paint_gl(render_context)
