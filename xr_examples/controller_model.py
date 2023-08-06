import ctypes
import inspect
from io import BytesIO
from typing import Dict

import numpy
from OpenGL import GL
from OpenGL.GL.shaders import compileShader, compileProgram
from PIL import Image
import pygltflib
from pygltflib import GLTF2, Mesh

import xr.api2


def public_dir(thing):
    return [f for f in dir(thing) if not f.startswith("_")]


type_to_dim: Dict[str, int] = {
    'MAT4': 16,
    'VEC4': 4,
    'VEC3': 3,
    'VEC2': 2,
    'SCALAR': 1
}


# Wrapper classes with direct object references and OpenGL implementations


class GltfFile(object):
    def __init__(self, file):
        self.gltf = GLTF2().load(file)
        # Wrap with numpy so slices will be references not copies
        self.blob = numpy.frombuffer(self.gltf.binary_blob(), dtype=numpy.uint8)
        self.accessors = self.gltf.accessors
        self.nodes = {}  # TODO: separate node sets for left and right controllers
        self.meshes = []
        for mesh_index, mesh in enumerate(self.gltf.meshes):
            self.meshes.append(GltfMesh(self, mesh_index))
        self.mesh_nodes = []
        self.scenes = []
        for scene_index, scene in enumerate(self.gltf.scenes):
            self.scenes.append(GltfScene(self, scene_index))


class GltfScene(object):
    def __init__(self, gltf_file: GltfFile, scene_index: int):
        gltf = gltf_file.gltf
        self.scene = gltf.scenes[scene_index]
        self.nodes = []
        for node_index in self.scene.nodes:
            if node_index not in gltf_file.nodes:
                gltf_file.nodes[node_index] = GltfNode(gltf_file, node_index)
            self.nodes.append(gltf_file.nodes[node_index])


class GltfNode(object):
    def __init__(self, gltf_file: GltfFile, node_index: int, parent_node_stack=[]):
        gltf = gltf_file.gltf
        self.index = node_index
        self.node = gltf.nodes[node_index]
        print(self.index, self.node)
        self.children = []
        self.local_matrix = xr.Matrix4x4f.create_scale(1.0)
        if self.node.translation is not None:
            self.local_matrix @= xr.Matrix4x4f.create_translation(*self.node.translation)
        if self.node.rotation is not None:
            self.local_matrix @= xr.Matrix4x4f.create_from_quaternion(xr.Quaternionf(*self.node.rotation))
        if self.node.scale is not None:
            self.local_matrix @= xr.Matrix4x4f.create_scale(*self.node.scale)
        if self.node.matrix is not None:
            raise NotImplementedError
        self.node_stack = parent_node_stack + [self]
        self.global_matrix = xr.Matrix4x4f.create_scale(1)
        for node in self.node_stack:
            self.global_matrix @= node.local_matrix
        self.global_matrix = self.global_matrix.as_numpy()  # TODO: updatable matrix on node changes
        self.mesh = None
        if self.node.mesh is not None:
            print("  Mesh", self.node.mesh)
            mesh_index = self.node.mesh
            self.mesh = gltf_file.meshes[mesh_index]
            gltf_file.mesh_nodes.append(self)
        for child_index in self.node.children:
            self.children.append(GltfNode(gltf_file, child_index, parent_node_stack=self.node_stack))

    def init_gl(self):
        if self.mesh is None:
            return
        self.mesh.init_gl()

    def paint_gl(self, context: xr.api2.RenderContext):
        if self.mesh is None:
            return
        self.mesh.paint_gl(context, self.global_matrix)


