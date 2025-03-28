# MIT License

# Copyright (c) 2024 Pogyzhinsu

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


bl_info = {
    "name": "Animation: Combine Actions for Export",
    "blender": (4, 0, 2),
    "category": "Animation",
    "author": "Pogyzhinsu",
    "version": (1, 2, 1),
    "description": "Combine multiple animations into one export-ready action",
}

import bpy
import mathutils

class CombineAnimationsOperator(bpy.types.Operator):
    """Combine animations into one export-ready action"""
    bl_label = "Combine Animations"
    bl_idname = "action.combine_animations"
    
    target_action_name: bpy.props.StringProperty(
        name="Target Action Name",
        description="Name for the combined action",
        default="Combined_Animation"
    )
    
    keep_original_actions: bpy.props.BoolProperty(
        name="Keep Original Actions",
        description="Keep the original actions after combining",
        default=True
    )
    
    add_root_track: bpy.props.BoolProperty(
        name="Add Root Track",
        description="Add location/rotation track for the root bone (needed for Unreal)",
        default=True
    )
    
    frame_margin: bpy.props.IntProperty(
        name="Frame Margin",
        description="Extra frames to add before and after detected animation range",
        default=0,
        min=0
    )
    
    debug_mode: bpy.props.BoolProperty(
        name="Debug Mode",
        description="Print detailed information during process",
        default=True
    )

    def execute(self, context):
        # Store original state
        original_mode = bpy.context.mode
        original_active = context.view_layer.objects.active
        original_area = bpy.context.area.type
        original_selected = context.selected_objects.copy()
        
        if self.debug_mode:
            self.report({'INFO'}, "Starting animation combination process...")
        
        # Get selected objects or all objects if none selected
        if context.selected_objects:
            base_objects = context.selected_objects.copy()
        else:
            self.report({'ERROR'}, "Please select at least one object from your rig.")
            return {'CANCELLED'}
            
        # Make sure there's an active object
        if not context.view_layer.objects.active and base_objects:
            context.view_layer.objects.active = base_objects[0]
            if self.debug_mode:
                self.report({'INFO'}, f"Setting active object to: {base_objects[0].name}")
        
        # Ensure we're in object mode
        try:
            if context.object and context.object.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError as e:
            self.report({'WARNING'}, f"Note: {str(e)}. Continuing anyway.")
        
        # Find the root object
        root_object = self.find_root_object(base_objects)
        if not root_object:
            self.report({'ERROR'}, "Could not determine root object. Please select a parent object with children.")
            return {'CANCELLED'}
            
        if self.debug_mode:
            self.report({'INFO'}, f"Using root object: {root_object.name}")
        
        # Get all objects in hierarchy
        all_objects = self.get_hierarchy_objects(root_object)
        if self.debug_mode:
            self.report({'INFO'}, f"Found {len(all_objects)} objects in hierarchy")
        
        # Find all animated objects
        animated_objects = []
        for obj in all_objects:
            if obj.animation_data and obj.animation_data.action:
                animated_objects.append(obj)
                
        if not animated_objects:
            self.report({'WARNING'}, "No animated objects found in the hierarchy.")
            return {'CANCELLED'}
            
        if self.debug_mode:
            self.report({'INFO'}, f"Found {len(animated_objects)} animated objects")
            
        # Find animation frame range
        min_frame, max_frame = self.find_animation_range(animated_objects)
        if min_frame == float('inf') or max_frame == float('-inf'):
            min_frame = context.scene.frame_start
            max_frame = context.scene.frame_end
        else:
            # Add margin to frame range
            min_frame = max(1, min_frame - self.frame_margin)
            max_frame = max_frame + self.frame_margin
            
        # Set the scene frame range
        context.scene.frame_start = int(min_frame)
        context.scene.frame_end = int(max_frame)
        
        if self.debug_mode:
            self.report({'INFO'}, f"Animation range: {min_frame} to {max_frame}")
        
        # Create a single standalone action
        result = self.create_standalone_action(context, root_object, animated_objects, 
                                          min_frame, max_frame)
        
        # Restore original selection
        bpy.ops.object.select_all(action='DESELECT')
        for obj in original_selected:
            if obj and obj.name in bpy.data.objects:
                obj.select_set(True)
        if original_active and original_active.name in bpy.data.objects:
            context.view_layer.objects.active = original_active
        
        # Restore original state
        bpy.context.area.type = original_area
        try:
            bpy.ops.object.mode_set(mode=original_mode)
        except:
            pass
            
        return result
    
    def create_standalone_action(self, context, root_object, animated_objects, min_frame, max_frame):
        """Create a truly combined animation driving all objects from a single action"""
        # Create a new action for the root
        master_action = bpy.data.actions.new(name=self.target_action_name)
        master_action.use_fake_user = True
        
        # Store original frame
        original_frame = context.scene.frame_current
        original_actions = {}
        
        # First collect all animation data
        animation_data = {}  # Will store {obj_name: {prop_name: {axis: [(frame, value)]}}}
        
        if self.debug_mode:
            self.report({'INFO'}, "Collecting animation data from all objects...")
        
        # Collect all keyframe data from all animations
        for obj in animated_objects:
            if not obj.animation_data or not obj.animation_data.action:
                continue
            
            # Store original action
            original_actions[obj] = obj.animation_data.action
            
            # Get animation data
            obj_data = {}
            for fcurve in obj.animation_data.action.fcurves:
                # Parse the data path and index
                prop_name = fcurve.data_path
                axis = fcurve.array_index
                
                # Store keyframe data
                keyframes = []
                for kf in fcurve.keyframe_points:
                    keyframes.append((kf.co[0], kf.co[1]))
                
                # Initialize property dict if needed
                if prop_name not in obj_data:
                    obj_data[prop_name] = {}
                
                # Store axis keyframes
                obj_data[prop_name][axis] = keyframes
                
            # Add to animation data
            if obj_data:
                animation_data[obj.name] = obj_data
        
        # Ensure root has animation data
        if not root_object.animation_data:
            root_object.animation_data_create()
        
        # Assign the action to the root object
        root_object.animation_data.action = master_action
        
        # Use a simpler naming convention for custom properties
        # Keep track of properties we need to create
        custom_props_to_create = {}
        
        # First pass: Create all the F-curves in the action
        for obj_name, props in animation_data.items():
            is_root = (obj_name == root_object.name)
            
            for prop_name, axes in props.items():
                for axis, keyframes in axes.items():
                    if is_root and prop_name in ["location", "rotation_euler", "scale"]:
                        # Direct animation on root
                        fc = master_action.fcurves.new(data_path=prop_name, index=axis)
                        for frame, value in keyframes:
                            fc.keyframe_points.insert(frame=frame, value=value)
                    else:
                        # Use a simpler naming scheme with fewer special characters
                        # Replace problematic characters
                        clean_obj_name = obj_name.replace('.', '_').replace(' ', '_')
                        
                        if prop_name in ["location", "rotation_euler", "scale"]:
                            # Standard transform properties
                            component = ["x", "y", "z"][axis]
                            
                            # Create a simple custom property name
                            # Format: obj_loc_x, obj_rot_y, obj_scale_z
                            if prop_name == "location":
                                prop_key = f"{clean_obj_name}_loc_{component}"
                            elif prop_name == "rotation_euler":
                                prop_key = f"{clean_obj_name}_rot_{component}"
                            else:  # scale
                                prop_key = f"{clean_obj_name}_scale_{component}"
                                
                            # Create the property if it doesn't exist
                            if prop_key not in root_object:
                                if self.debug_mode:
                                    self.report({'INFO'}, f"Creating custom property: {prop_key}")
                                root_object[prop_key] = 0.0
                                custom_props_to_create[prop_key] = True
                            
                            # Create the F-curve for this property
                            fc = master_action.fcurves.new(data_path=f'["{prop_key}"]')
                            
                            # Add keyframes
                            for frame, value in keyframes:
                                fc.keyframe_points.insert(frame=frame, value=value)
        
        # Second pass: Create drivers on all objects
        for obj_name, props in animation_data.items():
            if obj_name == root_object.name:
                continue  # Skip root object - it's animated directly
            
            obj = bpy.data.objects.get(obj_name)
            if not obj:
                continue
            
            # Create animation data if needed
            if not obj.animation_data:
                obj.animation_data_create()
            
            # Clean object name for property lookup
            clean_obj_name = obj_name.replace('.', '_').replace(' ', '_')
            
            # Set up drivers for each property
            for prop_name, axes in props.items():
                if prop_name not in ["location", "rotation_euler", "scale"]:
                    continue  # Skip non-transform properties
                
                for axis, keyframes in axes.items():
                    # Get the component name
                    component = ["x", "y", "z"][axis]
                    
                    # Get the property key
                    if prop_name == "location":
                        prop_key = f"{clean_obj_name}_loc_{component}"
                    elif prop_name == "rotation_euler":
                        prop_key = f"{clean_obj_name}_rot_{component}"
                    else:  # scale
                        prop_key = f"{clean_obj_name}_scale_{component}"
                    
                    # Create a driver that reads from the custom property
                    try:
                        # Remove any existing driver
                        obj.driver_remove(prop_name, axis)
                        
                        # Create new driver
                        driver = obj.driver_add(prop_name, axis).driver
                        
                        # Add variable
                        var = driver.variables.new()
                        var.name = "value"
                        var.type = 'SINGLE_PROP'
                        
                        # Target the custom property on the root object
                        var.targets[0].id = root_object
                        var.targets[0].data_path = f'["{prop_key}"]'
                        
                        # Set the expression
                        driver.expression = "value"
                        
                        if self.debug_mode:
                            self.report({'INFO'}, f"Created driver for {obj.name}.{prop_name}[{axis}] reading from {prop_key}")
                            
                    except Exception as e:
                        self.report({'WARNING'}, f"Error creating driver for {obj.name}.{prop_name}[{axis}]: {str(e)}")
        
        # Update the scene
        context.view_layer.update()
        
        # Remove original actions if not keeping them
        if not self.keep_original_actions:
            for obj in animated_objects:
                if obj != root_object and obj.animation_data and obj.animation_data.action:
                    # Remove NLA tracks first
                    if obj.animation_data.nla_tracks:
                        while obj.animation_data.nla_tracks:
                            obj.animation_data.nla_tracks.remove(obj.animation_data.nla_tracks[0])
                    
                    # Remove action
                    if obj != root_object and obj.animation_data:
                        obj.animation_data.action = None
        
        # Restore original frame
        context.scene.frame_set(original_frame)
        
        self.report({'INFO'}, f"Created combined animation with {len(custom_props_to_create)} custom properties")
        
        return {'FINISHED'}
        
    def find_animation_range(self, objects):
        """Find the overall animation range from all objects"""
        min_frame = float('inf')
        max_frame = float('-inf')
        
        for obj in objects:
            if obj.animation_data and obj.animation_data.action:
                action = obj.animation_data.action
                for fcurve in action.fcurves:
                    if len(fcurve.keyframe_points) > 0:
                        min_frame = min(min_frame, fcurve.keyframe_points[0].co[0])
                        max_frame = max(max_frame, fcurve.keyframe_points[-1].co[0])
        
        return min_frame, max_frame
    
    def find_root_object(self, objects):
        """Find the topmost parent object"""
        # First look for explicitly named root object
        for obj in objects:
            if "root" in obj.name.lower() or obj.name.lower().startswith(("player", "character", "rig")):
                return obj
        
        # If not found, look for object that contains "visual" in the name      
        for obj in objects:
            if "visual" in obj.name.lower():
                return obj
                
        # If not found, get the object with most children
        max_children = -1
        root_obj = None
        
        for obj in objects:
            # Count all descendants
            descendants = self.count_descendants(obj)
            if descendants > max_children:
                max_children = descendants
                root_obj = obj
                
        return root_obj
    
    def count_descendants(self, obj):
        """Count all descendants of an object"""
        count = len(obj.children)
        for child in obj.children:
            count += self.count_descendants(child)
        return count
    
    def get_hierarchy_objects(self, root_obj):
        """Get all objects in a hierarchy"""
        objects = [root_obj]
        for child in root_obj.children:
            objects.extend(self.get_hierarchy_objects(child))
        return objects
        
    def invoke(self, context, event):
        # Show the properties dialog when invoked
        return context.window_manager.invoke_props_dialog(self)


