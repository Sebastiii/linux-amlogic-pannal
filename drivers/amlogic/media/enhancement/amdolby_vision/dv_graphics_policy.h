/* SPDX-License-Identifier: GPL-2.0 */
#ifndef DV_GRAPHICS_POLICY_H
#define DV_GRAPHICS_POLICY_H

#define DV_GFX_CAPABILITY "[p3i-gfxref:2]"
/* Fixed transport starts at byte 19800: reserved[0] is at byte 19796. */
#define DV_GFX_REQUEST_SLOT 1
#define DV_GFX_ENABLE_SLOT 2
#define DV_GFX_ACK_SLOT 3
#define DV_GFX_FLAGS_SLOT 4
#define DV_GFX_RETRY_SLOT 5
#define DV_GFX_REQUEST_MAGIC 0x32584750U
#define DV_GFX_ACK_MAGIC 0x32584741U
#define DV_GFX_ACK_MODE 1U
#define DV_GFX_ACK_FRESH 2U
#define DV_GFX_ACK_GRAPHICS 4U

/* Private driver/shim receipt; legacy/vendor callback layouts are unchanged. */
struct dv_graphics_request {
	u64 epoch;
	u32 generation;
	bool enable;
	bool retry;
	bool tracked;
};

void amdv_graphics_capability(bool available);
void amdv_graphics_request(bool eligible, struct dv_graphics_request *request);
void amdv_graphics_result(const struct dv_graphics_request *request,
			 bool mode, bool fresh, int flags);

#endif
