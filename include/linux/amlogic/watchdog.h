/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _AMLOGIC_WATCHDOG_TRACE_H
#define _AMLOGIC_WATCHDOG_TRACE_H

#ifdef CONFIG_AMLOGIC_WDT_MESON
void aml_wdt_reboot_trace(const char *stage);
#else
static inline void aml_wdt_reboot_trace(const char *stage) { }
#endif

#endif
