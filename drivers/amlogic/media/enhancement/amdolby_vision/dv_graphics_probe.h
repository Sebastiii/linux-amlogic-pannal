/* SPDX-License-Identifier: GPL-2.0 */
/* Temporary CPU-data diagnostic. Included after the common driver globals. */
#include <linux/jiffies.h>

#define DV_GFX_CVM_WORDS 1024
#define DV_GFX_LUT_WORDS 1280
#define DV_GFX_PROBE_MS 8000

static bool dv_graphics_probe_backend(u64 *epoch, bool newer);
static bool dv_graphics_stability_busy(void);

static unsigned int dv_graphics_probe;
static DEFINE_SPINLOCK(dv_graphics_probe_lock);
struct dv_graphics_probe_key {
	u64 epoch;
	u32 config[22];
	u32 dm2[24];
	u32 dm3[26];
	u32 g2l[256];
	u32 vsvdb_len;
	u8 vsvdb[32];
};
static struct {
	unsigned long expires;
	u64 token, cp, pending_cp, application;
	u32 pending_frame;
	u32 held, restored, baseline_hash;
	bool restore_pending, inflight;
	const char *reason;
	struct dv_graphics_probe_key key, candidate;
	u32 baseline[DV_GFX_CVM_WORDS];
	u32 upload[DV_GFX_LUT_WORDS];
} dv_gfx_probe;

/* Byte order is explicit; host analysis uses the same little-endian u32 bytes.
 * Fingerprints are diagnostic only. Eligibility compares all key bytes. */
static u32 dv_graphics_probe_hash(const u32 *words, unsigned int count)
{
	u32 hash = 2166136261U;
	unsigned int i, byte;

	for (i = 0; i < count; i++)
		for (byte = 0; byte < 4; byte++) {
			hash ^= (words[i] >> (byte * 8)) & 0xff;
			hash *= 16777619U;
		}
	return hash;
}

static bool dv_graphics_probe_live(void)
{
	return READ_ONCE(dv_graphics_probe) &&
		time_before(jiffies, READ_ONCE(dv_gfx_probe.expires));
}

/* Allow a bounded closing trace while normal restoration is requested. */
static bool dv_graphics_probe_tracing(void)
{
	return dv_graphics_probe_live() ||
		(dv_gfx_probe.restore_pending &&
		 time_before(jiffies, dv_gfx_probe.expires + msecs_to_jiffies(2000)));
}

static bool dv_graphics_probe_key(struct dv_graphics_probe_key *key,
				 const struct dovi_setting_s *setting,
				 const u32 *dm2, const u32 *lut)
{
	u64 epoch;
	const struct vinfo_s *vinfo = get_current_vinfo();

	if (!is_meson_g12b_cpu() || !dv_graphics_probe_backend(&epoch, true) ||
	    !dolby_vision_on || !dolby_vision_core1_on ||
	    !xbmc_dv_source_native || xbmc_dv_vp || graphic_pq_active() ||
	    is_osd_off || !osd_graphic_width || !osd_graphic_height ||
	    !dolby_vision_graphic_max || dolby_vision_graphics_priority ||
	    (dolby_vision_flags & (FLAG_USE_SINK_MIN_MAX | FLAG_PRIORITY_GRAPHIC)) ||
	    setting->vsvdb_len > sizeof(key->vsvdb) ||
	    dolby_vision_mode != DOLBY_VISION_OUTPUT_MODE_IPT_TUNNEL ||
	    dolby_vision_target_mode != DOLBY_VISION_OUTPUT_MODE_IPT_TUNNEL ||
	    dolby_vision_ll_policy || !vinfo ||
	    setting->src_format != FORMAT_DOVI || setting->dst_format != FORMAT_DOVI ||
	    setting->dovi_ll_enable || setting->use_ll_flag ||
	    setting->diagnostic_enable || setting->diagnostic_mux_select ||
	    setting->g_format != G_SDR_RGB || setting->g_bitdepth != 8 ||
	    (dolby_vision_mask & 7) != 7 || force_update_reg ||
	    (dolby_vision_flags & 0x0400f840) ||
	    dolby_vision_core2_on_cnt < DV_CORE2_RECONFIG_CNT)
		return false;
	memset(key, 0, sizeof(*key));
	key->epoch = epoch;
	key->config[0] = dolby_vision_graphic_min;
	key->config[1] = dolby_vision_graphic_max;
	key->config[2] = dolby_vision_flags & ~FLAG_TOGGLE_FRAME;
	key->config[3] = dolby_vision_mask;
	key->config[4] = dolby_vision_graphics_priority;
	key->config[5] = osd_graphic_width;
	key->config[6] = osd_graphic_height;
	key->config[7] = vinfo->width;
	key->config[8] = vinfo->height;
	key->config[9] = vinfo->sync_duration_num;
	key->config[10] = vinfo->sync_duration_den;
	key->config[11] = dolby_vision_policy;
	key->config[12] = dolby_vision_target_min;
	key->config[13] = dolby_vision_target_max[FORMAT_DOVI][FORMAT_DOVI];
	key->config[14] = dv_new_signal_range;
	key->config[15] = dv_new_blob_enable;
	key->config[16] = setting->video_width;
	key->config[17] = setting->video_height;
	key->config[18] = dv_graphic_blend_test;
	key->config[19] = dv_HDR10_graphics_max;
	key->config[20] = setting->diagnostic_enable;
	key->config[21] = setting->diagnostic_mux_select;
	memcpy(key->dm2, dm2, sizeof(key->dm2));
	memcpy(key->dm3, &setting->dm_reg3, sizeof(key->dm3));
	memcpy(key->g2l, lut + DV_GFX_CVM_WORDS, sizeof(key->g2l));
	key->vsvdb_len = setting->vsvdb_len;
	memcpy(key->vsvdb, setting->vsvdb_tbl, setting->vsvdb_len);
	return true;
}