class CombineAnimationsPanel(bpy.types.Panel):
    bl_label = "Combine Animations"
    bl_idname = "ACTION_PT_CombineAnimations"
    bl_space_type = "DOPESHEET_EDITOR"
    bl_region_type = "UI"
    bl_category = "Tool"

    @classmethod
    def poll(cls, context):
        # Show in both NLA Editor and Dopesheet Editor
        return context.area.type in {'NLA_EDITOR', 'DOPESHEET_EDITOR'}

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.label(text=f"Version {'.'.join(map(str, bl_info['version']))}")
        
        box = layout.box()
        box.label(text="Combine Unity-exported animations")
        box.label(text="For export to Unreal Engine")
        
        # Show hierarchy info if objects are selected
        if context.selected_objects:
            selected_count = len(context.selected_objects)
            animated_count = len([obj for obj in context.selected_objects if obj.animation_data and obj.animation_data.action])
            
            info_box = layout.box()
            info_box.label(text=f"Selected: {selected_count} objects")
            info_box.label(text=f"With animations: {animated_count} objects")
        
        # Add options
        options_box = layout.box()
        options_box.label(text="Export Options:")
        
        row = options_box.row()
        row.prop(context.scene, "ca_keep_originals", text="Keep Originals")
        
        row = options_box.row()
        row.prop(context.scene, "ca_add_root_track", text="Add Root Track")
        
        row = options_box.row()
        row.prop(context.scene, "ca_frame_margin", text="Frame Margin")
        
        row = options_box.row()
        row.prop(context.scene, "ca_debug_mode", text="Debug Mode")
        
        # The main operator button
        row = layout.row()
        op = row.operator("action.combine_animations", icon="EXPORT")
        op.keep_original_actions = context.scene.ca_keep_originals
        op.add_root_track = context.scene.ca_add_root_track
        op.frame_margin = context.scene.ca_frame_margin
        op.debug_mode = context.scene.ca_debug_mode