class GltfBuffer(object):
    def __init__(self, gltf_file: GltfFile, accessor_index, target):
        gltf = gltf_file.gltf
        self.target = target  # e.g. GL_ARRAY_BUFFER
        self.accessor = gltf.accessors[accessor_index]
        buffer_view = gltf.bufferViews[self.accessor.bufferView]
        self.data = gltf_file.blob[buffer_view.byteOffset:buffer_view.byteOffset + buffer_view.byteLength]
        self.buffer_view = buffer_view
        self.gl_buffer = None

    def init_gl(self):
        self.gl_buffer = GL.glGenBuffers(1)
        GL.glBindBuffer(self.target, self.gl_buffer)
        GL.glBufferData(
            target=self.target,
            size=self.buffer_view.byteLength,
            data=self.data,
            usage=GL.GL_STATIC_DRAW,
        )


class GltfTextureImage(object):
    def __init__(self, gltf: GLTF2, image_index: int):
        self.gltf_image_index = image_index
        gltf_image = gltf.images[image_index]
        self.name = gltf_image.name
        self.buffer_view = gltf.bufferViews[gltf_image.bufferView]
        blob = gltf.binary_blob()
        png_data = blob[self.buffer_view.byteOffset:self.buffer_view.byteOffset + self.buffer_view.byteLength]
        self.pil_img = Image.open(BytesIO(png_data))
        # img.show()
        # TODO non-8bit pixel depths
        self.numpy_data = numpy.array(self.pil_img.getdata(), dtype=numpy.uint8).flatten()
        self.gl_buffer_id = None
        self.texture_id = None

    def init_gl(self):
        self.texture_id = GL.glGenTextures(1)
        # GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture_id)
        # TODO: texture format
        GL.glTexBuffer(GL.GL_TEXTURE_BUFFER, GL.GL_RGB, self.gl_buffer_id)
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGB,  #
            self.pil_img.width,
            self.pil_img.height,
            0,
            GL.GL_RGB,  #
            GL.GL_UNSIGNED_BYTE,
            self.numpy_data,
        )
        GL.glGenerateMipmap(GL.GL_TEXTURE_2D)

    def bind(self, texture_unit=0):
        GL.glActiveTexture(GL.GL_TEXTURE0 + texture_unit)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture_id)


class GltfPrimitive(object):
    def __init__(self, gltf_file: GltfFile, primitive: pygltflib.Primitive) -> None:
        att = primitive.attributes
        # TODO: separate positions object
        assert att.POSITION is not None
        self.pos_buffer = GltfBuffer(gltf_file, att.POSITION, GL.GL_ARRAY_BUFFER)
        self.element_buffer = None
        if primitive.indices is not None:
            self.element_buffer = GltfBuffer(gltf_file, primitive.indices, GL.GL_ELEMENT_ARRAY_BUFFER)
        self.primitive = primitive
        self.vao = None
        self.vert_buffer = None

    def init_gl(self):
        self.vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(self.vao)
        location = 0  # TODO
        GL.glEnableVertexAttribArray(location)
        self.pos_buffer.init_gl()
        acc = self.pos_buffer.accessor
        GL.glVertexAttribPointer(
            location,  # attribute index
            type_to_dim[acc.type],
            acc.componentType,
            acc.normalized,
            0,  # stride
            ctypes.c_void_p(acc.byteOffset)
        )
        if self.element_buffer is not None:
            self.element_buffer.init_gl()
        GL.glBindVertexArray(0)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        GL.glDisableVertexAttribArray(0)

    def paint_gl(self, context):
        GL.glBindVertexArray(self.vao)
        if self.element_buffer is not None:
            acc = self.element_buffer.accessor
            GL.glDrawElements(
                self.primitive.mode,
                acc.count,
                acc.componentType,
                ctypes.c_void_p(acc.byteOffset)
            )
        else:
            GL.glDrawArrays(self.primitive.mode, 0, self.pos_buffer.accessor.count)


class GltfMesh(object):
    def __init__(self, gltf_file: GLTF2, mesh_index) -> None:
        gltf = gltf_file.gltf
        mesh = gltf.meshes[mesh_index]
        self.mesh = mesh
        self.primitives = []
        for primitive in mesh.primitives:
            self.primitives.append(GltfPrimitive(gltf_file, primitive))

    def init_gl(self):
        for primitive in self.primitives:
            primitive.init_gl()

    def paint_gl(self, context, node_matrix):
        for primitive in self.primitives:
            primitive.paint_gl(context)


