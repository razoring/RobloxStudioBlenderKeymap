bl_info = {
    "name": "Roblox Studio Navigation",
    "author": "AI Assistant",
    "version": (1, 3, 0),
    "blender": (4, 0, 0),
    "location": "View3D > N-Panel > Roblox Nav",
    "description": "Roblox Studio Navigation: Always-on WASD, RMB Mouselook, Left Drag Box Select, and F to center.",
    "category": "3D View",
}

import bpy
import urllib.request
import threading
import os
import re
from mathutils import Vector, Quaternion
from bpy.app.handlers import persistent

class VIEW3D_OT_roblox_nav_modal(bpy.types.Operator):
    bl_idname = "view3d.roblox_nav_modal"
    bl_label = "Roblox Navigation Background Worker"
    
    _timer = None
    _keys = None
    _toolSet = False
    _hasMoved = False
    _wasSelected = False
    _prevSelectTool = "builtin.select_box"
    
    @classmethod
    def poll(cls, context):
        return context.window_manager is not None
        
    def invoke(self, context, event):
        if getattr(context.window_manager, "roblox_nav_running", False):
            return {'CANCELLED'}
            
        self._keys = {
            'W': False, 'A': False, 'S': False, 'D': False, 
            'E': False, 'Q': False, 'RMB': False, 'SHIFT': False
        }
        self._toolSet = False
        self._hasMoved = False
        self._wasSelected = len(context.selected_objects) > 0
        self._prevSelectTool = "builtin.select_box"
        self._scaleFixed = False
        self._mouseX = getattr(event, "mouse_x", 0)
        self._mouseY = getattr(event, "mouse_y", 0)
        
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.016, window=context.window)
        wm.modal_handler_add(self)
        wm.roblox_nav_running = True
        
        return {'RUNNING_MODAL'}
        
    def modal(self, context, event):
        #allow safe exit via ui button and handle unregistering
        if not getattr(context.window_manager, "roblox_nav_running", False):
            if self._keys.get('RMB', False):
                context.window.cursor_modal_restore()
            context.window_manager.event_timer_remove(self._timer)
            return {'FINISHED'}

        #safety catch: if user alt-tabs or window loses focus, reset all keys
        if event.type == 'WINDOW_DEACTIVATE':
            if self._keys.get('RMB', False):
                context.window.cursor_modal_restore()
            for k in self._keys:
                self._keys[k] = False
            return {'PASS_THROUGH'}

        #global release tracking
        if event.value == 'RELEASE':
            if event.type in {'W', 'A', 'S', 'D', 'E', 'Q'}:
                self._keys[event.type] = False
                if self._hasMoved and not any(self._keys[k] for k in ('W', 'A', 'S', 'D', 'E', 'Q', 'RMB')):
                    bpy.ops.ed.undo_push(message="Roblox Walk")
                    self._hasMoved = False
                return {'RUNNING_MODAL'}
            elif event.type == 'F':
                return {'RUNNING_MODAL'} #swallow f release so it doesn't trigger native blender tools
            elif event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
                self._keys['SHIFT'] = False
                return {'PASS_THROUGH'}
            elif event.type == 'RIGHTMOUSE':
                if self._keys['RMB']:
                    self._keys['RMB'] = False
                    context.window.cursor_modal_restore()
                    if self._hasMoved and not any(self._keys[k] for k in ('W', 'A', 'S', 'D', 'E', 'Q')):
                        bpy.ops.ed.undo_push(message="Roblox Mouselook")
                        self._hasMoved = False
                    return {'RUNNING_MODAL'}

        #viewport-specific actions
        hoveredArea = None
        if context.screen:
            for area in context.screen.areas:
                if area.x <= event.mouse_x <= area.x + area.width and area.y <= event.mouse_y <= area.y + area.height:
                    hoveredArea = area
                    break

        if hoveredArea and hoveredArea.type == 'VIEW_3D':
            space = hoveredArea.spaces.active
            rv3d = space.region_3d
            
            #extract the correct window region for context overrides
            windowRegion = next((r for r in hoveredArea.regions if r.type == 'WINDOW'), None)
            
            #enforce scale cage as the default scale tool on load
            if not self._scaleFixed and windowRegion:
                with context.temp_override(window=context.window, area=hoveredArea, region=windowRegion):
                    try:
                        bpy.ops.wm.tool_set_by_id(name="builtin.scale_cage")
                        self._scaleFixed = True
                    except Exception:
                        pass
                return {'PASS_THROUGH'}

            #enforce box select as the active tool (left click drag = select)
            if not self._toolSet and windowRegion:
                with context.temp_override(window=context.window, area=hoveredArea, region=windowRegion):
                    try:
                        if self._wasSelected:
                            bpy.ops.wm.tool_set_by_id(name="builtin.select")
                        else:
                            bpy.ops.wm.tool_set_by_id(name="builtin.select_box")
                        self._toolSet = True
                    except Exception:
                        pass
                        
            #dynamic selection tool swapping
            if windowRegion and event.type in {'TIMER', 'MOUSEMOVE'}:
                try:
                    currentTool = context.workspace.tools.from_space_view3d_mode(context.mode).idname
                except AttributeError:
                    currentTool = ""

                hasSelection = len(context.selected_objects) > 0
                
                if hasSelection and not self._wasSelected:
                    if currentTool in {'builtin.select_box', 'builtin.select_circle', 'builtin.select_lasso'}:
                        self._prevSelectTool = currentTool
                    with context.temp_override(window=context.window, area=hoveredArea, region=windowRegion):
                        try:
                            bpy.ops.wm.tool_set_by_id(name="builtin.select")
                        except Exception:
                            pass
                    self._wasSelected = True
                elif not hasSelection and self._wasSelected:
                    with context.temp_override(window=context.window, area=hoveredArea, region=windowRegion):
                        try:
                            bpy.ops.wm.tool_set_by_id(name=self._prevSelectTool)
                        except Exception:
                            pass
                    self._wasSelected = False
                elif not hasSelection:
                    if currentTool in {'builtin.select_box', 'builtin.select_circle', 'builtin.select_lasso'}:
                        self._prevSelectTool = currentTool
            
            #press tracking
            if event.value == 'PRESS':
                #sync rv3d to camera on initial press to prevent snapping
                if event.type in {'W', 'A', 'S', 'D', 'E', 'Q', 'RIGHTMOUSE'}:
                    if not any(self._keys[k] for k in ('W', 'A', 'S', 'D', 'E', 'Q', 'RMB')):
                        if rv3d.view_perspective == 'CAMERA' and space.camera:
                            rv3d.view_rotation = space.camera.rotation_euler.to_quaternion()
                            rv3d.view_location = space.camera.location - rv3d.view_rotation @ Vector((0.0, 0.0, rv3d.view_distance))

                #center camera on selection ('f' key)
                if event.type == 'F':
                    if windowRegion:
                        with context.temp_override(window=context.window, area=hoveredArea, region=windowRegion):
                            bpy.ops.view3d.view_selected()
                    return {'RUNNING_MODAL'}
                    
                #wasd movement
                elif event.type in {'W', 'A', 'S', 'D', 'E', 'Q'}:
                    self._keys[event.type] = True
                    return {'RUNNING_MODAL'} 
                elif event.type in {'LEFT_SHIFT', 'RIGHT_SHIFT'}:
                    self._keys['SHIFT'] = True
                    return {'PASS_THROUGH'}
                elif event.type == 'RIGHTMOUSE':
                    if not self._keys['RMB']:
                        self._keys['RMB'] = True
                        self._mouseX = event.mouse_x
                        self._mouseY = event.mouse_y
                        context.window.cursor_modal_set('NONE')
                        return {'RUNNING_MODAL'}

            #mouselook implementation
            if event.type == 'MOUSEMOVE':
                if self._keys['RMB']:
                    dx = event.mouse_x - self._mouseX
                    dy = event.mouse_y - self._mouseY
                    self._mouseX = event.mouse_x
                    self._mouseY = event.mouse_y
                    
                    if dx != 0 or dy != 0:
                        self._hasMoved = True
                        if rv3d.view_perspective == 'ORTHO':
                            rv3d.view_perspective = 'PERSP'
                            
                        sensitivity = 0.0035
                        
                        cameraPos = rv3d.view_location + rv3d.view_rotation @ Vector((0, 0, rv3d.view_distance))
                        qYaw = Quaternion((0.0, 0.0, 1.0), -dx * sensitivity)
                        qPitch = Quaternion((1.0, 0.0, 0.0), dy * sensitivity)
                        
                        currentRot = rv3d.view_rotation
                        newRot = qYaw @ currentRot @ qPitch
                        
                        upVector = newRot @ Vector((0.0, 1.0, 0.0))
                        if upVector.z >= 0.0:
                            rv3d.view_rotation = newRot
                            rv3d.view_location = cameraPos - newRot @ Vector((0, 0, rv3d.view_distance))
                        else:
                            rv3d.view_rotation = qYaw @ currentRot 
                            rv3d.view_location = cameraPos - (qYaw @ currentRot) @ Vector((0, 0, rv3d.view_distance))
                            
                        space = hoveredArea.spaces.active
                        if rv3d.view_perspective == 'CAMERA' and space.camera:
                            space.camera.rotation_euler = rv3d.view_rotation.to_euler()
                            space.camera.location = rv3d.view_location + rv3d.view_rotation @ Vector((0, 0, rv3d.view_distance))
                            
                    #infinite mouselook cursor wrap
                    centerX = hoveredArea.x + hoveredArea.width // 2
                    centerY = hoveredArea.y + hoveredArea.height // 2
                    
                    if abs(event.mouse_x - centerX) > 50 or abs(event.mouse_y - centerY) > 50:
                        context.window.cursor_warp(centerX, centerY)
                        self._mouseX = centerX
                        self._mouseY = centerY
                        
                    hoveredArea.tag_redraw()
                    return {'RUNNING_MODAL'}

            #smooth movement application
            if event.type == 'TIMER':
                moveVec = Vector((0.0, 0.0, 0.0))
                
                if self._keys['W']: moveVec.z -= 1
                if self._keys['S']: moveVec.z += 1
                if self._keys['A']: moveVec.x -= 1
                if self._keys['D']: moveVec.x += 1
                if self._keys['E']: moveVec.y += 1
                if self._keys['Q']: moveVec.y -= 1
                
                if moveVec.length > 0:
                    self._hasMoved = True
                    moveVec.normalize()
                    speed = 0.1
                    if self._keys['SHIFT']:
                        speed *= 0.1 
                        
                    delta = rv3d.view_rotation @ (moveVec * speed)
                    rv3d.view_location += delta
                    
                    space = hoveredArea.spaces.active
                    if rv3d.view_perspective == 'CAMERA' and space.camera:
                        space.camera.location += delta
                        
                    hoveredArea.tag_redraw()
                    
                return {'PASS_THROUGH'}

        return {'PASS_THROUGH'}