static int dv_graphics_probe_set(const char *value, const struct kernel_param *kp)
{
	unsigned int mode, i;
	unsigned long flags;
	int ret;

	BUILD_BUG_ON(sizeof(struct dm_lut_ipcore) != DV_GFX_LUT_WORDS * sizeof(u32));
	BUILD_BUG_ON(offsetof(struct dm_lut_ipcore, g_2_l) != DV_GFX_CVM_WORDS * sizeof(u32));
	ret = kstrtouint(value, 0, &mode);
	if (ret || mode > 2)
		return -EINVAL;
	/* Cancellation cannot wait behind a stalled producer. No buffer mutation. */
	if (!mode) {
		WRITE_ONCE(dv_graphics_probe, 0);
		return 0;
	}
	if (atomic_cmpxchg(&dv_transaction_busy, 0, 1))
		return -EBUSY;
	if (dv_graphics_stability_busy() || READ_ONCE(dv_graphics_probe) || dv_gfx_probe.restore_pending ||
	    dv_gfx_probe.inflight) {
		ret = -EBUSY;
		goto done;
	}
	if (mode == 2 &&
	    (force_set_lut || force_reset_core2 || stb_core2_const_flag ||
	     !dv_graphics_probe_key(&dv_gfx_probe.key, &dovi_setting,
		(u32 *)&dovi_setting.dm_reg2, (u32 *)&dovi_setting.dm_lut2))) {
		ret = -EINVAL;
		goto done;
	}
	if (mode == 2)
		memcpy(dv_gfx_probe.baseline, &dovi_setting.dm_lut2,
		       sizeof(dv_gfx_probe.baseline));
	spin_lock_irqsave(&dv_graphics_probe_lock, flags);
	dv_gfx_probe.token++;
	dv_gfx_probe.pending_cp = 0;
	dv_gfx_probe.pending_frame = 0;
	dv_gfx_probe.held = dv_gfx_probe.restored = 0;
	dv_gfx_probe.expires = jiffies + msecs_to_jiffies(DV_GFX_PROBE_MS);
	dv_gfx_probe.baseline_hash = mode == 2 ?
		dv_graphics_probe_hash(dv_gfx_probe.baseline, DV_GFX_CVM_WORDS) : 0;
	dv_gfx_probe.reason = "armed";
	WRITE_ONCE(dv_graphics_probe, mode);
	spin_unlock_irqrestore(&dv_graphics_probe_lock, flags);
	pr_info("DVG: arm token=%llu mode=%u baseline=%08x lease_ms=%u\n",
		(unsigned long long)dv_gfx_probe.token, mode,
		dv_gfx_probe.baseline_hash, DV_GFX_PROBE_MS);
	if (mode == 2)
		for (i = 0; i < DV_GFX_CVM_WORDS; i += 4)
			pr_info("DVG: baseline token=%llu i=%u data=%08x %08x %08x %08x\n",
				(unsigned long long)dv_gfx_probe.token, i,
				dv_gfx_probe.baseline[i], dv_gfx_probe.baseline[i + 1],
				dv_gfx_probe.baseline[i + 2], dv_gfx_probe.baseline[i + 3]);
	ret = 0;
done:
	dv_finish_transaction();
	return ret;
}