def print_node(node_index, gltf, indent=2, parent_node_stack=()):
    node = gltf.nodes[node_index]
    print(" " * indent, "Node", node.name, node.rotation, node.translation, public_dir(node))
    node_stack = (*parent_node_stack, node)
    if node.mesh is not None:
        mesh = gltf.meshes[node.mesh]
        print(" " * (indent + 2), "Mesh", mesh.name)
        vertex_buffer = GltfMesh(gltf, mesh)
    for child_index in node.children:
        print_node(child_index, gltf, indent + 2, node_stack)


def test():
    glb_filename = "C:/Users/cmbruns/Documents/git/webxr-input-profiles/packages/assets/profiles/htc-vive/none.glb"
    glb = GLTF2().load(glb_filename)
    print(public_dir(glb))
    for image_index, image in enumerate(glb.images):
        print("Image", image.name)
        texture = GltfTextureImage(glb, image_index)
        # texture.pil_img.show()
    for scene in glb.scenes:
        # print("Scene", scene.name)
        for node_index in scene.nodes:
            print_node(node_index, glb)
    for animation in glb.animations:
        print("Animation", public_dir(animation))


def get_a_mesh_from_node(gltf, node):
    if node.mesh is not None:
        return gltf.meshes[node.mesh]
    for child_index in node.children:
        child = gltf.nodes[child_index]
        mesh = get_a_mesh_from_node(gltf, child)
        if mesh is not None:
            return mesh
    return None


def get_a_mesh_from_gltf(gltf):
    for scene in gltf.scenes:
        # print("Scene", scene.name)
        for node_index in scene.nodes:
            node = gltf.nodes[node_index]
            mesh = get_a_mesh_from_node(gltf, node)
            if mesh is not None:
                return mesh
    return None


class ControllerRenderer(object):
    identity_matrix = xr.Matrix4x4f.create_scale(1).as_numpy()

    def __init__(self, node: GltfNode):
        self.node = node
        self.shader = None

    def init_gl(self):
        self.node.init_gl()
        vertex_shader = compileShader(
            inspect.cleandoc("""
            #version 430
            #line 180

            in vec3 position;

            layout(location = 0) uniform mat4 Projection = mat4(1);
            layout(location = 4) uniform mat4 View = mat4(1);
            layout(location = 8) uniform mat4 Model = mat4(1);
                
            void main() {
              gl_Position = Projection * View * Model * vec4(position, 1.0);
            }
            """), GL.GL_VERTEX_SHADER)
        fragment_shader = compileShader(
            inspect.cleandoc("""
            #version 430

            out vec4 FragColor;

            void main() {
              FragColor = vec4(0, 1, 0, 1);  // green
            }
            """), GL.GL_FRAGMENT_SHADER)
        self.shader = compileProgram(vertex_shader, fragment_shader)
        GL.glEnable(GL.GL_DEPTH_TEST)

    def paint_gl(self, render_context):
        GL.glUseProgram(self.shader)
        GL.glUniformMatrix4fv(0, 1, False, render_context.projection_matrix)
        GL.glUniformMatrix4fv(4, 1, False, render_context.view_matrix)
        if render_context.model_matrix is None:
            GL.glUniformMatrix4fv(8, 1, False, self.identity_matrix)
        else:
            GL.glUniformMatrix4fv(8, 1, False, render_context.model_matrix)
        self.node.paint_gl(render_context)