class VIEW3D_OT_roblox_nav_start(bpy.types.Operator):
    bl_idname = "view3d.roblox_nav_start"
    bl_label = "Start Roblox Nav"
    def execute(self, context):
        if not getattr(context.window_manager, "roblox_nav_running", False):
            bpy.ops.view3d.roblox_nav_modal('INVOKE_DEFAULT')
        return {'FINISHED'}

class VIEW3D_OT_roblox_nav_stop(bpy.types.Operator):
    bl_idname = "view3d.roblox_nav_stop"
    bl_label = "Stop Roblox Nav"
    def execute(self, context):
        context.window_manager.roblox_nav_running = False
        return {'FINISHED'}

class VIEW3D_PT_roblox_nav_panel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Roblox Nav'
    bl_label = "Roblox Navigation"

    def draw(self, context):
        layout = self.layout
        if getattr(context.window_manager, "roblox_nav_running", False):
            layout.operator("view3d.roblox_nav_stop", text="Disable Navigation", icon='CANCEL')
            layout.label(text="Status: ACTIVE", icon='PLAY')
        else:
            layout.operator("view3d.roblox_nav_start", text="Enable Navigation", icon='PLAY')
            layout.label(text="Status: OFF", icon='PAUSE')

def _check_update(): # check for updates
    try:
        url = "https://raw.githubusercontent.com/razoring/RobloxStudioBlenderKeymap/main/BlenderRobloxKeymap.py"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                patch = response.read().decode('utf-8')
                match = re.search(r'"version"\s*:\s*\((\d+),\s*(\d+),\s*(\d+)\)', patch)
                if match:
                    remote_ver = tuple(map(int, match.groups()))
                    local_ver = bl_info.get("version", (0, 0, 0))
                    if remote_ver > local_ver:
                        with open(os.path.realpath(__file__), 'w', encoding='utf-8') as f:
                            f.write(patch)
    except Exception:
        pass

