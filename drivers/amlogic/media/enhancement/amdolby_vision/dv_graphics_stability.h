/* SPDX-License-Identifier: GPL-2.0 */
/* Optional graphics-forward policy. CP/presenter calls own the existing
 * producer/presenter transaction. The parameter callback changes intent only. */
#include "dv_graphics_policy.h"

static u64 dv_backend_generation(void);
static bool dv_graphics_stable;
static bool dv_graphics_stable_available;
static u32 dv_graphics_stable_generation;
static DEFINE_SPINLOCK(dv_graphics_stable_lock);
static struct {
	bool engaged;
	bool eligible; /* Route eligibility captured by the most recent shim CP. */
	bool candidate_valid, candidate_fresh;
	struct dv_graphics_request candidate;
	bool pending_valid, pending_fresh;
	struct dv_graphics_request pending;
	bool applied_valid;
	struct dv_graphics_request applied;
} dv_gfx_stable;

static bool dv_graphics_stability_busy(void)
{
	return (READ_ONCE(dv_graphics_stable) &&
		READ_ONCE(dv_graphics_stable_available)) || READ_ONCE(dv_gfx_stable.engaged);
}

static int dv_graphics_stable_set(const char *value, const struct kernel_param *kp)
{
	bool enabled;
	unsigned long flags;
	int ret = kstrtobool(value, &enabled);

	if (ret)
		return ret;
	if (enabled) {
		bool busy;

		spin_lock_irqsave(&dv_graphics_probe_lock, flags);
		busy = READ_ONCE(dv_graphics_probe) || dv_gfx_probe.restore_pending ||
			dv_gfx_probe.inflight;
		spin_unlock_irqrestore(&dv_graphics_probe_lock, flags);
		if (busy)
			return -EBUSY;
	}
	spin_lock_irqsave(&dv_graphics_stable_lock, flags);
	if (dv_graphics_stable != enabled) {
		dv_graphics_stable_generation++;
		WRITE_ONCE(dv_graphics_stable, enabled);
	}
	if (enabled && dv_graphics_stable_available)
		WRITE_ONCE(dv_gfx_stable.engaged, true);
	spin_unlock_irqrestore(&dv_graphics_stable_lock, flags);
	return 0;
}

static const struct kernel_param_ops dv_graphics_stable_ops = {
	.set = dv_graphics_stable_set, .get = param_get_bool,
};
module_param_cb(dv_graphics_stable, &dv_graphics_stable_ops, &dv_graphics_stable, 0664);
module_param(dv_graphics_stable_available, bool, 0444);
MODULE_PARM_DESC(dv_graphics_stable, "Optional SDR graphics reference for display-led dovi5; default off preserves native graphics mapping");
MODULE_PARM_DESC(dv_graphics_stable_available, "Registered dovi5 supports the graphics reference protocol");

/* Registration/retirement owns callback exclusion. This advertises an option;
 * it never permits a different backend or changes video processing. */
void amdv_graphics_capability(bool available)
{
	unsigned long flags;

	spin_lock_irqsave(&dv_graphics_stable_lock, flags);
	WRITE_ONCE(dv_graphics_stable_available, available);
	dv_gfx_stable.candidate_valid = false;
	dv_gfx_stable.pending_valid = false;
	dv_gfx_stable.applied_valid = false;
	WRITE_ONCE(dv_gfx_stable.engaged,
		dv_graphics_stable && dv_graphics_stable_available);
	spin_unlock_irqrestore(&dv_graphics_stable_lock, flags);
}
EXPORT_SYMBOL(amdv_graphics_capability);

void amdv_graphics_request(bool eligible, struct dv_graphics_request *request)
{
	unsigned long flags;
	u64 epoch = dv_backend_generation();

	spin_lock_irqsave(&dv_graphics_stable_lock, flags);
	dv_gfx_stable.eligible = eligible;
	request->epoch = epoch;
	request->generation = dv_graphics_stable_generation;
	request->enable = eligible && dv_graphics_stable_available && dv_graphics_stable;
	request->tracked = dv_gfx_stable.engaged && dv_graphics_stable_available;
	request->retry = request->tracked &&
		(!dv_gfx_stable.applied_valid ||
		 request->epoch != dv_gfx_stable.applied.epoch ||
		 request->generation != dv_gfx_stable.applied.generation ||
		 request->enable != dv_gfx_stable.applied.enable);
	spin_unlock_irqrestore(&dv_graphics_stable_lock, flags);
}
EXPORT_SYMBOL(amdv_graphics_request);

