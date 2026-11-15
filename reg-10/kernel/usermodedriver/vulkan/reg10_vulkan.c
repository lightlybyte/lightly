/*
 * reg10_vulkan.c - Re:G 1010 minimal Vulkan ICD.
 *
 * A software ICD that presents the Re:G GPU VM as a Vulkan device.
 * Commands (draw, copy, present) are translated into ReG ISA kernel
 * dispatches via the usermode driver API.
 */

#include "reg10_vulkan.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* ------------------------------------------------------------------ */
/* Minimal object model                                               */
/* ------------------------------------------------------------------ */

typedef struct reg10_vk_instance {
    VkInstance handle;
} reg10_vk_instance_t;

typedef struct reg10_vk_physical_device {
    VkPhysicalDevice handle;
    reg10_device_t *dev;
} reg10_vk_physical_device_t;

typedef struct reg10_vk_device {
    VkDevice handle;
    reg10_device_t *dev;
    reg10_context_t *ctx;
    reg10_queue_t *queue;
} reg10_vk_device_t;

typedef struct reg10_vk_swapchain {
    VkSwapchainKHR handle;
    reg10_vk_device_t *dev;
    uint32_t width, height;
    VkImage images[8];
    uint32_t image_count;
    uint32_t current_image;
} reg10_vk_swapchain_t;

typedef struct reg10_vk_image {
    VkImage handle;
    uint32_t width, height;
} reg10_vk_image_t;

/* ------------------------------------------------------------------ */
/* Instance                                                           */
/* ------------------------------------------------------------------ */

static VkResult reg10_vk_create_instance_internal(VkInstance *pInstance)
{
    reg10_vk_instance_t *inst = calloc(1, sizeof(*inst));
    if (!inst) return VK_ERROR_OUT_OF_HOST_MEMORY;
    inst->handle = (VkInstance)inst;
    *pInstance = (VkInstance)inst;
    return VK_SUCCESS;
}

PFN_vkVoidFunction reg10_vkGetInstanceProcAddr(VkInstance instance,
                                                const char *pName)
{
    (void)instance;
    if (!pName) return NULL;
    if (!strcmp(pName, "vkEnumerateInstanceExtensionProperties"))
        return (PFN_vkVoidFunction)reg10_vkEnumerateInstanceExtensionProperties;
    if (!strcmp(pName, "vkCreateInstance"))
        return (PFN_vkVoidFunction)reg10_vkCreateInstance;
    if (!strcmp(pName, "vkDestroyInstance"))
        return (PFN_vkVoidFunction)reg10_vkDestroyInstance;
    if (!strcmp(pName, "vkEnumeratePhysicalDevices"))
        return (PFN_vkVoidFunction)reg10_vkEnumeratePhysicalDevices;
    if (!strcmp(pName, "vkGetPhysicalDeviceProperties"))
        return (PFN_vkVoidFunction)reg10_vkGetPhysicalDeviceProperties;
    if (!strcmp(pName, "vkCreateDevice"))
        return (PFN_vkVoidFunction)reg10_vkCreateDevice;
    if (!strcmp(pName, "vkDestroyDevice"))
        return (PFN_vkVoidFunction)reg10_vkDestroyDevice;
    if (!strcmp(pName, "vkCreateSwapchainKHR"))
        return (PFN_vkVoidFunction)reg10_vkCreateSwapchainKHR;
    if (!strcmp(pName, "vkDestroySwapchainKHR"))
        return (PFN_vkVoidFunction)reg10_vkDestroySwapchainKHR;
    if (!strcmp(pName, "vkAcquireNextImageKHR"))
        return (PFN_vkVoidFunction)reg10_vkAcquireNextImageKHR;
    if (!strcmp(pName, "vkQueuePresentKHR"))
        return (PFN_vkVoidFunction)reg10_vkQueuePresentKHR;
    if (!strcmp(pName, "vkQueueSubmit"))
        return (PFN_vkVoidFunction)reg10_vkQueueSubmit;
    if (!strcmp(pName, "vkQueueWaitIdle"))
        return (PFN_vkVoidFunction)reg10_vkQueueWaitIdle;
    return NULL;
}

