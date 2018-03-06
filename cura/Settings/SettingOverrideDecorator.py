# Copyright (c) 2016 Ultimaker B.V.
# Cura is released under the terms of the LGPLv3 or higher.

import copy

from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator
from UM.Scene.SceneNodeDecorator import SceneNodeDecorator
from UM.Signal import Signal, signalemitter
from UM.Settings.InstanceContainer import InstanceContainer
from UM.Settings.ContainerRegistry import ContainerRegistry
from UM.Logger import Logger
from UM.Settings.Validator import ValidatorState
from PyQt5.QtCore import QTimer
from UM.Application import Application

from cura.Settings.PerObjectContainerStack import PerObjectContainerStack
from cura.Settings.ExtruderManager import ExtruderManager

##  A decorator that adds a container stack to a Node. This stack should be queried for all settings regarding
#   the linked node. The Stack in question will refer to the global stack (so that settings that are not defined by
#   this stack still resolve.
@signalemitter
class SettingOverrideDecorator(SceneNodeDecorator):
    ##  Event indicating that the user selected a different extruder.
    activeExtruderChanged = Signal()

    ##  Non-printing meshes
    #
    #   If these settings are True for any mesh, the mesh does not need a convex hull,
    #   and is sent to the slicer regardless of whether it fits inside the build volume.
    #   Note that Support Mesh is not in here because it actually generates
    #   g-code in the volume of the mesh.
    _non_printing_mesh_settings = {"anti_overhang_mesh", "infill_mesh", "cutting_mesh"}

    def __init__(self):
        super().__init__()
        self._stack = PerObjectContainerStack(stack_id = "per_object_stack_" + str(id(self)))
        self._stack.setDirty(False)  # This stack does not need to be saved.
        self._stack.addContainer(InstanceContainer(container_id = "SettingOverrideInstanceContainer"))
        self._extruder_stack = ExtruderManager.getInstance().getExtruderStack(0).getId()

        self._is_non_printing_mesh = False
        self._error_check_timer = QTimer()
        self._error_check_timer.setInterval(250)
        self._error_check_timer.setSingleShot(True)
        self._error_check_timer.timeout.connect(self._checkStackForErrors)

        self._stack.propertyChanged.connect(self._onSettingChanged)

        Application.getInstance().getContainerRegistry().addContainer(self._stack)

        Application.getInstance().globalContainerStackChanged.connect(self._updateNextStack)
        self.activeExtruderChanged.connect(self._updateNextStack)
        self._updateNextStack()

    def __deepcopy__(self, memo):
        ## Create a fresh decorator object
        deep_copy = SettingOverrideDecorator()
        ## Copy the instance
        instance_container = copy.deepcopy(self._stack.getContainer(0), memo)

        ## Set the copied instance as the first (and only) instance container of the stack.
        deep_copy._stack.replaceContainer(0, instance_container)

        # Properly set the right extruder on the copy
        deep_copy.setActiveExtruder(self._extruder_stack)

        # use value from the stack because there can be a delay in signal triggering and "_is_non_printing_mesh"
        # has not been updated yet.
        deep_copy._is_non_printing_mesh = any(bool(self._stack.getProperty(setting, "value")) for setting in self._non_printing_mesh_settings)

        return deep_copy

    ##  Gets the currently active extruder to print this object with.
    #
    #   \return An extruder's container stack.
    def getActiveExtruder(self):
        return self._extruder_stack

    ##  Gets the signal that emits if the active extruder changed.
    #
    #   This can then be accessed via a decorator.
    def getActiveExtruderChangedSignal(self):
        return self.activeExtruderChanged

    ##  Gets the currently active extruders position
    #
    #   \return An extruder's position, or None if no position info is available.
    def getActiveExtruderPosition(self):
        containers = ContainerRegistry.getInstance().findContainers(id = self.getActiveExtruder())
        if containers:
            container_stack = containers[0]
            return container_stack.getMetaDataEntry("position", default=None)

    def isNonPrintingMesh(self):
        return self._is_non_printing_mesh

    def _onSettingChanged(self, instance, property_name): # Reminder: 'property' is a built-in function
        # Trigger slice/need slicing if the value has changed.
        if property_name == "value":
            self._is_non_printing_mesh = any(bool(self._stack.getProperty(setting, "value")) for setting in self._non_printing_mesh_settings)
            if not self._is_non_printing_mesh:
                # self._error_check_timer.start()
                self._checkStackForErrors()
        Application.getInstance().getBackend().needsSlicing()
        Application.getInstance().getBackend().tickle()

    def _checkStackForErrors(self):
        hasErrors = False;
        for key in self._stack.getAllKeys():
            validation_state = self._stack.getProperty(key, "validationState")
            if validation_state in (ValidatorState.Exception, ValidatorState.MaximumError, ValidatorState.MinimumError):
                Logger.log("w", "Setting Per Object %s is not valid.", key)
                hasErrors = True
                break
        Application.getInstance().getObjectsModel().setStacksHaveErrors(hasErrors)

    ##  Makes sure that the stack upon which the container stack is placed is
    #   kept up to date.
    def _updateNextStack(self):
        if self._extruder_stack:
            extruder_stack = ContainerRegistry.getInstance().findContainerStacks(id = self._extruder_stack)
            if extruder_stack:
                if self._stack.getNextStack():
                    old_extruder_stack_id = self._stack.getNextStack().getId()
                else:
                    old_extruder_stack_id = ""

                self._stack.setNextStack(extruder_stack[0])
                # Trigger slice/need slicing if the extruder changed.
                if self._stack.getNextStack().getId() != old_extruder_stack_id:
                    Application.getInstance().getBackend().needsSlicing()
                    Application.getInstance().getBackend().tickle()
            else:
                Logger.log("e", "Extruder stack %s below per-object settings does not exist.", self._extruder_stack)
        else:
            self._stack.setNextStack(Application.getInstance().getGlobalContainerStack())

    ##  Changes the extruder with which to print this node.
    #
    #   \param extruder_stack_id The new extruder stack to print with.
    def setActiveExtruder(self, extruder_stack_id):
        self._extruder_stack = extruder_stack_id
        self._updateNextStack()
        ExtruderManager.getInstance().resetSelectedObjectExtruders()
        self.activeExtruderChanged.emit()

    def getStack(self):
        return self._stack
