import ctypes
import inspect
from io import BytesIO
from typing import Dict

import numpy
from OpenGL import GL
from OpenGL.GL.shaders import compileShader, compileProgram
from PIL import Image
from pygltflib import GLTF2

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


class GltfBuffer(object):
    def __init__(self, gltf, accessor_index, target):
        self.target = target  # e.g. GL_ARRAY_BUFFER
        self.accessor = gltf.accessors[accessor_index]
        buffer_view = gltf.bufferViews[self.accessor.bufferView]
        self.data = gltf.binary_blob()[buffer_view.byteOffset:buffer_view.byteOffset + buffer_view.byteLength]
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


class GltfMesh(object):
    def __init__(self, gltf: GLTF2, mesh) -> None:
        self.gltf = gltf
        self.mesh = mesh
        self.primitive = mesh.primitives[0]  # TODO all the primitives
        att = self.primitive.attributes
        # TODO: separate positions object
        assert att.POSITION is not None
        self.pos_buffer = GltfBuffer(gltf, att.POSITION, GL.GL_ARRAY_BUFFER)
        self.element_buffer = None
        if self.primitive.indices is not None:
            self.element_buffer = GltfBuffer(gltf, self.primitive.indices, GL.GL_ELEMENT_ARRAY_BUFFER)
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
    def __init__(self, vbuf: GltfMesh):
        self.vbuf = vbuf
        self.shader = None

    def init_gl(self):
        self.vbuf.init_gl()
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
        # TODO model matrix
        self.vbuf.paint_gl(render_context)


def show_controller():
    print(int(GL.GL_TRIANGLES))
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


if __name__ == "__main__":
    show_controller()