VkResult reg10_vkEnumerateInstanceExtensionProperties(
    const char *pLayerName, uint32_t *pPropertyCount,
    VkExtensionProperties *pProperties)
{
    (void)pLayerName;
    static VkExtensionProperties exts[] = {
        {"VK_KHR_surface", VK_MAKE_API_VERSION(0, 1, 0, 0)},
        {"VK_KHR_win32_surface", VK_MAKE_API_VERSION(0, 1, 0, 0)},
    };
    if (!pPropertyCount) return VK_ERROR_INITIALIZATION_FAILED;
    if (!pProperties) {
        *pPropertyCount = 2;
        return VK_SUCCESS;
    }
    uint32_t copy = *pPropertyCount < 2 ? *pPropertyCount : 2;
    memcpy(pProperties, exts, copy * sizeof(VkExtensionProperties));
    *pPropertyCount = copy;
    return VK_SUCCESS;
}

VkResult reg10_vkCreateInstance(const VkInstanceCreateInfo *pCreateInfo,
                                const VkAllocationCallbacks *pAllocator,
                                VkInstance *pInstance)
{
    (void)pCreateInfo; (void)pAllocator;
    return reg10_vk_create_instance_internal(pInstance);
}

void reg10_vkDestroyInstance(VkInstance instance,
                             const VkAllocationCallbacks *pAllocator)
{
    (void)pAllocator;
    if (!instance) return;
    free((void*)instance);
}

/* ------------------------------------------------------------------ */
/* Physical devices                                                   */
/* ------------------------------------------------------------------ */

VkResult reg10_vkEnumeratePhysicalDevices(VkInstance instance,
                                          uint32_t *pPhysicalDeviceCount,
                                          VkPhysicalDevice *pPhysicalDevices)
{
    (void)instance;
    if (!pPhysicalDeviceCount) return VK_ERROR_INITIALIZATION_FAILED;
    if (!pPhysicalDevices) {
        *pPhysicalDeviceCount = 1;
        return VK_SUCCESS;
    }
    *pPhysicalDeviceCount = 1;
    pPhysicalDevices[0] = (VkPhysicalDevice)(uintptr_t)1;
    return VK_SUCCESS;
}

void reg10_vkGetPhysicalDeviceProperties(VkPhysicalDevice physicalDevice,
                                         VkPhysicalDeviceProperties *pProperties)
{
    (void)physicalDevice;
    memset(pProperties, 0, sizeof(*pProperties));
    pProperties->vendorID = 0x1234;
    pProperties->deviceID = 0x1010;
    pProperties->deviceType = VK_PHYSICAL_DEVICE_TYPE_CPU;
    snprintf(pProperties->deviceName, sizeof(pProperties->deviceName),
             "Re:G 1010 (usermode VM)");
    pProperties->apiVersion = VK_MAKE_API_VERSION(0, 1, 0, 0);
    pProperties->driverVersion = 1;
}

/* ------------------------------------------------------------------ */
/* Device                                                             */
/* ------------------------------------------------------------------ */

VkResult reg10_vkCreateDevice(VkPhysicalDevice physicalDevice,
                              const VkDeviceCreateInfo *pCreateInfo,
                              const VkAllocationCallbacks *pAllocator,
                              VkDevice *pDevice)
{
    (void)physicalDevice; (void)pCreateInfo; (void)pAllocator;
    reg10_vk_device_t *dev = calloc(1, sizeof(*dev));
    if (!dev) return VK_ERROR_OUT_OF_HOST_MEMORY;

    /* Open Re:G device */
    reg10_device_info_t info;
    reg10_enumerate_devices(&info, 1);
    dev->dev = reg10_device_open(0);
    dev->ctx = reg10_context_create(dev->dev);
    dev->queue = reg10_context_get_queue(dev->ctx);

    dev->handle = (VkDevice)dev;
    *pDevice = (VkDevice)dev;
    return VK_SUCCESS;
}