static int dv_graphics_probe_get(char *buffer, const struct kernel_param *kp)
{
	return scnprintf(buffer, PAGE_SIZE, "%u\n", dv_graphics_probe_live() ?
		READ_ONCE(dv_graphics_probe) : 0);
}

static int dv_graphics_probe_status(char *buffer, const struct kernel_param *kp)
{
	unsigned long flags;
	int result;

	spin_lock_irqsave(&dv_graphics_probe_lock, flags);
	result = scnprintf(buffer, PAGE_SIZE,
		"version=1 token=%llu mode=%u requested=%u restore_pending=%u inflight=%u held=%u restored=%u baseline=%08x reason=%s\n",
		(unsigned long long)dv_gfx_probe.token,
		dv_graphics_probe_live() ? READ_ONCE(dv_graphics_probe) : 0,
		READ_ONCE(dv_graphics_probe), dv_gfx_probe.restore_pending,
		dv_gfx_probe.inflight, dv_gfx_probe.held, dv_gfx_probe.restored,
		dv_gfx_probe.baseline_hash, dv_gfx_probe.reason ?: "idle");
	spin_unlock_irqrestore(&dv_graphics_probe_lock, flags);
	return result;
}

static const struct kernel_param_ops dv_graphics_probe_ops = {
	.set = dv_graphics_probe_set, .get = dv_graphics_probe_get,
};
static const struct kernel_param_ops dv_graphics_probe_status_ops = {
	.get = dv_graphics_probe_status,
};
module_param_cb(dv_graphics_probe, &dv_graphics_probe_ops, NULL, 0600);
module_param_cb(dv_graphics_probe_status, &dv_graphics_probe_status_ops, NULL, 0400);
MODULE_PARM_DESC(dv_graphics_probe, "Temporary eight-second probe: 0 off(default), 1 CPU trace, 2 trace plus guarded SDR Core2 CVM hold");
MODULE_PARM_DESC(dv_graphics_probe_status, "Probe v1 request status; restoration is a programming request, not a hardware receipt");

static void dv_graphics_probe_stop(const char *reason)
{
	unsigned long flags;

	if (READ_ONCE(dv_graphics_probe))
		pr_info("DVG: stop token=%llu reason=%s\n",
			(unsigned long long)dv_gfx_probe.token, reason);

	WRITE_ONCE(dv_graphics_probe, 0);
	spin_lock_irqsave(&dv_graphics_probe_lock, flags);
	dv_gfx_probe.reason = reason;
	spin_unlock_irqrestore(&dv_graphics_probe_lock, flags);
}

/* Caller owns the producer/presenter transaction. No new hardware reads. */
static u32 *dv_graphics_probe_select(u32 *normal, const u32 *dm2,
				    u32 current_flags, bool reset, bool *upload,
				    bool *held, bool *restoring)
{
	struct dv_graphics_probe_key *key = &dv_gfx_probe.candidate;
	bool active;
	unsigned long flags;

	*held = *restoring = false;
	if (!READ_ONCE(dv_graphics_probe) && !READ_ONCE(dv_gfx_probe.restore_pending) &&
	    !READ_ONCE(dv_gfx_probe.inflight))
		return normal;
	active = dv_graphics_probe_live() && READ_ONCE(dv_graphics_probe) == 2;
	if (active && (reset || force_set_lut || force_update_reg || !dv_pending_new ||
	    (current_flags & (CP_FLAG_CHANGE_TC2 | CP_FLAG_CONST_TC2)) ||
	    !dv_graphics_probe_key(key, &new_dovi_setting, dm2, normal) ||
	    memcmp(key, &dv_gfx_probe.key, sizeof(*key)))) {
		dv_graphics_probe_stop("invalidated");
		active = false;
	}
	/* A retained pre-arm candidate has no matching trace. Wait for a fresh
	 * successful CP result before admitting the first held request. */
	if (active && !dv_gfx_probe.pending_cp)
		return normal;
	/* Reserve before the final cancellation fence. Cleanup must not see an
	 * idle state while an already admitted held request can still finish. */
	if (active) {
		*upload |= !dv_gfx_probe.restore_pending;
		spin_lock_irqsave(&dv_graphics_probe_lock, flags);
		dv_gfx_probe.inflight = *upload;
		spin_unlock_irqrestore(&dv_graphics_probe_lock, flags);
	}
	active = active && dv_graphics_probe_live() && READ_ONCE(dv_graphics_probe) == 2;
	if (!active) {
		spin_lock_irqsave(&dv_graphics_probe_lock, flags);
		dv_gfx_probe.inflight = false;
		spin_unlock_irqrestore(&dv_graphics_probe_lock, flags);
		if (READ_ONCE(dv_graphics_probe) == 2)
			dv_graphics_probe_stop("expired");
		if (dv_gfx_probe.restore_pending) {
			*upload = true;
			*restoring = true;
		}
		return normal;
	}
	memcpy(dv_gfx_probe.upload, dv_gfx_probe.baseline, sizeof(dv_gfx_probe.baseline));
	memcpy(dv_gfx_probe.upload + DV_GFX_CVM_WORDS, normal + DV_GFX_CVM_WORDS,
	       256 * sizeof(u32));
	*held = true;
	return dv_gfx_probe.upload;
}

