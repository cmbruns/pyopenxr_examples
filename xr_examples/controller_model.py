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
        self.images = []
        for image_index, image in enumerate(self.gltf.images):
            self.images.append(GltfTextureImage(self, image_index))
        self.nodes = dict()
        self.meshes = []
        for mesh_index, mesh in enumerate(self.gltf.meshes):
            self.meshes.append(GltfMesh(self, mesh_index))
        self.mesh_nodes = []  # to be filled in during node traversal
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
        self.mesh.paint_gl(context)


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
    def __init__(self, gltf_file: GltfFile, image_index: int):
        gltf = gltf_file.gltf
        gltf_image = gltf.images[image_index]
        self.buffer_view = gltf.bufferViews[gltf_image.bufferView]
        blob = gltf_file.blob
        png_data = blob[self.buffer_view.byteOffset:self.buffer_view.byteOffset + self.buffer_view.byteLength]
        self.pil_img = Image.open(BytesIO(png_data))
        # self.pil_img.show()
        # TODO non-8bit pixel depths
        self.numpy_data = numpy.array(self.pil_img.getdata(), dtype=numpy.uint8).flatten()  # Takes a moment...
        self.gl_buffer_id = None
        self.texture_id = None

    def init_gl(self):
        self.texture_id = GL.glGenTextures(1)
        # GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.texture_id)
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameterf(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGB,  # TODO: other formats
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


class GltfVertexAttribute(object):
    def __init__(self, gltf_file, attribute_index: int, location: int):
        self.location = location
        self.buffer = GltfBuffer(gltf_file, attribute_index, GL.GL_ARRAY_BUFFER)

    def init_gl(self):
        GL.glEnableVertexAttribArray(self.location)
        self.buffer.init_gl()
        accessor = self.buffer.accessor
        GL.glVertexAttribPointer(
            self.location,  # attribute index
            type_to_dim[accessor.type],
            accessor.componentType,
            accessor.normalized,
            0,  # stride
            ctypes.c_void_p(accessor.byteOffset)
        )


class GltfPrimitive(object):
    def __init__(self, gltf_file: GltfFile, primitive: pygltflib.Primitive) -> None:
        gltf = gltf_file.gltf
        att = primitive.attributes
        #
        self.pos_buffer = GltfBuffer(gltf_file, att.POSITION, GL.GL_ARRAY_BUFFER)
        #
        self.vertex_attributes = []
        if att.POSITION is not None:
            self.vertex_attributes.append(GltfVertexAttribute(gltf_file, att.POSITION, 0))
        self.element_buffer = None
        if primitive.indices is not None:
            self.element_buffer = GltfBuffer(gltf_file, primitive.indices, GL.GL_ELEMENT_ARRAY_BUFFER)
        self.texture_image = None
        if att.TEXCOORD_0 is not None and primitive.material is not None:
            material_index = primitive.material
            material = gltf.materials[material_index]
            # TODO: we are skipping a lot of properties here...
            pbr_metallic_roughness = material.pbrMetallicRoughness
            base_color_texture = pbr_metallic_roughness.baseColorTexture
            texture = gltf.textures[base_color_texture.index]
            self.texture_image = GltfTextureImage(gltf_file, texture.source)
            x = 3
        self.primitive = primitive
        self.vao = None
        self.vert_buffer = None

    def init_gl(self):
        self.vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(self.vao)
        #
        # for attribute in self.vertex_attributes:
        #     attribute.init_gl()
        #
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
        if self.texture_image is not None:
            self.texture_image.init_gl()
        GL.glBindVertexArray(0)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        GL.glDisableVertexAttribArray(location)

    def paint_gl(self, context):
        GL.glBindVertexArray(self.vao)
        if self.texture_image is not None:
            self.texture_image.bind()
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

    def paint_gl(self, context):
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
    for image_index, image in enumerate(glb.images):
        print("Image", image.name)
        texture = GltfTextureImage(glb, image_index)
        # texture.pil_img.show()
    for scene in glb.scenes:
        # print("Scene", scene.name)
        for node_index in scene.nodes:
            print_node(node_index, glb)


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
        for node_index in scene.nodes:
            node = gltf.nodes[node_index]
            mesh = get_a_mesh_from_node(gltf, node)
            if mesh is not None:
                return mesh
    return None


class ControllerRenderer(object):
    identity_matrix = xr.Matrix4x4f.create_scale(1).as_numpy()

    def __init__(self, nodes: list[GltfNode]):
        self.nodes = nodes
        self.shader = None
        self.model_matrix = self.identity_matrix

    def init_gl(self):
        for node in self.nodes:
            node.init_gl()
        vertex_shader = compileShader(
            inspect.cleandoc("""
            #version 430
            #line 303

            layout(location = 0) in vec3 position_in;
            layout(location = 1) in vec2 tex_coord_in;

            layout(location = 0) uniform mat4 Projection = mat4(1);
            layout(location = 4) uniform mat4 View = mat4(1);
            layout(location = 8) uniform mat4 Model = mat4(1);
            layout(location = 12) uniform mat4 NodeMatrix = mat4(1);
            
            out vec2 tex_coord;
            
            void main() {
              gl_Position = Projection * View * Model * NodeMatrix * vec4(position_in, 1.0);
              tex_coord = tex_coord_in;
            }
            """), GL.GL_VERTEX_SHADER)
        fragment_shader = compileShader(
            inspect.cleandoc("""
            #version 430
            #line 323

            uniform sampler2D image;
            in vec2 tex_coord;
            
            out vec4 fragColor;

            void main() {
              // fragColor = vec4(0, 1, 0, 1);  // green
              fragColor = texture(image, tex_coord);
            }
            """), GL.GL_FRAGMENT_SHADER)
        self.shader = compileProgram(vertex_shader, fragment_shader)
        GL.glEnable(GL.GL_DEPTH_TEST)

    def paint_gl(self, render_context):
        GL.glUseProgram(self.shader)
        GL.glUniformMatrix4fv(0, 1, False, render_context.projection_matrix)
        GL.glUniformMatrix4fv(4, 1, False, render_context.view_matrix)
        GL.glUniformMatrix4fv(8, 1, False, self.model_matrix)
        for node in self.nodes:
            GL.glUniformMatrix4fv(12, 1, False, node.global_matrix)
            node.paint_gl(render_context)


def show_controller():
    glb_filename = "C:/Users/cmbruns/Documents/git/webxr-input-profiles/packages/assets/profiles/htc-vive/none.glb"
    glb = GLTF2().load(glb_filename)
    mesh = get_a_mesh_from_gltf(glb)
    assert mesh
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
    gltf_file = GltfFile(glb_filename)
    renderers = [ControllerRenderer(gltf_file.mesh_nodes),  # left controller
                 ControllerRenderer(gltf_file.mesh_nodes), ]  # right controller
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
                        for controller_pose in controller_poses:
                            if controller_pose is None:
                                continue
                            for renderer in renderers:
                                renderer.model_matrix = controller_pose
                                renderer.paint_gl(render_context)


if __name__ == "__main__":
    # show_controller()
    test()
    # test2()