/* Called after successful CP bounds validation/copyout for this exact request.
 * Current CONST retains priority even when graphics concatenation completed. */
void amdv_graphics_result(const struct dv_graphics_request *request,
			 bool mode, bool fresh, int flags)
{
	dv_gfx_stable.candidate = *request;
	dv_gfx_stable.candidate_valid = request->tracked && mode == request->enable;
	dv_gfx_stable.candidate_fresh = fresh && !(flags & CP_FLAG_CONST_TC2);
}
EXPORT_SYMBOL(amdv_graphics_result);

static void dv_graphics_stability_begin(void)
{
	dv_gfx_stable.candidate_valid = false;
}

static void dv_graphics_stability_cp(bool newer, bool accepted)
{
	dv_gfx_stable.pending_valid = newer && accepted && dv_gfx_stable.candidate_valid;
	dv_gfx_stable.pending_fresh = dv_gfx_stable.pending_valid &&
		dv_gfx_stable.candidate_fresh;
	if (dv_gfx_stable.pending_valid)
		dv_gfx_stable.pending = dv_gfx_stable.candidate;
}

static bool dv_graphics_stability_fresh(void)
{
	return dv_gfx_stable.pending_valid && dv_gfx_stable.pending_fresh &&
		dv_gfx_stable.pending.epoch == dv_backend_generation() &&
		dv_pending_new && !READ_ONCE(dv_new_runtime_failed);
}

/* Receipt follows the Core2 port loop, never cache publication. A newer intent
 * may race an admitted transfer; the next frame regenerates its own request. */
static void dv_graphics_stability_requested(void)
{
	unsigned long flags;

	if (!dv_graphics_stability_fresh())
		return;
	spin_lock_irqsave(&dv_graphics_stable_lock, flags);
	dv_gfx_stable.applied = dv_gfx_stable.pending;
	dv_gfx_stable.applied_valid = true;
	if (!dv_graphics_stable && !dv_gfx_stable.applied.enable &&
	    dv_graphics_stable_generation == dv_gfx_stable.applied.generation)
		WRITE_ONCE(dv_gfx_stable.engaged, false);
	spin_unlock_irqrestore(&dv_graphics_stable_lock, flags);
	dv_gfx_stable.pending_fresh = false;
}

static void dv_graphics_stability_disabled(void)
{
	unsigned long flags;

	spin_lock_irqsave(&dv_graphics_stable_lock, flags);
	dv_gfx_stable.candidate_valid = false;
	dv_gfx_stable.pending_valid = false;
	dv_gfx_stable.applied_valid = false;
	WRITE_ONCE(dv_gfx_stable.engaged,
		dv_graphics_stable && dv_graphics_stable_available);
	spin_unlock_irqrestore(&dv_graphics_stable_lock, flags);
}

/* A paused frame needs a fresh CP, not a force of the previously cached LUT.
 * Natural route changes already reparse. Defer masked/hidden graphics work. */
static bool dv_graphics_stability_changed(void)
{
	struct dv_graphics_request request;

	if (!READ_ONCE(dv_gfx_stable.engaged) || !dv_graphics_stable_available ||
	    !dolby_vision_on || is_osd_off || !(dolby_vision_mask & 2) ||
	    (dolby_vision_flags & FLAG_DISABE_CORE_SETTING) || !dv_pending_new ||
	    !xbmc_dv_source_native || xbmc_dv_vp || graphic_pq_active() ||
	    dovi_setting.src_format != FORMAT_DOVI ||
	    dovi_setting.dst_format != FORMAT_DOVI || dovi_setting.use_ll_flag ||
	    dovi_setting.g_format != G_SDR_RGB || dovi_setting.g_bitdepth != 8)
		return false;
	/* Use the shim's admitted route, including video/graphics priority.
	 * A later CP updates eligibility together with its request. */
	amdv_graphics_request(READ_ONCE(dv_gfx_stable.eligible), &request);
	return request.retry;
}
