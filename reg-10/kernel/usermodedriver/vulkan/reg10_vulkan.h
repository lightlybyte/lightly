/*
 * reg10_vulkan.h - Re:G 1010 minimal Vulkan ICD interface.
 *
 * Exposes the Re:G GPU as a Vulkan device. Because Re:G is a usermode
 * VM, this is a software ICD that translates Vulkan commands into Re:G
 * ISA kernel dispatches.
 */

#ifndef REG10_VULKAN_H
#define REG10_VULKAN_H

#include <vulkan/vulkan.h>
#include "reg10_driver.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Vulkan ICD entry points (the subset a minimal ICD must provide). */

PFN_vkVoidFunction reg10_vkGetInstanceProcAddr(VkInstance instance,
                                                const char *pName);

VkResult reg10_vkEnumerateInstanceExtensionProperties(
    const char *pLayerName, uint32_t *pPropertyCount,
    VkExtensionProperties *pProperties);

VkResult reg10_vkCreateInstance(const VkInstanceCreateInfo *pCreateInfo,
                                const VkAllocationCallbacks *pAllocator,
                                VkInstance *pInstance);

void reg10_vkDestroyInstance(VkInstance instance,
                             const VkAllocationCallbacks *pAllocator);

VkResult reg10_vkEnumeratePhysicalDevices(VkInstance instance,
                                          uint32_t *pPhysicalDeviceCount,
                                          VkPhysicalDevice *pPhysicalDevices);

void reg10_vkGetPhysicalDeviceProperties(VkPhysicalDevice physicalDevice,
                                         VkPhysicalDeviceProperties *pProperties);

VkResult reg10_vkCreateDevice(VkPhysicalDevice physicalDevice,
                              const VkDeviceCreateInfo *pCreateInfo,
                              const VkAllocationCallbacks *pAllocator,
                              VkDevice *pDevice);

void reg10_vkDestroyDevice(VkDevice device,
                           const VkAllocationCallbacks *pAllocator);

VkResult reg10_vkCreateSwapchainKHR(VkDevice device,
                                    const VkSwapchainCreateInfoKHR *pCreateInfo,
                                    const VkAllocationCallbacks *pAllocator,
                                    VkSwapchainKHR *pSwapchain);

void reg10_vkDestroySwapchainKHR(VkDevice device, VkSwapchainKHR swapchain,
                                 const VkAllocationCallbacks *pAllocator);

VkResult reg10_vkAcquireNextImageKHR(VkDevice device, VkSwapchainKHR swapchain,
                                     uint64_t timeout, VkSemaphore semaphore,
                                     VkFence fence, uint32_t *pImageIndex);

VkResult reg10_vkQueuePresentKHR(VkQueue queue,
                                 const VkPresentInfoKHR *pPresentInfo);

VkResult reg10_vkQueueSubmit(VkQueue queue, uint32_t submitCount,
                             const VkSubmitInfo *pSubmits, VkFence fence);

VkResult reg10_vkQueueWaitIdle(VkQueue queue);

#ifdef __cplusplus
}
#endif

#endif /* REG10_VULKAN_H */