/* SPDX-License-Identifier: GPL-2.0 */
#ifndef AML_AUDIO_DIAGNOSTICS_H
#define AML_AUDIO_DIAGNOSTICS_H
#include <linux/kconfig.h>
#include <linux/types.h>

/* Scalar observations only. Never pass pointers, DMA addresses or media data. */
enum aml_audio_diag_kind {
	AAD_PRIMARY = 1, AAD_THREAD, AAD_POINTER, AAD_THREAD_EXIT, AAD_FRDDR_STATUS,
	AAD_PREPARE = 10, AAD_TRIGGER, AAD_CLOSE, AAD_FREE,
	AAD_ACR = 20, AAD_ACR_POLICY, AAD_AVMUTE, AAD_AMUTE,
	AAD_FIFO_RESET, AAD_AUDIO_RESET, AAD_HDMI_IRQ, AAD_MODE, AAD_PHY,
	AAD_HDCP, AAD_AUDIO_SETUP, AAD_AUDIO_DONE
};
#if IS_REACHABLE(CONFIG_AMLOGIC_HDMITX)
bool aml_audio_diag_enabled(void);
void aml_audio_diag_record(unsigned int kind, unsigned int id, u32 a, u32 b, u32 c);
ssize_t aml_audio_diag_show(char *buf);
ssize_t aml_audio_diag_events_show(char *buf);
int aml_audio_diag_control(const char *buf);
ssize_t aml_audio_diag_hw_show(char *buf);
#else
static inline bool aml_audio_diag_enabled(void) { return false; }
static inline void aml_audio_diag_record(unsigned int k, unsigned int id,
	u32 a, u32 b, u32 c) { }
#endif
#endif
