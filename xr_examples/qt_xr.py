import abc
import ctypes
import platform
import sys

from OpenGL import GL
if platform.system() == "Windows":
    from OpenGL import WGL
elif platform.system() == "Linux":
    from OpenGL import GLX
from PySide6 import QtCore, QtGui, QtOpenGLWidgets, QtWidgets
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtCore import Qt
import xr.api2


class IRenderer(abc.ABC):
    @abc.abstractmethod
    def init_gl(self):
        """Initialize OpenGL resources"""
        pass

    @abc.abstractmethod
    def render_scene(self, context):
        pass


class NothingRenderer(IRenderer):
    def init_gl(self):
        pass

    def render_scene(self, context):
        pass


class PinkWorldRenderer(IRenderer):
    def init_gl(self):
        pass

    def render_scene(self, context):
        if context.color_space == xr.api2.ColorSpace.LINEAR:
            GL.glClearColor(1, 0.7, 0.7, 1)  # pink
        else:  # srgb
            GL.glClearColor(1, 0.85, 0.85, 1)  # pink
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)


class QtGLContext(xr.api2.IGLContext):
    def __init__(self, widget: QtOpenGLWidgets.QOpenGLWidget):
        self.widget = widget

    def make_current(self) -> None:
        self.widget.makeCurrent()


class XrGlQtRenderer(object):
    def __init__(
        self,
        scene_renderer: IRenderer = None,
        instance_create_info: xr.InstanceCreateInfo = xr.InstanceCreateInfo(),
        system_get_info: xr.SystemGetInfo = xr.SystemGetInfo(),
        session_create_info: xr.SessionCreateInfo = xr.SessionCreateInfo(),
        reference_space_type: xr.ReferenceSpaceType = xr.ReferenceSpaceType.STAGE,
    ):
        self.scene_renderer = scene_renderer
        # Make sure OpenGL is enabled
        if xr.KHR_OPENGL_ENABLE_EXTENSION_NAME not in instance_create_info.enabled_extension_names:
            extensions = list(instance_create_info.enabled_extension_names)
            extensions.append(xr.KHR_OPENGL_ENABLE_EXTENSION_NAME)
            instance_create_info.enabled_extension_names = extensions
        self.instance = xr.create_instance(instance_create_info).__enter__()
        self.system_id = xr.get_system(
            instance=self.instance,
            get_info=system_get_info,
        )
        # TODO: maybe separate into pre-graphics and post-graphics objects...
        self.session = None  # Not until GL context is ready
        self.session_create_info = session_create_info
        self.session_create_info.system_id = self.system_id
        self.graphics_binding = None
        self.swapchains = None
        self.reference_space_type = reference_space_type
        self.reference_space = None
        self.session_manager = None
        self.graphics_context = None
        self.latest_render_context = None

    def init_gl(self):
        xr_get_open_gl_graphics_requirements_khr = ctypes.cast(
            xr.get_instance_proc_addr(
                instance=self.instance,
                name="xrGetOpenGLGraphicsRequirementsKHR",
            ),
            xr.PFN_xrGetOpenGLGraphicsRequirementsKHR
        )
        graphics_requirements = xr.GraphicsRequirementsOpenGLKHR()
        result = xr_get_open_gl_graphics_requirements_khr(
            self.instance,
            self.system_id,
            ctypes.byref(graphics_requirements))
        result = xr.check_result(xr.Result(result))
        if result.is_exception():
            raise result
        if platform.system() == "Windows":
            self.graphics_binding = xr.GraphicsBindingOpenGLWin32KHR()
            self.graphics_binding.h_dc = WGL.wglGetCurrentDC()
            self.graphics_binding.h_glrc = WGL.wglGetCurrentContext()
        elif platform.system() == "Linux":
            drawable = GLX.glXGetCurrentDrawable()
            context = GLX.glXGetCurrentContext()
            display = GLX.glXGetCurrentDisplay()
            self.graphics_binding = xr.GraphicsBindingOpenGLXlibKHR(
                x_display=display,
                glx_drawable=drawable,
                glx_context=context,
            )
        else:
            raise NotImplementedError
        self.session_create_info.next = ctypes.cast(
            ctypes.pointer(self.graphics_binding),
            ctypes.c_void_p,
        )
        self.session = xr.create_session(self.instance, self.session_create_info).__enter__()
        self.reference_space = xr.create_reference_space(
            session=self.session,
            create_info=xr.ReferenceSpaceCreateInfo(
                reference_space_type=self.reference_space_type,
            ),
        )
        self.swapchains = xr.api2.XrSwapchains(
            instance=self.instance,
            system_id=self.system_id,
            session=self.session,
            context=self.graphics_context,
            view_configuration_type=xr.ViewConfigurationType.PRIMARY_STEREO,
            reference_space=self.reference_space,
        ).__enter__()
        self.session_manager = xr.api2.SessionManager(
            instance=self.instance,
            system_id=self.system_id,
            session=self.session,
            is_headless=False,
            swapchains=self.swapchains,
        ).__enter__()
        if self.scene_renderer is not None:
            self.scene_renderer.init_gl()

    def render_scene(self):
        if self.scene_renderer is None:
            return
        for frame in self.session_manager.frame():  # zero or one frame
            if not frame.frame_state.should_render:
                return
            for view in frame.views():
                render_context = xr.api2.RenderContext.from_view(
                    view=view,
                    color_space=self.swapchains.color_space
                )
                self.latest_render_context = render_context
                self.scene_renderer.render_scene(render_context)


