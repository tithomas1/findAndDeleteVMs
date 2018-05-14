# Copyright (c) 2017 Cisco and/or its affiliates.
#
# This software is licensed to you under the terms of the Cisco Sample
# Code License, Version 1.0 (the "License"). You may obtain a copy of the
# License at
#
#                https://developer.cisco.com/docs/licenses
#
# All use of the material herein must be in accordance with the terms of
# the License. All rights not expressly granted by the License are
# reserved. Unless required by applicable law or agreed to separately in
# writing, software distributed under the License is distributed on an "AS
# IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
# or implied.
#
# Author:  Tim Thomas
# Created: 2/18/17
#
# Find and destroy a given set of VMs on a vCenter instance, potentially limited
# a particular folder to make it more specific
#
# Reference: https://github.com/vmware/pyvmomi/blob/master/docs/vim/VirtualMachine.rst


import atexit
import argparse

from pyVim import connect
from pyVmomi import vim, vmodl


DEFAULT_PORT = 443
STATE_POWERON = 'poweredOn'
STATE_POWEROFF = 'poweredOff'


def getArgs():
    parser = argparse.ArgumentParser(description="Find and delete specific VMs in vCenter instance")
    parser.add_argument('-a', '--addr', required=True, action='store', help='vCenter IP')
    parser.add_argument('-o', '--port', required=False, action='store', help='Remote port', type=int, default=DEFAULT_PORT)
    parser.add_argument('-u', '--user', required=True, action='store', help='Username')
    parser.add_argument('-p', '--password', required=True, action='store', help='Password')
    parser.add_argument('-f', '--folder', required=False, action='store', help='Target folder', default=None)
    parser.add_argument('-v', '--verbose', required=False, action='store_true', help='Verbose output', default=False)
    parser.add_argument('vmList', metavar='VM', nargs='+', help='List of VMs to delete')
    return parser.parse_args()


# The following routine is taken more or less from:
#
# https://github.com/vmware/pyvmomi/blob/master/sample/poweronvm.py

def waitForTasks(serviceInstance, tasks):
    """
    Given the service instance and list of tasks, it returns after all the
    tasks are complete
    """

    pc = serviceInstance.content.propertyCollector

    taskList = [str(task) for task in tasks]

    # Create filter
    objSpecs = [vmodl.query.PropertyCollector.ObjectSpec(obj=task)
                for task in tasks]
    propSpec = vmodl.query.PropertyCollector.PropertySpec(type=vim.Task, pathSet=[], all=True)
    filterSpec = vmodl.query.PropertyCollector.FilterSpec()
    filterSpec.objectSet = objSpecs
    filterSpec.propSet = [propSpec]
    filter = pc.CreateFilter(filterSpec, True)

    try:
        version, state = None, None

        # Loop looking for updates till the state moves to completed
        while len(taskList):
            update = pc.WaitForUpdates(version)
            for filterSet in update.filterSet:
                for objSet in filterSet.objectSet:
                    task = objSet.obj
                    for change in objSet.changeSet:
                        if change.name == 'info':
                            state = change.val.state
                        elif change.name == 'info.state':
                            state = change.val
                        else:
                            continue

                        if not str(task) in taskList:
                            continue

                        if state == vim.TaskInfo.State.success:
                            # Remove task from taskList
                            taskList.remove(str(task))
                        elif state == vim.TaskInfo.State.error:
                            raise task.info.error
            # Move to next version
            version = update.version
    finally:
        if filter:
            filter.Destroy()


def powerDownAndDelete(serviceInstance, folder, vmList, verbose):
    folderName = "{}/".format(folder.name) if folder is not None else ""

    # First power down any VMs in the list that happen to be on

    powerTaskList = []
    for virtualMachine in vmList:
        powerState = virtualMachine.runtime.powerState
        if powerState == STATE_POWERON:
            if verbose:
                print("Powering off {}{}".format(folderName, virtualMachine.name))
            powerTaskList.append(virtualMachine.PowerOffVM_Task())

    if powerTaskList:
        waitForTasks(serviceInstance, powerTaskList)

    # Circle back around and delete the VM instances

    deleteTaskList = []
    for virtualMachine in vmList:
        if verbose:
            print("Destroying {}{}".format(folderName, virtualMachine.name))
        deleteTaskList.append(virtualMachine.Destroy_Task())

    if deleteTaskList:
        waitForTasks(serviceInstance, deleteTaskList)


def findTargetVMs():
    args = getArgs()

    """
    Turning off SSL verification here is required due to the vCenter instance having
    a self-signed cert and a change/enhancement in Python 2.7.9+. This also requires
    pyVmomi 6.0+
    """

    try:
        import ssl
        context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        context.verify_mode = ssl.CERT_NONE

        serviceInstance = connect.SmartConnect(host=args.addr, user=args.user, pwd=args.password,
                                               port=args.port, sslContext=context)

        atexit.register(connect.Disconnect, serviceInstance)

        if args.verbose:
            print("Authenticated to vCenter at {}".format(args.addr), end=', ')
            print("current session id: {}\n".format(serviceInstance.content.sessionManager.currentSession.key))

        # First retrieve the list of VM folders if a folder was specified

        content = serviceInstance.RetrieveContent()
        contentRoot = content.rootFolder
        recursive = True
        targetFolder = None

        if args.folder is not None:
            containerView = content.viewManager.CreateContainerView(content.rootFolder, [vim.Folder], recursive)

            for object in containerView.view:
                assert isinstance(object, vim.Folder)
                if object.name == args.folder:
                    targetFolder = object
                    break

            if targetFolder is None:
                print("Unable to locate target folder '{}'".format(args.folder))
                return -1

            # Now we know the root of the subsequent VM search is the target folder

            contentRoot = targetFolder
            recursive = False
            containerView.Destroy()

        # Find all the VMs

        targetVMList = []
        targetView = content.viewManager.CreateContainerView(contentRoot, [vim.VirtualMachine], recursive)

        for object in targetView.view:
            assert isinstance(object, vim.VirtualMachine)
            if object.name in args.vmList:
                targetVMList.append(object)

        if targetVMList == []:
            print("Unable to locate any of the target VMs")
            return -1

        powerDownAndDelete(serviceInstance, targetFolder, targetVMList, args.verbose)
        targetView.Destroy()

    except vmodl.MethodFault as error:
        print("Caught vmodl fault: " + error.msg)
        return -1

    return 0


if __name__ == "__main__":
    findTargetVMs()