def register():
    # Register scene properties for the panel
    bpy.types.Scene.ca_keep_originals = bpy.props.BoolProperty(
        name="Keep Original Actions",
        description="Keep the original actions after combining",
        default=True
    )
    
    bpy.types.Scene.ca_add_root_track = bpy.props.BoolProperty(
        name="Add Root Track",
        description="Add location/rotation track for the root bone (needed for Unreal)",
        default=True
    )
    
    bpy.types.Scene.ca_frame_margin = bpy.props.IntProperty(
        name="Frame Margin",
        description="Extra frames to add before and after detected animation range",
        default=5,
        min=0
    )
    
    bpy.types.Scene.ca_debug_mode = bpy.props.BoolProperty(
        name="Debug Mode",
        description="Print detailed information during process",
        default=True
    )
    
    bpy.utils.register_class(CombineAnimationsOperator)
    bpy.utils.register_class(CombineAnimationsPanel)


def unregister():
    # Remove scene properties
    del bpy.types.Scene.ca_keep_originals
    del bpy.types.Scene.ca_add_root_track
    del bpy.types.Scene.ca_frame_margin
    del bpy.types.Scene.ca_debug_mode
    
    bpy.utils.unregister_class(CombineAnimationsOperator)
    bpy.utils.unregister_class(CombineAnimationsPanel)


if __name__ == "__main__":
    register() 