static void dv_graphics_probe_requested(bool held, bool restoring)
{
	unsigned long flags;

	if (!held && !restoring)
		return;

	spin_lock_irqsave(&dv_graphics_probe_lock, flags);
	dv_gfx_probe.inflight = false;
	if (held) {
		dv_gfx_probe.restore_pending = true;
		dv_gfx_probe.held++;
	}
	if (restoring) {
		dv_gfx_probe.restore_pending = false;
		dv_gfx_probe.restored++;
		dv_gfx_probe.reason = "normal_requested";
	}
	spin_unlock_irqrestore(&dv_graphics_probe_lock, flags);
}

static void dv_graphics_probe_disabled(void)
{
	unsigned long flags;

	WRITE_ONCE(dv_graphics_probe, 0);
	spin_lock_irqsave(&dv_graphics_probe_lock, flags);
	dv_gfx_probe.restore_pending = false;
	dv_gfx_probe.inflight = false;
	dv_gfx_probe.reason = "hardware_disabled";
	spin_unlock_irqrestore(&dv_graphics_probe_lock, flags);
}

static void dv_graphics_probe_metadata(const char *stage, u64 cp, bool newer,
				       u64 epoch, int cp_flags)
{
	const struct md_reg_ipcore3 *md = &new_dovi_setting.md_reg3;
	unsigned int i;

	if (md->size > 204 || md->size > ARRAY_SIZE(md->raw_metadata)) {
		pr_info("DVG: cp=%llu stage=%s invalid_size=%u\n",
			(unsigned long long)cp, stage, md->size);
		return;
	}
	pr_info("DVG: cp=%llu stage=%s backend=%u epoch=%llu frame=%u flags=%x words=%u fnv=%08x cvm=%08x\n",
		(unsigned long long)cp, stage, newer, (unsigned long long)epoch,
		frame_count, cp_flags, md->size,
		dv_graphics_probe_hash(md->raw_metadata, md->size),
		dv_graphics_probe_hash((u32 *)&new_dovi_setting.dm_lut2, DV_GFX_CVM_WORDS));
	for (i = 0; i < md->size; i += 4) {
		unsigned int j;
		char row[40];
		int pos = 0;

		for (j = i; j < min(i + 4, md->size); j++)
			pos += scnprintf(row + pos, sizeof(row) - pos, "%s%08x",
				j == i ? "" : " ", md->raw_metadata[j]);
		pr_info("DVG: cp=%llu stage=%s i=%u data=%s\n",
			(unsigned long long)cp, stage, i, row);
	}
}

/* These are CPU candidates and issued programming requests, never LUT RAM reads. */
static void dv_graphics_probe_dma(const u32 *normal, const u32 *selected,
				  u32 current_flags, bool reset, bool set_lut,
				  bool force, bool upload, bool held, bool restoring)
{
	u64 epoch;

	if (!dv_graphics_probe_tracing() && !held && !restoring)
		return;
	dv_graphics_probe_backend(&epoch, false);
	pr_info("DVG: dma token=%llu cp=%llu app=%llu epoch=%llu cp_frame=%u frame=%u flags=%x const=%u reset=%u set_lut=%u force=%u upload=%u hold=%u restoring=%u normal=%08x selected=%08x cvm=%08x g2l=%08x\n",
		(unsigned long long)dv_gfx_probe.token,
		(unsigned long long)dv_gfx_probe.pending_cp,
		(unsigned long long)++dv_gfx_probe.application,
		(unsigned long long)epoch, dv_gfx_probe.pending_frame, frame_count,
		current_flags, stb_core2_const_flag,
		reset, set_lut, force, upload, held, restoring,
		dv_graphics_probe_hash(normal, DV_GFX_LUT_WORDS),
		dv_graphics_probe_hash(selected, DV_GFX_LUT_WORDS),
		dv_graphics_probe_hash(selected, DV_GFX_CVM_WORDS),
		dv_graphics_probe_hash(selected + DV_GFX_CVM_WORDS, 256));
}