def show_controller():
    glb_filename = "C:/Users/cmbruns/Documents/git/webxr-input-profiles/packages/assets/profiles/htc-vive/none.glb"
    glb = GLTF2().load(glb_filename)
    mesh = get_a_mesh_from_gltf(glb)
    assert mesh
    print(mesh)
    vbuff = GltfMesh(glb, mesh)
    renderer = ControllerRenderer(vbuff)
    with xr.api2.XrContext(
            instance_create_info=xr.InstanceCreateInfo(
                enabled_extension_names=[
                    # A graphics extension is mandatory (without a headless extension)
                    xr.KHR_OPENGL_ENABLE_EXTENSION_NAME,
                ],
            ),
    ) as context:
        context.graphics_context.make_current()
        renderer.init_gl()
        for frame_index, frame in enumerate(context.frames()):
            if frame.frame_state.should_render:
                for view in frame.views():
                    GL.glClearColor(1, 0.7, 0.7, 1)  # pink
                    GL.glClearDepth(1.0)
                    GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
                    render_context = xr.api2.RenderContext(view)
                    renderer.paint_gl(render_context)


def print_node(gltf, node_index, indent=" "):
    node = gltf.nodes[node_index]
    print(indent, node_index, "Node", node.name)
    if node_index == 2:
        print(indent, node_index, node)
    if node.mesh is not None:
        mesh = gltf.meshes[node.mesh]
        print(indent + "  ", node.mesh, mesh)
    for child_index in node.children:
        print_node(gltf, child_index, indent + "  ")


def test2():
    glb_filename = "C:/Users/cmbruns/Documents/git/webxr-input-profiles/packages/assets/profiles/htc-vive/none.glb"
    glb = GLTF2().load(glb_filename)
    for scene in glb.scenes:
        print(scene)
        for node_index in scene.nodes:
            print_node(glb, node_index, "  ")


def test():
    glb_filename = "C:/Users/cmbruns/Documents/git/webxr-input-profiles/packages/assets/profiles/htc-vive/none.glb"
    # gltf = GLTF2().load(glb_filename)
    gltf_file = GltfFile(glb_filename)
    renderers = []
    print(len(gltf_file.meshes))
    for node in gltf_file.mesh_nodes:
        renderers.append(ControllerRenderer(node))
        print(node.mesh.mesh)
        # break  # OK just one for now
    with xr.api2.XrContext(
            instance_create_info=xr.InstanceCreateInfo(
                enabled_extension_names=[
                    # A graphics extension is mandatory (without a headless extension)
                    xr.KHR_OPENGL_ENABLE_EXTENSION_NAME,
                ],
            ),
    ) as context:
        instance, session = context.instance, context.session
        context.graphics_context.make_current()
        controller_poses = [None, None]
        for renderer in renderers:
            renderer.init_gl()
        with xr.api2.TwoControllers(
            instance=instance,
            session=session,
        ) as two_controllers:
            xr.attach_session_action_sets(
                session=session,
                attach_info=xr.SessionActionSetsAttachInfo(
                    action_sets=[two_controllers.action_set],
                ),
            )
            for frame_index, frame in enumerate(context.frames()):
                if frame.session_state == xr.SessionState.FOCUSED:
                    # Get controller poses
                    for index, space_location in two_controllers.enumerate_active_controllers(
                            time=frame.frame_state.predicted_display_time,
                            reference_space=context.reference_space,
                    ):
                        if space_location.location_flags & xr.SPACE_LOCATION_POSITION_VALID_BIT:
                            tx = xr.Matrix4x4f.create_translation_rotation_scale(
                                translation=space_location.pose.position,
                                rotation=space_location.pose.orientation,
                                scale=[1.0],
                            )
                            controller_poses[index] = tx.as_numpy()
                if frame.frame_state.should_render:
                    for view in frame.views():
                        GL.glClearColor(1, 0.7, 0.7, 1)  # pink
                        GL.glClearDepth(1.0)
                        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
                        render_context = xr.api2.RenderContext(view)
                        for renderer in renderers:
                            renderer.paint_gl(render_context)
                        for controller_pose in controller_poses:
                            # continue
                            if controller_pose is None:
                                continue
                            for renderer in renderers:
                                render_context.model_matrix = controller_pose
                                renderer.paint_gl(render_context)


if __name__ == "__main__":
    # show_controller()
    test()
    # test2()