void reg10_vkDestroyDevice(VkDevice device,
                           const VkAllocationCallbacks *pAllocator)
{
    (void)pAllocator;
    if (!device) return;
    reg10_vk_device_t *dev = (reg10_vk_device_t *)device;
    if (dev->ctx) reg10_context_destroy(dev->ctx);
    if (dev->dev) reg10_device_close(dev->dev);
    free(dev);
}

/* ------------------------------------------------------------------ */
/* Swapchain                                                          */
/* ------------------------------------------------------------------ */

VkResult reg10_vkCreateSwapchainKHR(VkDevice device,
                                    const VkSwapchainCreateInfoKHR *pCreateInfo,
                                    const VkAllocationCallbacks *pAllocator,
                                    VkSwapchainKHR *pSwapchain)
{
    (void)pAllocator;
    reg10_vk_device_t *dev = (reg10_vk_device_t *)device;
    reg10_vk_swapchain_t *sc = calloc(1, sizeof(*sc));
    if (!sc) return VK_ERROR_OUT_OF_HOST_MEMORY;

    sc->dev = dev;
    sc->width = pCreateInfo->imageExtent.width;
    sc->height = pCreateInfo->imageExtent.height;
    sc->image_count = pCreateInfo->minImageCount > 8 ? 8 : pCreateInfo->minImageCount;
    if (sc->image_count == 0) sc->image_count = 1;
    sc->current_image = 0;

    /* Configure Re:G framebuffer */
    reg10_device_set_framebuffer(dev->dev, sc->width, sc->height);

    sc->handle = (VkSwapchainKHR)sc;
    *pSwapchain = (VkSwapchainKHR)sc;
    return VK_SUCCESS;
}

void reg10_vkDestroySwapchainKHR(VkDevice device, VkSwapchainKHR swapchain,
                                 const VkAllocationCallbacks *pAllocator)
{
    (void)pAllocator;
    if (!swapchain) return;
    free((reg10_vk_swapchain_t*)swapchain);
}

VkResult reg10_vkAcquireNextImageKHR(VkDevice device, VkSwapchainKHR swapchain,
                                     uint64_t timeout, VkSemaphore semaphore,
                                     VkFence fence, uint32_t *pImageIndex)
{
    (void)device; (void)timeout; (void)semaphore; (void)fence;
    reg10_vk_swapchain_t *sc = (reg10_vk_swapchain_t*)swapchain;
    *pImageIndex = sc->current_image % sc->image_count;
    sc->current_image++;
    return VK_SUCCESS;
}

/* ------------------------------------------------------------------ */
/* Queue submission / present                                         */
/* ------------------------------------------------------------------ */

VkResult reg10_vkQueueSubmit(VkQueue queue, uint32_t submitCount,
                             const VkSubmitInfo *pSubmits, VkFence fence)
{
    (void)queue; (void)fence;
    for (uint32_t i = 0; i < submitCount; i++) {
        const VkSubmitInfo *si = &pSubmits[i];
        (void)si;
    }
    return VK_SUCCESS;
}

VkResult reg10_vkQueuePresentKHR(VkQueue queue,
                                 const VkPresentInfoKHR *pPresentInfo)
{
    (void)queue;
    if (!pPresentInfo) return VK_ERROR_INITIALIZATION_FAILED;
    for (uint32_t i = 0; i < pPresentInfo->swapchainCount; i++) {
        reg10_vk_swapchain_t *sc =
            (reg10_vk_swapchain_t*)pPresentInfo->pSwapchains[i];
        if (sc && sc->dev && sc->dev->queue)
            reg10_queue_present(sc->dev->queue);
    }
    return VK_SUCCESS;
}

VkResult reg10_vkQueueWaitIdle(VkQueue queue)
{
    (void)queue;
    return VK_SUCCESS;
}