class MyGlWidget(QtOpenGLWidgets.QOpenGLWidget):
    def __init__(self, renderer: IRenderer):
        super().__init__()
        self.renderer = renderer
        # Use a timer to rerender as fast as possible
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(0)
        self.timer.timeout.connect(self.render_vr)
        # Accept keyboard events
        self.setFocusPolicy(Qt.StrongFocus)
        self.render_context = xr.api2.RenderContext(color_space=xr.api2.ColorSpace.SRGB)
        self.xr_renderer = XrGlQtRenderer(renderer)

    def initializeGL(self):
        self.xr_renderer.graphics_context = QtGLContext(self)
        self.xr_renderer.init_gl()
        if self.renderer is not None:
            self.renderer.init_gl()
        self.timer.start()

    def keyPressEvent(self, event):
        """press ESCAPE to quit the application"""
        key = event.key()
        if key == Qt.Key_Escape:
            QtCore.QCoreApplication.quit()

    def paintGL(self):
        print("paintGL")
        self.renderer.render_scene(self.render_context)

    def render_vr(self):
        self.makeCurrent()
        self.xr_renderer.render_scene()
        self.doneCurrent()
        self.render_context.view_matrix = self.xr_renderer.latest_render_context.view_matrix
        self.timer.start()  # render again real soon now


class QtApp(QtWidgets.QApplication):
    def __init__(self, renderer: IRenderer):
        # Set OpenGL version *before* constructing QApplication()
        gl_format = QSurfaceFormat()
        gl_format.setMajorVersion(4)
        gl_format.setMinorVersion(6)  # OpenGL 4.6
        gl_format.setProfile(QSurfaceFormat.CoreProfile)
        gl_format.setSwapBehavior(QSurfaceFormat.SwapBehavior.SingleBuffer)
        # Not working
        gl_format.setColorSpace(QtGui.QColorSpace.NamedColorSpace.SRgbLinear)
        QSurfaceFormat.setDefaultFormat(gl_format)
        super().__init__(sys.argv)
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle("Qt pyopenxr demo")
        self.window.resize(800, 600)
        self.glwidget = MyGlWidget(renderer)
        self.window.setCentralWidget(self.glwidget)
        self.window.show()


class NullGraphicsContext(xr.api2.IGLContext):
    def destroy(self) -> None:
        pass

    def make_current(self) -> None:
        pass


def main():
    renderer = PinkWorldRenderer()
    app = QtApp(renderer)
    result = app.exec()
    sys.exit(result)


if __name__ == "__main__":
    main()