def auto_start_nav():
    if not getattr(bpy.context.window_manager, "roblox_nav_running", False):
        bpy.ops.view3d.roblox_nav_start()
    return None

@persistent
def load_handler(dummy):
    bpy.app.timers.register(auto_start_nav, first_interval=1.5)

def register():
    threading.Thread(target=_check_update, daemon=True).start()
    bpy.utils.register_class(VIEW3D_OT_roblox_nav_modal)
    bpy.utils.register_class(VIEW3D_OT_roblox_nav_start)
    bpy.utils.register_class(VIEW3D_OT_roblox_nav_stop)
    bpy.utils.register_class(VIEW3D_PT_roblox_nav_panel)
    bpy.types.WindowManager.roblox_nav_running = bpy.props.BoolProperty(default=False)
    if load_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(load_handler)
    bpy.app.timers.register(auto_start_nav, first_interval=0.5)

def unregister():
    if getattr(bpy.context, "window_manager", None):
        bpy.context.window_manager.roblox_nav_running = False

    bpy.utils.unregister_class(VIEW3D_OT_roblox_nav_modal)
    bpy.utils.unregister_class(VIEW3D_OT_roblox_nav_start)
    bpy.utils.unregister_class(VIEW3D_OT_roblox_nav_stop)
    bpy.utils.unregister_class(VIEW3D_PT_roblox_nav_panel)
    if hasattr(bpy.types.WindowManager, "roblox_nav_running"):
        del bpy.types.WindowManager.roblox_nav_running
    if load_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_handler)

if __name__ == "__main__": register()